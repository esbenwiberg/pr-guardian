"""Serve the dashboard HTML pages.

Routing follows the UI redesign sitemap (specs/ui-redesign/design.md):
  /reviews         — queue (new root)
  /reviews/{id}    — review viewer
  /insights        — analytics
  /settings        — admin-only, consolidated
  /help/*          — documentation pages

Legacy paths 302-redirect to their new home.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter(tags=["dashboard"])

# Emerald shield (matches the sidebar wordmark logo) served as the favicon so
# every page stops 404-ing on /favicon.ico.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#10b981">'
    '<path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/>'
    '<path d="M10 15.5l-3.5-3.5 1.41-1.41L10 12.67l5.59-5.59L17 8.5l-7 7z" fill="#0f172a"/>'
    "</svg>"
)

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
_PULL_REQUESTS_HTML = _DASHBOARD_DIR / "pull_requests.html"
_PROFILES_HTML = _DASHBOARD_DIR / "profiles.html"
_INSIGHTS_HTML = _DASHBOARD_DIR / "insights.html"
_REVIEWS_QUEUE_HTML = _DASHBOARD_DIR / "reviews_queue.html"
_LIVE_PROGRESS_HTML = _DASHBOARD_DIR / "live_progress.html"
_REVIEW_DETAIL_HTML = _DASHBOARD_DIR / "review_detail.html"
_SCANS_HTML = _DASHBOARD_DIR / "scans.html"
_SETTINGS_HTML = (
    _DASHBOARD_DIR / "settings.html"
)  # consolidated hub (LLM + Prompts + Admin inlined)
_HOW_IT_WORKS_HTML = _DASHBOARD_DIR / "how_it_works.html"
_HUMAN_REVIEW_HTML = _DASHBOARD_DIR / "human_review.html"
_HUMAN_WIZARD_HTML = _DASHBOARD_DIR / "human_wizard.html"
_CLI_REFERENCE_HTML = _DASHBOARD_DIR / "cli_reference.html"
_API_REFERENCE_HTML = _DASHBOARD_DIR / "api_reference.html"


def _is_admin(request: Request) -> bool:
    identity = getattr(request.state, "identity", None)
    return bool(getattr(identity, "is_admin", False))


def _can_manage_profiles(request: Request) -> bool:
    identity = getattr(request.state, "identity", None)
    return bool(
        getattr(identity, "kind", "anonymous") != "api_key"
        and (
            getattr(identity, "is_admin", False) or getattr(identity, "can_manage_profiles", False)
        )
    )


# ---------------------------------------------------------------------------
# Primary surfaces
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/reviews", status_code=302)


@router.get("/favicon.ico", include_in_schema=False)
@router.get("/favicon.svg", include_in_schema=False)
async def favicon() -> Response:
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_page():
    """Reviews queue — the new root."""
    return _REVIEWS_QUEUE_HTML.read_text()


@router.get("/pull-requests", response_class=HTMLResponse)
async def pull_requests_page():
    """Browse-only open pull requests discovered from sync-enabled Connections."""
    return _PULL_REQUESTS_HTML.read_text()


@router.get("/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request):
    """Profile, Connection, and exact repo-link management for Profile Managers."""
    if not _can_manage_profiles(request):
        return RedirectResponse(url="/reviews?error=profile_manager_required", status_code=302)
    return _PROFILES_HTML.read_text()


@router.get("/reviews/{review_id}/live", response_class=HTMLResponse)
async def review_live(review_id: str):
    """Live pipeline progress stream for a running review."""
    return _LIVE_PROGRESS_HTML.read_text()


_VIEWER_MODES = {
    "wizard": _HUMAN_WIZARD_HTML,
    "chapters": _HUMAN_REVIEW_HTML,
    "findings": _REVIEW_DETAIL_HTML,
}


def _pick_default_mode(review_id: str) -> str:
    """Pick a sensible default mode when ?mode= is absent.

    Heuristic: scans → wizard; otherwise findings. The shared viewer-shell on
    the client can still override based on the user's last-used mode (kept in
    localStorage).
    """
    if review_id.startswith("scan-") or review_id.startswith("demo-scan"):
        return "wizard"
    return "findings"


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
async def review_detail(review_id: str, request: Request):
    """Review viewer — three modes (wizard|chapters|findings) under one URL."""
    mode = (request.query_params.get("mode") or "").lower().strip()
    if mode not in _VIEWER_MODES:
        mode = _pick_default_mode(review_id)
    return _VIEWER_MODES[mode].read_text()


@router.get("/insights", response_class=HTMLResponse)
async def insights_page():
    """Analytics surface. Renamed from /dashboard."""
    return _INSIGHTS_HTML.read_text()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Admin-only settings page. Brief 06 will consolidate /prompts and /admin into anchored sections."""
    if not _is_admin(request):
        return RedirectResponse(url="/reviews?error=admin_required", status_code=302)
    return _SETTINGS_HTML.read_text()


# ---------------------------------------------------------------------------
# Help (footer popover — no top-level nav slot)
# ---------------------------------------------------------------------------


@router.get("/help/how-it-works", response_class=HTMLResponse)
async def help_how_it_works():
    return _HOW_IT_WORKS_HTML.read_text()


@router.get("/help/cli", response_class=HTMLResponse)
async def help_cli():
    return _CLI_REFERENCE_HTML.read_text()


@router.get("/help/api", response_class=HTMLResponse)
async def help_api():
    return _API_REFERENCE_HTML.read_text()


# ---------------------------------------------------------------------------
# Legacy redirects (302 — preserve old bookmarks during the redesign)
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def legacy_dashboard():
    return RedirectResponse(url="/insights", status_code=302)


@router.get("/pr-dashboard")
async def legacy_pr_dashboard():
    return RedirectResponse(url="/pull-requests", status_code=302)


@router.get("/browse-pr")
async def legacy_browse_pr():
    return RedirectResponse(url="/pull-requests", status_code=302)


@router.get("/scans", response_class=HTMLResponse)
async def scans_page():
    """Recent-changes and maintenance scan history."""
    return _SCANS_HTML.read_text()


@router.get("/scans/{scan_id}", response_class=HTMLResponse)
async def scan_detail_page(scan_id: str):
    """Recent-changes and maintenance scan detail."""
    return _SCANS_HTML.read_text()


@router.get("/prompts")
async def legacy_prompts():
    return RedirectResponse(url="/settings#prompts", status_code=302)


@router.get("/admin")
async def legacy_admin():
    return RedirectResponse(url="/settings#api-keys", status_code=302)


@router.get("/how-it-works")
async def legacy_how_it_works():
    return RedirectResponse(url="/help/how-it-works", status_code=302)


@router.get("/cli-reference")
async def legacy_cli_reference():
    return RedirectResponse(url="/help/cli", status_code=302)


@router.get("/api-reference")
async def legacy_api_reference():
    return RedirectResponse(url="/help/api", status_code=302)


@router.get("/reviews/{review_id}/wizard")
async def legacy_wizard(review_id: str):
    return RedirectResponse(url=f"/reviews/{review_id}?mode=wizard", status_code=302)


@router.get("/reviews/{review_id}/human-review")
async def legacy_human_review(review_id: str):
    return RedirectResponse(url=f"/reviews/{review_id}?mode=chapters", status_code=302)


@router.get("/review-mode", response_class=HTMLResponse)
async def review_mode_page():
    """Live PR review mode (browse diff without a stored review). Kept as a power-user entry; not in primary nav."""
    return _HUMAN_REVIEW_HTML.read_text()
