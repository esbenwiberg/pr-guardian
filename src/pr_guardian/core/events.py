"""In-process event bus for real-time progress updates (SSE)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator


@dataclass
class ReviewEvent:
    review_id: str
    pr_id: str
    repo: str
    stage: str
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, object] = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self))}\n\n"


class EventBus:
    """Simple broadcast event bus backed by asyncio.Queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[ReviewEvent]] = []

    def publish(self, event: ReviewEvent) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    @contextlib.asynccontextmanager
    async def subscription(self) -> AsyncIterator[asyncio.Queue[ReviewEvent]]:
        """Register a subscriber queue for the duration of the context.

        Lower-level than a bare event iterator: the caller (the SSE layer) runs
        its own receive loop with a heartbeat timeout so idle streams aren't
        reaped by the ingress proxy, and so a disconnected client tears the
        subscription down promptly instead of parking forever on an empty queue.
        """
        q: asyncio.Queue[ReviewEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        try:
            yield q
        finally:
            self._subscribers.remove(q)


# Singleton
event_bus = EventBus()
