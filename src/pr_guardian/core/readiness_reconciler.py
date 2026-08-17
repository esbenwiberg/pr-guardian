from __future__ import annotations

import asyncio
import random
import uuid

import structlog

from pr_guardian.core.readiness import evaluate_candidate, reassert_reviewed_readiness
from pr_guardian.persistence import storage

log = structlog.get_logger()


async def reconcile_readiness_once(*, limit: int = 100) -> int:
    """Re-read recoverable candidates from their live link/Profile/Connection."""
    candidates = await storage.list_recoverable_readiness_candidates(limit=limit)
    count = 0
    for candidate in candidates:
        try:
            # Terminal `reviewed` candidates short-circuit evaluate_candidate, so
            # re-assert their stranded readiness check directly instead.
            if candidate["state"] == "reviewed":
                await reassert_reviewed_readiness(candidate)
            else:
                await evaluate_candidate(candidate_id=uuid.UUID(candidate["id"]))
            count += 1
        except Exception as exc:
            # No `exc_info` here. This fires once per unhealthy candidate on every
            # tick, and a single bad connection makes that every candidate in the
            # batch — up to `limit` of them, every interval. The console renderer
            # expands exc_info into a full traceback with locals, so attaching it
            # turned a recoverable platform error into a sustained render-and-log
            # cost on the replica. repr(exc) carries the status and message, which
            # is what diagnosing a 401/404 from a link actually needs; a genuine
            # unexpected fault still surfaces via error_type.
            log.warning(
                "readiness_reconcile_candidate_failed",
                candidate_id=candidate["id"],
                error=repr(exc),
                error_type=type(exc).__name__,
            )
    return count


async def readiness_reconciler_loop(*, interval_seconds: int = 30) -> None:
    """Small app-local background loop; durable candidates make missed ticks harmless.

    Gated behind a Postgres advisory lock so only the leader replica reconciles;
    followers skip the tick. Durable candidates make a missed tick harmless, and
    a separate lock key from pr-sync lets the two loops lead on different
    replicas.
    """
    from pr_guardian.persistence.leader_lock import READINESS_LOCK_KEY, leader_lock

    while True:
        # Sleep first, and jitter, for the same reason as pr-sync: a restarting
        # replica must not fire background work at boot, and replicas that restart
        # together must not stay in lockstep.
        await asyncio.sleep(interval_seconds * random.uniform(1.0, 1.5))
        try:
            async with leader_lock(READINESS_LOCK_KEY, label="readiness_reconciler") as is_leader:
                if is_leader:
                    await reconcile_readiness_once()
                else:
                    log.debug("readiness_reconciler_skipped_not_leader")
        except Exception as exc:
            log.warning("readiness_reconciler_failed", error=str(exc))
