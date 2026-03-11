"""Backward-compatibility redirects from old API paths to /api/v1/*.

These will be removed after one release cycle. They issue 307 (Temporary
Redirect) so clients preserve the HTTP method and body.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["compat"])


def _redirect(request: Request, new_path: str) -> RedirectResponse:
    """Build a 307 redirect preserving query string."""
    url = new_path
    if request.url.query:
        url = f"{new_path}?{request.url.query}"
    return RedirectResponse(url=url, status_code=307)


# --- Review ---

@router.post("/api/review")
async def compat_review(request: Request):
    return _redirect(request, "/api/v1/review")


# --- Dashboard ---

@router.get("/api/dashboard/stats")
async def compat_stats(request: Request):
    return _redirect(request, "/api/v1/dashboard/stats")


@router.get("/api/dashboard/reviews")
async def compat_reviews(request: Request):
    return _redirect(request, "/api/v1/reviews")


@router.get("/api/dashboard/reviews/{review_id}")
async def compat_review_detail(review_id: str, request: Request):
    return _redirect(request, f"/api/v1/reviews/{review_id}")


@router.get("/api/dashboard/active")
async def compat_active(request: Request):
    return _redirect(request, "/api/v1/active")


@router.delete("/api/dashboard/reviews/{review_id}")
async def compat_cancel_review(review_id: str, request: Request):
    return _redirect(request, f"/api/v1/reviews/{review_id}")


@router.get("/api/dashboard/events")
async def compat_events(request: Request):
    return _redirect(request, "/api/v1/events")


@router.get("/api/dashboard/scans")
async def compat_scans(request: Request):
    return _redirect(request, "/api/v1/scans")


@router.get("/api/dashboard/scans/{scan_id}")
async def compat_scan_detail(scan_id: str, request: Request):
    return _redirect(request, f"/api/v1/scans/{scan_id}")


@router.get("/api/dashboard/scan-stats")
async def compat_scan_stats(request: Request):
    return _redirect(request, "/api/v1/scans/stats")


@router.get("/api/dashboard/prompts")
async def compat_prompts(request: Request):
    return _redirect(request, "/api/v1/prompts")


@router.put("/api/dashboard/prompts/{agent_name}")
async def compat_update_prompt(agent_name: str, request: Request):
    return _redirect(request, f"/api/v1/prompts/{agent_name}")


@router.delete("/api/dashboard/prompts/{agent_name}")
async def compat_delete_prompt(agent_name: str, request: Request):
    return _redirect(request, f"/api/v1/prompts/{agent_name}")


@router.get("/api/dashboard/settings")
async def compat_get_settings(request: Request):
    return _redirect(request, "/api/v1/settings")


@router.put("/api/dashboard/settings")
async def compat_put_settings(request: Request):
    return _redirect(request, "/api/v1/settings")


# --- Scans (old /api/scan/* and /api/scans/*) ---

@router.post("/api/scan/recent")
async def compat_scan_recent(request: Request):
    return _redirect(request, "/api/v1/scan/recent")


@router.post("/api/scan/maintenance")
async def compat_scan_maintenance(request: Request):
    return _redirect(request, "/api/v1/scan/maintenance")


@router.get("/api/scans")
async def compat_list_scans(request: Request):
    return _redirect(request, "/api/v1/scans")


@router.get("/api/scans/stats")
async def compat_list_scan_stats(request: Request):
    return _redirect(request, "/api/v1/scans/stats")


@router.get("/api/scans/{scan_id}")
async def compat_scan_detail_by_id(scan_id: str, request: Request):
    return _redirect(request, f"/api/v1/scans/{scan_id}")
