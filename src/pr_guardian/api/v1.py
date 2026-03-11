"""Versioned API router: /api/v1/*

Mounts all API endpoints under /api/v1/ with Entra ID auth dependencies.
Health and webhook endpoints remain outside the versioned prefix (no auth).
"""
from __future__ import annotations

import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from pr_guardian.auth.permissions import require_permission
from pr_guardian.persistence import storage

# Re-use existing endpoint logic — import the handler internals
from pr_guardian.api import dashboard as _dash
from pr_guardian.api import review as _review
from pr_guardian.api import scans as _scans

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ---------------------------------------------------------------------------
# Auth config (unauthenticated — needed by MSAL.js before login)
# ---------------------------------------------------------------------------

@router.get("/auth/config")
async def auth_config():
    """Return Entra ID configuration for the dashboard MSAL.js client.

    This endpoint is intentionally unauthenticated — the browser needs the
    client ID and tenant to *start* the login flow.
    """
    from pr_guardian.auth.entra import AUTH_ENABLED, _API_CLIENT_ID, _TENANT_ID

    if not AUTH_ENABLED:
        return {"enabled": False}

    return {
        "enabled": True,
        "client_id": _API_CLIENT_ID,
        "tenant_id": _TENANT_ID,
        "scopes": [
            f"api://{_API_CLIENT_ID}/Dashboard.Read",
            f"api://{_API_CLIENT_ID}/Review.Execute",
            f"api://{_API_CLIENT_ID}/Scan.Execute",
            f"api://{_API_CLIENT_ID}/Settings.Write",
        ],
    }


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


# ---------------------------------------------------------------------------
# Export endpoints — findings as JSON or CSV for CLI / integrations
# ---------------------------------------------------------------------------

@router.get(
    "/reviews/{review_id}/export",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def export_review_findings(
    review_id: uuid.UUID,
    format: str = Query("json", regex="^(json|csv)$"),
):
    """Export review findings as JSON or CSV."""
    row = await storage.get_review(review_id)
    if not row:
        return Response(status_code=404, content="Review not found")

    findings = _extract_review_findings(row)

    if format == "csv":
        return _findings_to_csv(findings, f"review-{review_id}")
    return findings


@router.get(
    "/scans/{scan_id}/export",
    dependencies=[Depends(require_permission("Dashboard.Read"))],
)
async def export_scan_findings(
    scan_id: uuid.UUID,
    format: str = Query("json", regex="^(json|csv)$"),
):
    """Export scan findings as JSON or CSV."""
    row = await storage.get_scan(scan_id)
    if not row:
        return Response(status_code=404, content="Scan not found")

    findings = _extract_scan_findings(row)

    if format == "csv":
        return _findings_to_csv(findings, f"scan-{scan_id}")
    return findings


def _extract_review_findings(row: dict) -> list[dict]:
    """Flatten agent results into a list of finding dicts."""
    findings = []
    agents = row.get("agent_results") or []
    for agent in agents:
        agent_name = agent.get("agent_name", "unknown")
        for f in agent.get("findings", []):
            findings.append({
                "agent": agent_name,
                "severity": f.get("severity", ""),
                "category": f.get("category", ""),
                "file": f.get("file", ""),
                "line": f.get("line", ""),
                "title": f.get("title", ""),
                "description": f.get("description", ""),
            })
    return findings


def _extract_scan_findings(row: dict) -> list[dict]:
    """Flatten scan agent results into a list of finding dicts."""
    findings = []
    agents = row.get("agent_results") or []
    for agent in agents:
        agent_name = agent.get("agent_name", "unknown")
        for f in agent.get("findings", []):
            findings.append({
                "agent": agent_name,
                "severity": f.get("severity", ""),
                "category": f.get("category", ""),
                "file": f.get("file", ""),
                "line": f.get("line", ""),
                "title": f.get("title", ""),
                "description": f.get("description", ""),
            })
    return findings


def _findings_to_csv(findings: list[dict], filename: str) -> Response:
    """Convert findings list to a CSV download response."""
    if not findings:
        cols = ["agent", "severity", "category", "file", "line", "title", "description"]
    else:
        cols = list(findings[0].keys())

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    writer.writerows(findings)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
