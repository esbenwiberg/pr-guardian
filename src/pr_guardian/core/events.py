"""In-process event bus for real-time progress updates (SSE)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Coroutine
from typing import Any
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
    """Broadcast event bus backed by an asyncio.Queue per subscriber.

    The bus is in-process. To reach SSE subscribers on *other* replicas, a
    cross-replica bridge (see ``core.event_bridge``) registers a remote
    publisher via :meth:`set_remote_publisher`; every published event is then
    also re-broadcast (e.g. through Postgres NOTIFY) and re-injected into the
    local bus on the other replicas via :meth:`fanout_local`. When no bridge is
    registered (single process, sqlite/no-DB, tests) the bus behaves exactly as
    a plain in-process broadcaster.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[ReviewEvent]] = []
        self._remote_publisher: Callable[[ReviewEvent], Coroutine[Any, Any, None]] | None = None
        self._remote_tasks: set[asyncio.Task[None]] = set()

    def fanout_local(self, event: ReviewEvent) -> None:
        """Deliver an event to this process's subscribers only (no re-broadcast)."""
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    def set_remote_publisher(
        self, publisher: Callable[[ReviewEvent], Coroutine[Any, Any, None]]
    ) -> None:
        self._remote_publisher = publisher

    def clear_remote_publisher(self) -> None:
        self._remote_publisher = None

    def publish(self, event: ReviewEvent) -> None:
        self.fanout_local(event)
        publisher = self._remote_publisher
        if publisher is None:
            return
        # Re-broadcast to other replicas. publish() is sync and called from
        # within running async tasks; schedule the send and keep a strong ref so
        # the task isn't GC'd mid-flight. No running loop → local-only delivery.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(publisher(event))
        self._remote_tasks.add(task)
        task.add_done_callback(self._remote_tasks.discard)

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
