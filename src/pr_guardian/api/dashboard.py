"""Dashboard API: stats, review list, review detail, active reviews, and SSE stream."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from pr_guardian.core.events import event_bus
from pr_guardian.persistence import storage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats():
    """Aggregate statistics for the dashboard overview."""
    return await storage.get_stats()


@router.get("/reviews")
async def dashboard_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    decision: str | None = Query(None),
):
    """Paginated list of reviews with optional filters."""
    return await storage.list_reviews(limit=limit, offset=offset, repo=repo, decision=decision)


@router.get("/reviews/{review_id}")
async def dashboard_review_detail(review_id: uuid.UUID):
    """Full detail for a single review."""
    row = await storage.get_review(review_id)
    if not row:
        return {"error": "not found"}
    return row


@router.get("/active")
async def dashboard_active():
    """Currently in-progress reviews."""
    return await storage.get_active_reviews()


@router.get("/events")
async def dashboard_events():
    """SSE stream of real-time review progress events."""

    async def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        async for event in event_bus.subscribe():
            yield event.to_sse()

    return StreamingResponse(generate(), media_type="text/event-stream")
