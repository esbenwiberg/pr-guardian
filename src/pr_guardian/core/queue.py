from __future__ import annotations

import asyncio

import structlog

from pr_guardian.models.pr import PlatformPR

log = structlog.get_logger()


class ReviewQueue:
    """In-process asyncio task manager with dedup and cancellation."""

    def __init__(self):
        self._active: dict[str, asyncio.Task] = {}
        self._seen: set[str] = set()

    def _pr_key(self, pr: PlatformPR) -> str:
        return f"{pr.repo}:{pr.pr_id}"

    def _dedup_key(self, pr: PlatformPR, commit_sha: str) -> str:
        return f"{pr.repo}:{pr.pr_id}:{commit_sha}"

    def is_duplicate(self, pr: PlatformPR, commit_sha: str) -> bool:
        key = self._dedup_key(pr, commit_sha)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    async def enqueue(
        self,
        pr: PlatformPR,
        coro,
    ) -> None:
        """Enqueue a review. Cancels any in-flight review for the same PR."""
        key = self._pr_key(pr)

        if key in self._active and not self._active[key].done():
            log.info("cancelling_stale_review", pr_key=key)
            self._active[key].cancel()

        task = asyncio.create_task(coro)
        self._active[key] = task

        def _cleanup(t: asyncio.Task) -> None:
            if self._active.get(key) is t:
                del self._active[key]

        task.add_done_callback(_cleanup)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._active.values() if not t.done())
