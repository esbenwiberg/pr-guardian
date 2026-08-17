"""Process-wide bound on how many reviews may execute concurrently.

Every review entry point detaches its work with ``asyncio.create_task`` —
webhooks, the readiness reconciler, ChatOps, the dashboard, the nightly scan.
Nothing counted those tasks, so the number of review pipelines running at once
was whatever the trigger happened to produce.

That is what took production down. ``reconcile_readiness_once`` pulls up to 100
recoverable candidates per tick and every one that evaluates as ready detaches a
full ``run_review`` (``readiness.py``), so a single reconcile tick 30-45s after
boot could start a hundred pipelines inside one 4Gi replica: a hundred diffs held
in memory, plus semgrep and gitleaks subprocesses. Working set spiked to ~3.9 GiB
against a 4Gi ceiling, the event loop stalled hard enough that ``/api/health``
missed even a 10s probe timeout, and the platform restarted the replica — which
ran the same tick again.

The bound lives here rather than at the ~15 call sites because every one of them
funnels through ``orchestrator.run_review`` / ``run_re_review``. Callers still
detach freely; the tasks simply queue at the gate instead of all allocating at
once, so peak memory tracks the limit rather than the size of the trigger.

Sizing is deliberately conservative and set by ``GUARDIAN_MAX_CONCURRENT_REVIEWS``.
A backlog therefore drains more slowly than it used to appear to — which is the
point, because the old behaviour did not drain it at all, it killed the replica.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from weakref import WeakKeyDictionary

import structlog

log = structlog.get_logger()

DEFAULT_MAX_CONCURRENT_REVIEWS = 2

# Keyed by event loop: an ``asyncio.Semaphore`` latches onto the first loop that
# awaits it and raises if reused from another, and the test suite runs each case
# in its own loop. WeakKeyDictionary so finished loops do not accumulate.
_gates: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


def max_concurrent_reviews() -> int:
    """Read the limit from the environment, falling back to a safe default.

    Read per call rather than cached at import so the value can be changed with a
    container restart and no rebuild. A non-numeric or non-positive value is
    treated as unset: an unbounded gate is the failure mode this module exists to
    prevent, so it must not be reachable by typo.
    """
    raw = os.environ.get("GUARDIAN_MAX_CONCURRENT_REVIEWS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_REVIEWS
    return value if value > 0 else DEFAULT_MAX_CONCURRENT_REVIEWS


def _gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(max_concurrent_reviews())
        _gates[loop] = gate
    return gate


@asynccontextmanager
async def review_slot(*, label: str, pr_id: str = "", repo: str = "") -> AsyncIterator[None]:
    """Hold a review slot for the duration of the block.

    Logs only when a caller actually has to wait. A queued review is the signal
    worth having — it means the trigger produced more work than the replica can
    run at once — while logging every acquisition would add a line per review for
    no information.
    """
    gate = _gate()
    if gate.locked():
        log.info(
            "review_slot_waiting",
            label=label,
            pr_id=pr_id,
            repo=repo,
            limit=max_concurrent_reviews(),
        )
    async with gate:
        yield
