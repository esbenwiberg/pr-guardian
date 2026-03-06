"""Serve the dashboard HTML page."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the single-page dashboard."""
    return _DASHBOARD_HTML.read_text()
