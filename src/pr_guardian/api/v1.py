"""Versioned API router: /api/v1/*

Mounts all API endpoints under /api/v1/ with Entra ID auth dependencies.
Health and webhook endpoints remain outside the versioned prefix (no auth).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from pr_guardian.auth.permissions import require_permission

# Re-use existing endpoint logic — import the handler internals
from pr_guardian.api import dashboard as _dash
from pr_guardian.api import review as _review
from pr_guardian.api import scans as _scans

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ---------------------------------------------------------------------------
# Review endpoints — require Review.Execute
# ---------------------------------------------------------------------------

@router.post(
    "/review",
    response_model=_review.ReviewResponse,
    dependencies=[Depends(require_permission("Review.Execute"))],
)
async def trigger_review(req: _review.ReviewRequest, request: Request):
    """Trigger a review for a PR by URL."""
    return await _review.manual_review(req, request)


@router.delete(
    "/reviews/{review_id}",
    dependencies=[Depends(require_permission("Review.Execute"))],
)
async def cancel_review(review_id: uuid.UUID):
    """Cancel/dismiss a stuck review."""
    return await _dash.dashboard_cancel_review(review_id)


# ---------------------------------------------------------------------------
# Scan endpoints — require Scan.Execute to trigger, Dashboard.Read to query
# ---------------------------------------------------------------------------

@router.post(
    "/scan/recent",
    response_model=_scans.ScanResponse,
    dependencies=[Depends(require_permission("Scan.Execute"))],
)
async def trigger_recent_scan(req: _scans.RecentChangesScanRequest):
    """Trigger a recent changes scan."""
    return await _scans.trigger_recent_scan(req)


@router.post(
    "/scan/maintenance",
    response_model=_scans.ScanResponse,
    dependencies=[Depends(require_permission("Scan.Execute"))],
)
async def trigger_maintenance_scan(req: _scans.MaintenanceScanRequest):
    """Trigger a maintenance scan."""
    return await _scans.trigger_maintenance_scan(req)


# ---------------------------------------------------------------------------
# Dashboard / read endpoints — require Dashboard.Read
# ---------------------------------------------------------------------------

@router.get(
    "/stats",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def dashboard_stats():
    return await _dash.dashboard_stats()


@router.get(
    "/reviews",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def list_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    decision: str | None = Query(None),
):
    return await _dash.dashboard_reviews(limit=limit, offset=offset, repo=repo, decision=decision)


@router.get(
    "/reviews/{review_id}",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def review_detail(review_id: uuid.UUID):
    return await _dash.dashboard_review_detail(review_id)


@router.get(
    "/active",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def active_reviews():
    return await _dash.dashboard_active()


@router.get(
    "/events",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def events():
    return await _dash.dashboard_events()


@router.get(
    "/scans",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def list_scans(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    scan_type: str | None = Query(None),
):
    return await _dash.dashboard_scans(limit=limit, offset=offset, repo=repo, scan_type=scan_type)


@router.get(
    "/scans/stats",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def scan_stats():
    return await _dash.dashboard_scan_stats()


@router.get(
    "/scans/{scan_id}",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def scan_detail(scan_id: uuid.UUID):
    return await _dash.dashboard_scan_detail(scan_id)


# ---------------------------------------------------------------------------
# Prompt endpoints — read requires Dashboard.Read, write requires Settings.Write
# ---------------------------------------------------------------------------

@router.get(
    "/prompts",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def list_prompts():
    return await _dash.list_prompts()


@router.put(
    "/prompts/{agent_name}",
    dependencies=[Depends(require_permission("Settings.Write"))],
)
async def update_prompt(agent_name: str, body: _dash.PromptUpdate):
    return await _dash.update_prompt(agent_name, body)


@router.delete(
    "/prompts/{agent_name}",
    dependencies=[Depends(require_permission("Settings.Write"))],
)
async def reset_prompt(agent_name: str):
    return await _dash.reset_prompt(agent_name)


# ---------------------------------------------------------------------------
# Settings endpoints — read requires Dashboard.Read, write requires Settings.Write
# ---------------------------------------------------------------------------

@router.get(
    "/settings",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def get_settings():
    return await _dash.get_settings()


@router.put(
    "/settings",
    dependencies=[Depends(require_permission("Settings.Write"))],
)
async def update_settings(body: _dash.SettingsUpdate):
    return await _dash.update_settings(body)
