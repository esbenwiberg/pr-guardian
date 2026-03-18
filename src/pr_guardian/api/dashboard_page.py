"""Serve the dashboard HTML pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
_DASHBOARD_HTML = _DASHBOARD_DIR / "index.html"
_REVIEWS_HTML = _DASHBOARD_DIR / "reviews.html"
_REVIEW_DETAIL_HTML = _DASHBOARD_DIR / "review_detail.html"
_SCANS_HTML = _DASHBOARD_DIR / "scans.html"
_PROMPTS_HTML = _DASHBOARD_DIR / "prompts.html"
_SETTINGS_HTML = _DASHBOARD_DIR / "settings.html"
_HOW_IT_WORKS_HTML = _DASHBOARD_DIR / "how_it_works.html"
_CLI_REFERENCE_HTML = _DASHBOARD_DIR / "cli_reference.html"
_API_REFERENCE_HTML = _DASHBOARD_DIR / "api_reference.html"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard overview page."""
    return _DASHBOARD_HTML.read_text()


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_page():
    """Serve the reviews list page."""
    return _REVIEWS_HTML.read_text()


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
async def review_detail(review_id: str):
    """Serve the standalone review findings page."""
    return _REVIEW_DETAIL_HTML.read_text()


@router.get("/scans", response_class=HTMLResponse)
async def scans_page():
    """Serve the scans list and detail page."""
    return _SCANS_HTML.read_text()


@router.get("/scans/{scan_id}", response_class=HTMLResponse)
async def scan_detail_page(scan_id: str):
    """Serve the scan detail page."""
    return _SCANS_HTML.read_text()


@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page():
    """Serve the prompt editor page."""
    return _PROMPTS_HTML.read_text()


@router.get("/how-it-works", response_class=HTMLResponse)
async def how_it_works_page():
    """Serve the how-it-works explainer page."""
    return _HOW_IT_WORKS_HTML.read_text()


@router.get("/cli-reference", response_class=HTMLResponse)
async def cli_reference_page():
    """Serve the CLI reference documentation page."""
    return _CLI_REFERENCE_HTML.read_text()


@router.get("/api-reference", response_class=HTMLResponse)
async def api_reference_page():
    """Serve the API reference documentation page."""
    return _API_REFERENCE_HTML.read_text()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page():
    """Serve the LLM provider settings page."""
    return _SETTINGS_HTML.read_text()
