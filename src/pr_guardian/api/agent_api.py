"""Agent API: endpoints for external agents/bots to interact via API key.

Provides a focused surface for automated agents to:
- List and read review findings
- Dismiss findings
- Trigger re-reviews and full reviews

All endpoints require a valid API key via Authorization: Bearer prg_*.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Literal

from pr_guardian.auth.dependencies import require_write_scope
from pr_guardian.auth.identity import Identity
from pr_guardian.persistence import storage

log = structlog.get_logger()

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _require_api_key(request) -> Identity:
    """Require a valid API key (not anonymous, not just Easy Auth)."""
    identity = getattr(request.state, "identity", None)
    if not identity or identity.kind not in ("api_key", "user"):
        raise HTTPException(401, "Authentication required")
    return identity


# ---------------------------------------------------------------------------
# Read reviews / findings
# ---------------------------------------------------------------------------


@router.get("/reviews/{review_id}")
async def get_review(review_id: uuid.UUID, identity: Identity = Depends(_require_api_key)):
    """Get a review with all findings."""
    review = await storage.get_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    return review


@router.get("/reviews")
async def list_reviews(
    repo: str | None = None,
    pr_id: str | None = None,
    limit: int = 20,
    identity: Identity = Depends(_require_api_key),
):
    """List reviews, optionally filtered by repo or PR."""
    reviews = await storage.list_reviews(limit=limit, repo=repo)
    if pr_id:
        reviews = [r for r in reviews if str(r.get("pr_id")) == pr_id]
    return reviews


# ---------------------------------------------------------------------------
# Dismiss findings
# ---------------------------------------------------------------------------


class DismissRequest(BaseModel):
    status: str = "acknowledged"  # false_positive | by_design | acknowledged | will_fix
    comment: str = ""


@router.post("/findings/{finding_id}/dismiss")
async def dismiss_finding(
    finding_id: uuid.UUID,
    body: DismissRequest,
    identity: Identity = Depends(require_write_scope),
):
    """Dismiss a finding."""
    from pr_guardian.api.dashboard import _find_review_for_finding

    review = await _find_review_for_finding(finding_id)
    if not review:
        raise HTTPException(404, "Finding not found")

    finding_dict = review["_matched_finding"]
    agent_name = review["_matched_agent"]

    dismissal_id = await storage.upsert_dismissal(
        pr_id=review["pr_id"],
        repo=review["repo"],
        platform=review["platform"],
        finding=finding_dict,
        agent_name=agent_name,
        status=body.status,
        comment=f"[{identity.display_name}] {body.comment}"
        if body.comment
        else f"[{identity.display_name}]",
    )

    log.info("agent_dismissed_finding", finding_id=str(finding_id), by=identity.display_name)
    return {"status": "dismissed", "dismissal_id": str(dismissal_id)}


# ---------------------------------------------------------------------------
# Trigger reviews
# ---------------------------------------------------------------------------


@router.post("/reviews/{review_id}/re-review")
async def trigger_re_review(
    review_id: uuid.UUID,
    identity: Identity = Depends(require_write_scope),
):
    """Trigger a focused re-review of an existing review."""
    import asyncio
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    review = await storage.get_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    if not review.get("pr_url"):
        raise HTTPException(422, "Review has no PR URL")

    stub, platform_name = _parse_pr_url(review["pr_url"])
    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    async def _run():
        import traceback

        try:
            from pr_guardian.core.orchestrator import run_re_review

            await run_re_review(pr, adapter, original_review=review)
        except Exception as e:
            log.error(
                "agent_re_review_failed",
                pr_id=pr.pr_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )

    asyncio.create_task(_run())

    log.info("agent_re_review_queued", review_id=str(review_id), by=identity.display_name)
    return {"status": "queued", "review_id": str(review_id), "mode": "re_evaluate"}


class FullReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_url: str
    comment_mode: Literal["none", "summary", "inline"] = "summary"


@router.post("/review")
async def trigger_full_review(
    body: FullReviewRequest,
    identity: Identity = Depends(require_write_scope),
):
    """Trigger a full review for a PR URL."""
    import asyncio
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    stub, platform_name = _parse_pr_url(body.pr_url)
    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    # Load dismissals from previous reviews
    dismissals = None
    try:
        dismissals = await storage.get_active_dismissals(pr.pr_id, pr.repo, pr.platform.value)
    except Exception:
        pass

    async def _run():
        import traceback

        try:
            from pr_guardian.core.orchestrator import run_review

            await run_review(pr, adapter, comment_mode=body.comment_mode, dismissals=dismissals)
        except Exception as e:
            log.error(
                "agent_review_failed",
                pr_id=pr.pr_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )

    asyncio.create_task(_run())

    log.info("agent_review_queued", pr_url=body.pr_url, by=identity.display_name)
    return {"status": "queued", "pr_id": pr.pr_id, "repo": pr.repo}
