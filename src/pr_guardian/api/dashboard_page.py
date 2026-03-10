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
