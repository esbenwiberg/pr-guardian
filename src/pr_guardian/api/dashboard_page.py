"""Serve the dashboard HTML pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
_DASHBOARD_HTML = _DASHBOARD_DIR / "index.html"
_REVIEW_DETAIL_HTML = _DASHBOARD_DIR / "review_detail.html"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the single-page dashboard."""
    return _DASHBOARD_HTML.read_text()


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
async def review_detail(review_id: str):
    """Serve the standalone review findings page."""
    return _REVIEW_DETAIL_HTML.read_text()
