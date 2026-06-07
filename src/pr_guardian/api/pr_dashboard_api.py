"""API endpoints for the PR Dashboard feature."""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid_mod

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pr_guardian.persistence import storage

# ---------------------------------------------------------------------------
# Demo data — shown when DB is unavailable (GUARDIAN_DEV_ADMIN=1 mode only)
# ---------------------------------------------------------------------------

_DEMO_PR_ID = "11111111-2222-3333-4444-555555555555"
_DEMO_PR_URL = "https://github.com/demo-org/demo-repo/pull/42"

_DEMO_PRS = [
    {
        "id": _DEMO_PR_ID,
        "platform": "github",
        "pr_id": "42",
        "org": "demo-org",
        "project": "",
        "repo": "demo-repo",
        "title": "feat: add widget support to the dashboard",
        "author": "alice",
        "author_display": "Alice Example",
        "pr_url": _DEMO_PR_URL,
        "source_branch": "feature/widgets",
        "target_branch": "main",
        "is_draft": False,
        "has_conflicts": False,
        "approval_status": "pending",
        "reviewers": ["bob"],
        "assignees": [],
        "comment_count": 3,
        "ci_status": "success",
        "has_guardian_review": False,
        "guardian_review_id": None,
        "guardian_decision": None,
        "pr_created_at": "2026-05-10T09:00:00",
        "pr_updated_at": "2026-05-11T07:00:00",
        "synced_at": "2026-05-11T07:00:00",
    }
]

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
    approval_status: str | None = None,
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
            approval_status=approval_status,
            search=search,
            offset=offset,
            limit=limit,
        )
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    except Exception as exc:
        log.error("list_prs_failed", error=str(exc))
        # In dev-admin mode without DB, surface demo data so the dashboard is testable.
        if os.environ.get("GUARDIAN_DEV_ADMIN") == "1":
            return {"items": _DEMO_PRS, "total": len(_DEMO_PRS), "offset": offset, "limit": limit}
        return {"items": [], "total": 0, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# Filter options (for client-side dropdowns)
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
    # Fall back to demo data in dev-admin no-DB mode
    if not pr and os.environ.get("GUARDIAN_DEV_ADMIN") == "1":
        pr = next((p for p in _DEMO_PRS if p["id"] == pr_uuid), None)
    if not pr:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Self-assign on the platform
    if body.assign_self and email:
        adapter = None
        try:
            user_id = await storage.get_user_identity(email)
            if user_id and pr["platform"] == "github":
                github_handle = user_id.get("github_handle")
                if github_handle:
                    from pr_guardian.platform.factory import create_github_adapter

                    adapter = await create_github_adapter()
                    await adapter.add_pr_reviewer(pr["repo"], pr["pr_id"], github_handle)
                    await adapter.add_pr_assignee(pr["repo"], pr["pr_id"], github_handle)
        except Exception as exc:
            log.warning("start_wizard_assign_failed", error=str(exc))
        finally:
            if adapter is not None:
                await adapter.close()

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

    # Parse the PR URL once — works for both GitHub and ADO URLs.
    from pr_guardian.api.review import (
        _create_adapter_for_resolution,
        _parse_pr_url,
        _run_review_background,
        _resolve_review_profile,
    )
    from pr_guardian.platform.factory import create_adapter

    try:
        stub, platform_name = _parse_pr_url(pr["pr_url"])
    except Exception as exc:
        log.warning("start_wizard_parse_failed", error=str(exc))
        return JSONResponse({"error": "unsupported PR URL"}, status_code=400)

    # Resolve the linked connection so the review uses the right token and
    # profile — the same path the paste-URL flow takes. Without this the
    # wizard falls back to the bare global token, which may not have access
    # to private repos.
    resolved_profile = None
    try:
        resolved_profile = await _resolve_review_profile(stub, platform_name)
    except Exception as exc:
        log.warning("start_wizard_profile_resolve_failed", error=str(exc))

    # Pre-create the review record so we can return the review_id immediately.
    # If DB is unavailable we fall back to a transient UUID so the wizard
    # redirect still lands on a page (wizard shows an error state there instead
    # of silently doing nothing).
    review_db_id = None
    try:
        review_db_id = await storage.create_review_record(stub, comment_mode="none")
    except Exception as exc:
        log.warning("start_wizard_precreate_failed", error=str(exc))
        review_db_id = _uuid_mod.uuid4()

    # Start a new review in the background
    try:
        bg_adapter = (
            await _create_adapter_for_resolution(platform_name, resolved_profile)
            if resolved_profile
            else create_adapter(platform_name)
        )
        base_url = str(request.base_url)
        asyncio.create_task(
            _run_review_background(
                stub,
                bg_adapter,
                "none",
                base_url,
                platform_name=platform_name,
                review_db_id=review_db_id,
                resolved_profile=resolved_profile,
            )
        )
    except Exception as exc:
        log.warning("start_wizard_review_launch_failed", error=str(exc))

    return {
        "mode": "new",
        "review_id": str(review_db_id) if review_db_id else None,
        "pr_url": pr["pr_url"],
    }


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
