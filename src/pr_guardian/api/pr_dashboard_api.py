"""API endpoints for the PR Dashboard feature."""
from __future__ import annotations

import asyncio
import os

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
# Dashboard summary (cards)
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
            "ready": {"total": 0},
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
    project: str | None = None,
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
            project=project,
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
# Filter options (for dynamic dropdowns)
# ---------------------------------------------------------------------------


@router.get("/api/prs/filter-options")
async def get_filter_options():
    try:
        return await storage.get_pr_filter_options()
    except Exception as exc:
        log.error("filter_options_failed", error=str(exc))
        return {"platforms": [], "orgs": [], "projects": [], "repos": []}


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
# Wizard review: start + self-assign
# ---------------------------------------------------------------------------


class StartWizardRequest(BaseModel):
    assign_self: bool = True


@router.post("/api/prs/{pr_uuid}/start-wizard")
async def start_wizard_review(pr_uuid: str, body: StartWizardRequest, request: Request):
    """Check for / start a guardian review and optionally self-assign as reviewer."""
    identity = request.state.identity
    email = identity.email or ""

    try:
        pr = await storage.get_synced_pr(pr_uuid)
    except Exception:
        pr = None
    if not pr:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Self-assign on the platform
    if body.assign_self and email:
        try:
            user_id = await storage.get_user_identity(email)
            if user_id and pr["platform"] == "github":
                github_handle = user_id.get("github_handle")
                if github_handle:
                    from pr_guardian.platform.github import GitHubAdapter
                    token = os.environ.get("GITHUB_TOKEN", "")
                    adapter = GitHubAdapter(token=token)
                    try:
                        await adapter.add_pr_reviewer(pr["repo"], pr["pr_id"], github_handle)
                        await adapter.add_pr_assignee(pr["repo"], pr["pr_id"], github_handle)
                    finally:
                        await adapter.close()
        except Exception as exc:
            log.warning("start_wizard_assign_failed", error=str(exc))

    # Check for an existing completed review
    try:
        existing = await storage.find_review_by_pr_url(pr["pr_url"])
    except Exception:
        existing = None

    if existing:
        return {
            "mode": "existing",
            "review_id": str(existing["id"]),
            "pr_url": pr["pr_url"],
        }

    # Start a new review in the background
    try:
        from pr_guardian.api.review import _parse_pr_url, _run_review_background
        from pr_guardian.platform.factory import create_adapter

        stub, platform_name = _parse_pr_url(pr["pr_url"])
        adapter = create_adapter(platform_name)
        base_url = str(request.base_url)
        asyncio.create_task(
            _run_review_background(stub, adapter, "none", base_url, platform_name=platform_name)
        )
    except Exception as exc:
        log.warning("start_wizard_review_launch_failed", error=str(exc))

    return {"mode": "new", "pr_url": pr["pr_url"]}


# ---------------------------------------------------------------------------
# Repo exclusion
# ---------------------------------------------------------------------------


class ExcludeRepoRequest(BaseModel):
    platform: str
    org: str
    project: str = ""
    repo: str


@router.post("/api/prs/exclude-repo")
async def exclude_repo(body: ExcludeRepoRequest, request: Request):
    """Exclude a repo from the PR dashboard (admin-side filter)."""
    identity = request.state.identity
    email = identity.email or ""
    added = await storage.add_excluded_repo(
        platform=body.platform,
        org=body.org,
        project=body.project,
        repo=body.repo,
        email=email,
    )
    return {"ok": True, "added": added}


@router.delete("/api/prs/exclude-repo/{exclusion_id}")
async def unexclude_repo(exclusion_id: str):
    """Remove a repo exclusion."""
    removed = await storage.remove_excluded_repo(exclusion_id)
    if not removed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Manual sync trigger
# ---------------------------------------------------------------------------


@router.post("/api/prs/sync")
async def trigger_sync():
    from pr_guardian.core.pr_sync import run_pr_sync

    asyncio.create_task(run_pr_sync())
    return {"ok": True, "message": "sync started"}
