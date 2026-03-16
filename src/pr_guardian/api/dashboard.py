"""Dashboard API: stats, review list, review detail, active reviews, and SSE stream."""
from __future__ import annotations

import asyncio
import os
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pr_guardian.agents.base import AGENT_OUTPUT_SCHEMA
from pr_guardian.agents.prompt_composer import CROSS_LANGUAGE_SECTION
from pr_guardian.core.events import event_bus
from pr_guardian.persistence import storage
from pr_guardian.persistence.storage import finding_signature

log = structlog.get_logger()

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
    """Full detail for a single review, enriched with dismissal data."""
    row = await storage.get_review(review_id)
    if not row:
        return {"error": "not found"}

    # Enrich findings with dismissal info
    try:
        dismissals = await storage.get_active_dismissals(
            row["pr_id"], row["repo"], row["platform"],
        )
        sig_map = {d["signature"]: d for d in dismissals}

        dismissal_count = 0
        for agent in row.get("agent_results", []):
            for f in agent.get("findings", []):
                sig = finding_signature(
                    f.get("file", ""), f.get("category", ""), agent["agent_name"],
                )
                match = sig_map.get(sig)
                f["dismissal"] = match
                if match:
                    dismissal_count += 1

        row["dismissal_count"] = dismissal_count
    except Exception:
        row["dismissal_count"] = 0

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


# ---------------------------------------------------------------------------
# Finding dismissals (feedback loop)
# ---------------------------------------------------------------------------

_VALID_DISMISSAL_STATUSES = {"by_design", "false_positive", "acknowledged", "will_fix"}


class DismissRequest(BaseModel):
    status: str
    comment: str = ""


@router.post("/findings/{finding_id}/dismiss")
async def dismiss_finding(finding_id: uuid.UUID, body: DismissRequest):
    """Dismiss a finding with a status and optional comment."""
    if body.status not in _VALID_DISMISSAL_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {_VALID_DISMISSAL_STATUSES}")

    # Look up the finding to get file/category/agent_name + PR context
    review = await _find_review_for_finding(finding_id)
    if not review:
        raise HTTPException(404, "Finding not found")

    finding_dict, agent_name = review["_matched_finding"], review["_matched_agent"]

    dismissal_id = await storage.upsert_dismissal(
        pr_id=review["pr_id"],
        repo=review["repo"],
        platform=review["platform"],
        finding=finding_dict,
        agent_name=agent_name,
        status=body.status,
        comment=body.comment,
    )
    return {
        "id": str(dismissal_id),
        "signature": finding_signature(finding_dict["file"], finding_dict["category"], agent_name),
    }


@router.delete("/dismissals/{dismissal_id}")
async def undismiss_finding(dismissal_id: uuid.UUID):
    """Remove a dismissal (un-dismiss)."""
    deleted = await storage.remove_dismissal(dismissal_id)
    if not deleted:
        raise HTTPException(404, "Dismissal not found")
    return {"status": "removed"}


@router.post("/reviews/{review_id}/re-review")
async def re_review(review_id: uuid.UUID, request: Request):
    """Trigger a re-review of the same PR, with dismissal context injected."""
    review = await storage.get_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    if not review.get("pr_url"):
        raise HTTPException(422, "Review has no PR URL — cannot re-review")

    # Fetch active dismissals
    dismissals = await storage.get_active_dismissals(
        review["pr_id"], review["repo"], review["platform"],
    )

    # Trigger the review via the same path as manual review
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    stub, platform_name = _parse_pr_url(review["pr_url"])
    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    base_url = str(request.base_url).rstrip("/")

    async def _run_bg():
        import traceback
        try:
            from pr_guardian.core.orchestrator import run_review
            await run_review(pr, adapter, post_comment=True, base_url=base_url, dismissals=dismissals)
        except Exception as e:
            log.error("re_review_failed", pr_id=pr.pr_id, error=str(e), traceback=traceback.format_exc())

    asyncio.create_task(_run_bg())
    return {"status": "queued", "pr_id": review["pr_id"], "dismissal_count": len(dismissals)}


async def _find_review_for_finding(finding_id: uuid.UUID) -> dict | None:
    """Look up a finding by ID and return its review + matched finding dict + agent name."""
    from pr_guardian.persistence.database import async_session as get_session
    from pr_guardian.persistence.models import FindingRow, AgentResultRow, ReviewRow

    async with get_session() as session:
        from sqlalchemy import select as sel
        q = sel(FindingRow).where(FindingRow.id == finding_id)
        f_row = (await session.scalars(q)).first()
        if not f_row:
            return None

        ar_row = await session.get(AgentResultRow, f_row.agent_result_id)
        if not ar_row:
            return None

        r_row = await session.get(ReviewRow, ar_row.review_id)
        if not r_row:
            return None

        return {
            "pr_id": r_row.pr_id,
            "repo": r_row.repo,
            "platform": r_row.platform,
            "_matched_finding": {
                "file": f_row.file,
                "line": f_row.line,
                "category": f_row.category,
                "severity": f_row.severity,
                "certainty": f_row.certainty,
                "description": f_row.description,
            },
            "_matched_agent": ar_row.agent_name,
        }


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


# ---------------------------------------------------------------------------
# Settings (LLM provider)
# ---------------------------------------------------------------------------


def _mask_key(key: str | None) -> str:
    """Return masked version of an API key for display, or empty string."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


@router.get("/settings")
async def get_settings():
    """Current LLM provider settings (API keys are masked)."""
    try:
        config = await storage.get_global_config()
    except Exception:
        config = {}

    active = config.get("llm.active_provider", "anthropic")

    # Anthropic key: DB override > env var
    anthropic_db_key = config.get("llm.anthropic.api_key", "")
    anthropic_env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_key = anthropic_db_key or anthropic_env_key

    # Azure AI Foundry
    azure_db_key = config.get("llm.azure_ai_foundry.api_key", "")
    azure_env_key = os.environ.get("AZURE_AI_FOUNDRY_API_KEY", "")
    azure_key = azure_db_key or azure_env_key
    azure_endpoint = config.get("llm.azure_ai_foundry.endpoint_url", "")

    return {
        "active_provider": active,
        "anthropic": {
            "api_key_masked": _mask_key(anthropic_key),
            "api_key_source": "settings" if anthropic_db_key else ("env" if anthropic_env_key else ""),
        },
        "azure_ai_foundry": {
            "endpoint_url": azure_endpoint,
            "api_key_masked": _mask_key(azure_key),
            "api_key_source": "settings" if azure_db_key else ("env" if azure_env_key else ""),
        },
    }


class SettingsUpdate(BaseModel):
    active_provider: str  # "anthropic" or "azure-ai-foundry"
    anthropic_api_key: str | None = None  # blank = keep existing
    azure_endpoint_url: str | None = None
    azure_api_key: str | None = None  # blank = keep existing


@router.put("/settings")
async def update_settings(body: SettingsUpdate):
    """Update LLM provider settings."""
    if body.active_provider not in ("anthropic", "azure-ai-foundry"):
        return {"status": "error", "detail": "Invalid provider"}

    # Validate: switching to azure requires endpoint + key
    if body.active_provider == "azure-ai-foundry":
        try:
            existing = await storage.get_global_config()
        except Exception:
            existing = {}

        has_endpoint = body.azure_endpoint_url or existing.get("llm.azure_ai_foundry.endpoint_url")
        has_key = (
            body.azure_api_key
            or existing.get("llm.azure_ai_foundry.api_key")
            or os.environ.get("AZURE_AI_FOUNDRY_API_KEY")
        )
        if not has_endpoint:
            return {"status": "error", "detail": "Azure AI Foundry endpoint URL is required"}
        if not has_key:
            return {"status": "error", "detail": "Azure AI Foundry API key is required"}

    await storage.set_global_config("llm.active_provider", body.active_provider)

    if body.anthropic_api_key:
        await storage.set_global_config("llm.anthropic.api_key", body.anthropic_api_key)

    if body.azure_endpoint_url is not None:
        await storage.set_global_config("llm.azure_ai_foundry.endpoint_url", body.azure_endpoint_url)

    if body.azure_api_key:
        await storage.set_global_config("llm.azure_ai_foundry.api_key", body.azure_api_key)

    return {"status": "saved"}
