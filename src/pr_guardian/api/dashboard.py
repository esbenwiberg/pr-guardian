"""Dashboard API: stats, review list, review detail, active reviews, and SSE stream."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pr_guardian.agents.base import AGENT_OUTPUT_SCHEMA
from pr_guardian.agents.prompt_composer import CROSS_LANGUAGE_SECTION
from pr_guardian.core.events import event_bus
from pr_guardian.persistence import storage

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats():
    """Aggregate statistics for the dashboard overview."""
    try:
        return await storage.get_stats()
    except Exception:
        return {"total_reviews": 0, "decisions": {}, "avg_score": 0, "total_cost_usd": 0}


@router.get("/reviews")
async def dashboard_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    decision: str | None = Query(None),
):
    """Paginated list of reviews with optional filters."""
    try:
        return await storage.list_reviews(limit=limit, offset=offset, repo=repo, decision=decision)
    except Exception:
        return []


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
    try:
        return await storage.get_active_reviews()
    except Exception:
        return []


@router.delete("/reviews/{review_id}")
async def dashboard_cancel_review(review_id: uuid.UUID):
    """Cancel/dismiss a stuck review, marking it as errored."""
    await storage.mark_review_failed(review_id, "Cancelled by user")
    return {"status": "cancelled"}


@router.get("/events")
async def dashboard_events():
    """SSE stream of real-time review progress events."""

    async def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        async for event in event_bus.subscribe():
            yield event.to_sse()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Scan dashboard endpoints
# ---------------------------------------------------------------------------


@router.get("/scans")
async def dashboard_scans(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    scan_type: str | None = Query(None),
):
    """Paginated list of scans."""
    try:
        return await storage.list_scans(limit=limit, offset=offset, repo=repo, scan_type=scan_type)
    except Exception:
        return []


@router.get("/scans/{scan_id}")
async def dashboard_scan_detail(scan_id: uuid.UUID):
    """Full detail for a single scan."""
    try:
        row = await storage.get_scan(scan_id)
    except Exception:
        return {"error": "not found"}
    if not row:
        return {"error": "not found"}
    return row


@router.get("/scan-stats")
async def dashboard_scan_stats():
    """Aggregate scan statistics."""
    try:
        return await storage.get_scan_stats()
    except Exception:
        return {"total_scans": 0, "type_counts": {}, "severity_counts": {}, "total_cost_usd": 0, "avg_cost_usd": 0}


# ---------------------------------------------------------------------------
# Prompt management
# ---------------------------------------------------------------------------


class PromptUpdate(BaseModel):
    content: str


@router.get("/prompts")
async def list_prompts():
    """All agent prompts with override status, plus shared system sections."""
    agents = await storage.get_all_prompts()
    return {
        "agents": agents,
        "output_schema": AGENT_OUTPUT_SCHEMA.strip(),
        "cross_language_section": CROSS_LANGUAGE_SECTION.strip(),
    }


@router.put("/prompts/{agent_name}")
async def update_prompt(agent_name: str, body: PromptUpdate):
    """Create or update a prompt override for an agent."""
    await storage.set_prompt_override(agent_name, body.content)
    return {"status": "saved", "agent_name": agent_name}


@router.delete("/prompts/{agent_name}")
async def reset_prompt(agent_name: str):
    """Delete a prompt override, reverting to the file default."""
    deleted = await storage.delete_prompt_override(agent_name)
    return {"status": "reset" if deleted else "no_override", "agent_name": agent_name}
