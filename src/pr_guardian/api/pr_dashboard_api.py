"""API endpoints for the PR Dashboard feature."""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pr_guardian.persistence import storage

log = structlog.get_logger()
router = APIRouter(tags=["pr-dashboard"])


class IdentityUpdate(BaseModel):
    github_handle: str | None = None
    ado_upn: str | None = None


# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------


@router.get("/api/user/identity")
async def get_user_identity(request: Request):
    identity = request.state.identity
    email = identity.email or ""
    if not email:
        return {"email": None, "github_handle": None, "ado_upn": None}
    try:
        data = await storage.get_user_identity(email)
        return data or {"email": email, "github_handle": None, "ado_upn": None}
    except Exception:
        return {"email": email, "github_handle": None, "ado_upn": None}


@router.put("/api/user/identity")
async def update_user_identity(request: Request, body: IdentityUpdate):
    identity = request.state.identity
    email = identity.email or ""
    if not email:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    try:
        await storage.upsert_user_identity(
            email=email,
            github_handle=body.github_handle,
            ado_upn=body.ado_upn,
        )
        return {"ok": True}
    except Exception as exc:
        log.error("identity_update_failed", error=str(exc))
        return JSONResponse({"error": "db unavailable"}, status_code=503)


# ---------------------------------------------------------------------------
# Dashboard summary (4 cards)
# ---------------------------------------------------------------------------


@router.get("/api/prs/summary")
async def get_pr_summary(request: Request):
    identity = request.state.identity
    email = identity.email or ""
    try:
        user_id = await storage.get_user_identity(email) if email else None
        github_handle = user_id.get("github_handle") if user_id else None
        ado_upn = user_id.get("ado_upn") if user_id else None
        summary = await storage.get_pr_dashboard_summary(
            github_handle=github_handle,
            ado_upn=ado_upn,
        )
        return summary
    except Exception:
        return {
            "mine": {"total": 0, "needs_attention": 0},
            "queue": {"total": 0},
            "stale": {"total": 0, "oldest_days": None},
            "all": {"total": 0, "repo_count": 0},
        }


# ---------------------------------------------------------------------------
# PR list
# ---------------------------------------------------------------------------


@router.get("/api/prs")
async def list_prs(
    request: Request,
    view: str | None = None,
    platform: str | None = None,
    org: str | None = None,
    repo: str | None = None,
    author: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
):
    identity = request.state.identity
    email = identity.email or ""
    try:
        user_id = await storage.get_user_identity(email) if email else None
        github_handle = user_id.get("github_handle") if user_id else None
        ado_upn = user_id.get("ado_upn") if user_id else None

        items, total = await storage.list_synced_prs(
            view=view,
            github_handle=github_handle,
            ado_upn=ado_upn,
            platform=platform,
            org=org,
            repo=repo,
            author=author,
            search=search,
            offset=offset,
            limit=limit,
        )
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    except Exception as exc:
        log.error("list_prs_failed", error=str(exc))
        return {"items": [], "total": 0, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# Single PR detail (for side panel)
# ---------------------------------------------------------------------------


@router.get("/api/prs/{pr_uuid}")
async def get_pr(pr_uuid: str):
    try:
        pr = await storage.get_synced_pr(pr_uuid)
        if not pr:
            return JSONResponse({"error": "not found"}, status_code=404)
        return pr
    except Exception as exc:
        log.error("get_pr_failed", pr_uuid=pr_uuid, error=str(exc))
        return JSONResponse({"error": "db unavailable"}, status_code=503)


# ---------------------------------------------------------------------------
# Manual sync trigger
# ---------------------------------------------------------------------------


@router.post("/api/prs/sync")
async def trigger_sync():
    from pr_guardian.core.pr_sync import run_pr_sync

    asyncio.create_task(run_pr_sync())
    return {"ok": True, "message": "sync started"}
