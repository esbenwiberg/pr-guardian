"""Detect and attribute event-loop stalls.

Production kept dying with ``/api/health`` — a handler that returns a static dict
and touches nothing — exceeding a 10s probe timeout, while CPU sat pinned at
exactly one core and the working set climbed past 3 GiB. That shape says one
single-threaded, CPU-bound, allocating operation is holding the loop.

The problem is that no ordinary logging can name it. A blocked loop cannot run the
``log.info`` that would say what it is doing, so the interesting window is exactly
the window with no logs in it. Every attribution attempt from the outside failed
for the same reason.

``faulthandler.dump_traceback_later`` solves it because the timer lives in a C
thread that does not need the GIL-holding coroutine to yield: when it fires it
writes every thread's Python stack to stderr, including the frame that is stuck.
The heartbeat below re-arms that timer on each tick, so it only ever fires when
the loop genuinely failed to run for longer than the threshold — a stall, not a
slow request.

The RSS sampling exists for the same attribution reason: it gives the allocation
ramp leading into the stall, and the last sample before the stack dump is the
strongest hint about which structure is growing.
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys

import structlog

log = structlog.get_logger()

DEFAULT_STALL_THRESHOLD_SECONDS = 15
DEFAULT_HEARTBEAT_SECONDS = 5
# Report the ramp, not every wobble: a steady-state process moves a few MiB
# between ticks and a runaway allocation moves hundreds.
_RSS_REPORT_DELTA_BYTES = 256 * 1024 * 1024


def stall_threshold_seconds() -> int:
    """Seconds the loop may fail to tick before every stack is dumped.

    ``0`` disables the watchdog entirely. Kept configurable because the dump goes
    to stderr and is large; if stalls ever become routine rather than fatal, the
    volume should be a deployment decision and not a rebuild.
    """
    raw = os.environ.get("GUARDIAN_STALL_WATCHDOG_SECONDS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALL_THRESHOLD_SECONDS
    return max(value, 0)


def _rss_bytes() -> int | None:
    """Resident set size from procfs, or None where that is not available.

    Deliberately not psutil: this module has to be safe to import in the hosted
    image and in tests on macOS, and a missing /proc is not an error worth
    raising from a diagnostic.
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE")


async def stall_watchdog_loop(
    *,
    threshold_seconds: int | None = None,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
) -> None:
    """Re-arm the faulthandler timer every tick; report the RSS ramp.

    Each ``dump_traceback_later`` call cancels the pending one, so a loop that
    keeps ticking never dumps. When the loop stops ticking the timer matures in
    its own thread and writes the stacks, which is the only way to see the frame
    responsible for the stall.
    """
    threshold = stall_threshold_seconds() if threshold_seconds is None else threshold_seconds
    if threshold <= 0:
        log.info("stall_watchdog_disabled")
        return

    faulthandler.enable(file=sys.stderr)
    log.info("stall_watchdog_started", threshold_seconds=threshold, heartbeat=heartbeat_seconds)
    last_reported = _rss_bytes()
    try:
        while True:
            faulthandler.dump_traceback_later(threshold, exit=False, file=sys.stderr)
            await asyncio.sleep(heartbeat_seconds)
            rss = _rss_bytes()
            if rss is not None and (
                last_reported is None or abs(rss - last_reported) >= _RSS_REPORT_DELTA_BYTES
            ):
                log.warning("rss_step", rss_mib=rss // (1024 * 1024))
                last_reported = rss
    finally:
        # Leave no armed timer behind on shutdown: it would fire into a
        # half-torn-down process and the dump would be noise.
        faulthandler.cancel_dump_traceback_later()
