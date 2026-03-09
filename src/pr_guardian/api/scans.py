"""Scans API: trigger and query recent changes / maintenance scans."""
from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pr_guardian.config.loader import load_repo_config
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.maintenance import run_maintenance_scan
from pr_guardian.core.recent_changes import run_recent_changes_scan
from pr_guardian.persistence import storage
from pr_guardian.platform.factory import create_adapter

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["scans"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class RecentChangesScanRequest(BaseModel):
    repo: str  # "owner/repo"
    platform: str = "github"
    time_window_days: int = 7
    since: str | None = None  # ISO date override


class MaintenanceScanRequest(BaseModel):
    repo: str
    platform: str = "github"
    staleness_months: int = 6
    max_files: int = 50


class ScanResponse(BaseModel):
    status: str
    scan_id: str
    scan_type: str
    repo: str


# ---------------------------------------------------------------------------
# Trigger endpoints
# ---------------------------------------------------------------------------


@router.post("/scan/recent", response_model=ScanResponse)
async def trigger_recent_scan(req: RecentChangesScanRequest):
    """Trigger a recent changes scan. Runs asynchronously."""
    adapter = create_adapter(req.platform)
    config = GuardianConfig()

    async def _run():
        try:
            await run_recent_changes_scan(
                repo=req.repo,
                platform=req.platform,
                adapter=adapter,
                config=config,
                time_window_days=req.time_window_days,
                since=req.since,
            )
        except Exception as e:
            log.error("recent_scan_background_error", repo=req.repo, error=str(e))
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    task = asyncio.create_task(_run())
    # Return immediately — scan runs in background
    return ScanResponse(
        status="started",
        scan_id="",  # ID assigned asynchronously in orchestrator
        scan_type="recent_changes",
        repo=req.repo,
    )


@router.post("/scan/maintenance", response_model=ScanResponse)
async def trigger_maintenance_scan(req: MaintenanceScanRequest):
    """Trigger a maintenance scan. Runs asynchronously."""
    adapter = create_adapter(req.platform)
    config = GuardianConfig()

    async def _run():
        try:
            await run_maintenance_scan(
                repo=req.repo,
                platform=req.platform,
                adapter=adapter,
                config=config,
                staleness_months=req.staleness_months,
                max_files=req.max_files,
            )
        except Exception as e:
            log.error("maintenance_scan_background_error", repo=req.repo, error=str(e))
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    task = asyncio.create_task(_run())
    return ScanResponse(
        status="started",
        scan_id="",
        scan_type="maintenance",
        repo=req.repo,
    )


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------


@router.get("/scans")
async def list_scans(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    scan_type: str | None = Query(None),
):
    """Paginated list of scans with optional filters."""
    return await storage.list_scans(
        limit=limit, offset=offset, repo=repo, scan_type=scan_type,
    )


@router.get("/scans/stats")
async def scan_stats():
    """Aggregate statistics for scans."""
    return await storage.get_scan_stats()


@router.get("/scans/{scan_id}")
async def scan_detail(scan_id: uuid.UUID):
    """Full detail for a single scan."""
    row = await storage.get_scan(scan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scan not found")
    return row
