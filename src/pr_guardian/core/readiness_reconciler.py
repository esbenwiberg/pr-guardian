from __future__ import annotations

import asyncio
import uuid

import structlog

from pr_guardian.core.readiness import evaluate_candidate
from pr_guardian.persistence import storage

log = structlog.get_logger()


async def reconcile_readiness_once(*, limit: int = 100) -> int:
    """Re-read recoverable candidates from their live link/Profile/Connection."""
    candidates = await storage.list_recoverable_readiness_candidates(limit=limit)
    count = 0
    for candidate in candidates:
        try:
            await evaluate_candidate(candidate_id=uuid.UUID(candidate["id"]))
            count += 1
        except Exception as exc:
            log.warning(
                "readiness_reconcile_candidate_failed",
                candidate_id=candidate["id"],
                error=str(exc),
            )
    return count


async def readiness_reconciler_loop(*, interval_seconds: int = 30) -> None:
    """Small app-local background loop; durable candidates make missed ticks harmless."""
    while True:
        try:
            await reconcile_readiness_once()
        except Exception as exc:
            log.warning("readiness_reconciler_failed", error=str(exc))
        await asyncio.sleep(interval_seconds)
