"""The watchdog is a diagnostic, so what matters is that it arms, re-arms, and disarms."""

from __future__ import annotations

import asyncio
import faulthandler
from unittest.mock import patch

import pytest

from pr_guardian.core.stall_watchdog import (
    DEFAULT_STALL_THRESHOLD_SECONDS,
    stall_threshold_seconds,
    stall_watchdog_loop,
)
from pr_guardian.main import _loop_enabled


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30", 30),
        ("0", 0),
        # Negative would mean "dump immediately, forever" if passed through.
        ("-5", 0),
        ("nonsense", DEFAULT_STALL_THRESHOLD_SECONDS),
    ],
)
def test_threshold_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("GUARDIAN_STALL_WATCHDOG_SECONDS", raw)
    assert stall_threshold_seconds() == expected


async def test_zero_threshold_returns_without_arming(monkeypatch):
    monkeypatch.setenv("GUARDIAN_STALL_WATCHDOG_SECONDS", "0")
    with patch.object(faulthandler, "dump_traceback_later") as armed:
        await stall_watchdog_loop()
    armed.assert_not_called()


async def test_loop_rearms_each_heartbeat_and_cancels_on_shutdown(monkeypatch):
    """Re-arming is the whole mechanism: a ticking loop must never dump."""
    monkeypatch.setenv("GUARDIAN_STALL_WATCHDOG_SECONDS", "15")

    with (
        patch.object(faulthandler, "dump_traceback_later") as armed,
        patch.object(faulthandler, "cancel_dump_traceback_later") as cancelled,
        patch.object(faulthandler, "enable"),
    ):
        task = asyncio.create_task(stall_watchdog_loop(heartbeat_seconds=0))
        # heartbeat_seconds=0 still yields to the loop, so a few passes happen.
        for _ in range(6):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert armed.call_count >= 2, "each heartbeat must re-arm the timer"
    assert armed.call_args.args[0] == 15
    cancelled.assert_called_once()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, True),
        ("1", True),
        ("true", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("no", False),
        # A typo must not silently stop the safety net.
        ("mabye", True),
    ],
)
def test_loop_switch_defaults_on(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("GUARDIAN_TEST_LOOP_FLAG", raising=False)
    else:
        monkeypatch.setenv("GUARDIAN_TEST_LOOP_FLAG", raw)
    assert _loop_enabled("GUARDIAN_TEST_LOOP_FLAG") is expected
