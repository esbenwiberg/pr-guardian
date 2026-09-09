"""Guards on the hosted process's log rendering.

Log volume is a billing surface, not just noise. ``ConsoleRenderer``'s default
exception formatter is ``RichTracebackFormatter(show_locals=True,
max_frames=100)``, which renders a single exception as a multi-hundred-line box
quoting every frame's locals. On the readiness reconciler's per-candidate path
that took the production Log Analytics workspace from 0.16 to 19.8 GB/day —
437 GB and ~kr 7.2k in a month, 97.7% of the billed bytes being traceback boxes.
"""

from __future__ import annotations

import logging
import re

import structlog

from pr_guardian.main import _configure_logging


def _renderer() -> structlog.dev.ConsoleRenderer:
    _configure_logging()
    renderers = [
        p
        for p in structlog.get_config()["processors"]
        if isinstance(p, structlog.dev.ConsoleRenderer)
    ]
    assert len(renderers) == 1, "expected exactly one ConsoleRenderer"
    return renderers[0]


def test_exception_formatter_does_not_render_locals():
    """The formatter must not be rich's locals-expanding one.

    Asserted on the configured object rather than on rendered output, so the
    guard holds even if ``rich`` stops being an installed dependency (structlog
    silently falls back to a plain formatter when it is absent, which would make
    an output-only assertion pass for the wrong reason).
    """
    formatter = _renderer()._exception_formatter
    assert formatter is structlog.dev.plain_traceback, (
        "ConsoleRenderer must be built with exception_formatter=plain_traceback; "
        f"got {formatter!r}"
    )
    assert not isinstance(formatter, structlog.dev.RichTracebackFormatter)


def test_rendered_exception_stays_bounded_and_hides_frame_state():
    """End-to-end: a raised exception with fat locals renders small and clean."""
    renderer = _renderer()

    # Locals shaped like the real readiness failure: nested config dicts that
    # rich would expand frame by frame.
    def _boom() -> None:
        snapshot = {"settings": {"quiet_period_seconds": 10}, "patterns": ["p" * 40] * 12}
        candidate = dict(snapshot)
        assert candidate is not None
        raise RuntimeError("Client error '404 Not Found' for url 'https://dev.azure.com/x'")

    try:
        _boom()
    except RuntimeError as exc:
        rendered = renderer(
            None,
            "warning",
            {
                "event": "readiness_platform_error",
                "level": "warning",
                "error": repr(exc),
                "exc_info": exc,
            },
        )

    assert "readiness_platform_error" in rendered
    assert "404 Not Found" in rendered
    # The frame-state values rich would have expanded. Asserted on the values
    # rather than on the word "locals", which legitimately appears in traceback
    # file paths and symbol names.
    assert "quiet_period_seconds" not in rendered
    assert "pppppppppppppppppppppppppppppppppppppppp" not in rendered
    assert "│" not in rendered and "╭" not in rendered
    # ANSI would defeat Log Analytics term tokenisation on a non-tty sink.
    assert not re.search(r"\x1b\[", rendered)
    # A plain traceback for this stack is a handful of lines; the rich version
    # of the same failure in production averaged ~380.
    assert len(rendered.splitlines()) < 25, rendered


def test_log_level_defaults_to_info(monkeypatch):
    """Debug in prod is expensive: stdout writes are synchronous."""
    monkeypatch.delenv("GUARDIAN_LOG_LEVEL", raising=False)
    _configure_logging()
    log = structlog.get_logger()
    assert not log.is_enabled_for(logging.DEBUG)
    assert log.is_enabled_for(logging.INFO)
