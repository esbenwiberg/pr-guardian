"""SSE heartbeat + subscription lifecycle for the dashboard `/events` stream.

Regression for prod "stream timeout": the ingress (ACA/Envoy) reaps idle
streams, and between reviews the SSE carries no traffic. A heartbeat keeps the
stream alive and lets a gone client tear its subscription down promptly.
"""

from __future__ import annotations

import asyncio

import pr_guardian.api.dashboard as dash
from pr_guardian.core.events import EventBus, ReviewEvent, event_bus


async def test_subscription_delivers_event_then_cleans_up():
    bus = EventBus()
    async with bus.subscription() as q:
        assert len(bus._subscribers) == 1
        bus.publish(ReviewEvent(review_id="r1", pr_id="1", repo="x", stage="start"))
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev.review_id == "r1"
    # Context exit must drop the subscriber even though the queue still drained cleanly.
    assert bus._subscribers == []


async def test_subscription_cleans_up_on_exception():
    bus = EventBus()
    try:
        async with bus.subscription():
            assert len(bus._subscribers) == 1
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert bus._subscribers == []


async def test_dashboard_events_emits_heartbeat_when_idle(monkeypatch):
    monkeypatch.setattr(dash, "_SSE_HEARTBEAT_SECONDS", 0.01)
    resp = await dash.dashboard_events()
    it = resp.body_iterator
    try:
        first = await it.__anext__()
        assert "connected" in first
        # No events published → the next chunk must be the keepalive comment,
        # not an indefinite block (which is what the ingress was reaping).
        second = await asyncio.wait_for(it.__anext__(), timeout=1)
        assert second == ": keepalive\n\n"
    finally:
        await it.aclose()


async def test_dashboard_events_streams_published_event(monkeypatch):
    monkeypatch.setattr(dash, "_SSE_HEARTBEAT_SECONDS", 5)
    resp = await dash.dashboard_events()
    it = resp.body_iterator
    try:
        await it.__anext__()  # "connected" — generator now suspends before subscribing
        pending = asyncio.ensure_future(it.__anext__())
        await asyncio.sleep(0.05)  # let it register the subscription and block on q.get()
        event_bus.publish(ReviewEvent(review_id="r9", pr_id="9", repo="x", stage="agents"))
        chunk = await asyncio.wait_for(pending, timeout=1)
        assert '"review_id": "r9"' in chunk
    finally:
        await it.aclose()
