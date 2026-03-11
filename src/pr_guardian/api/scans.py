"""Scans API: trigger and query recent changes / maintenance scans."""
from __future__ import annotations

import asyncio
import re
import uuid
from urllib.parse import unquote

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pr_guardian.config.loader import apply_global_settings, load_service_defaults
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.maintenance import run_maintenance_scan
from pr_guardian.core.recent_changes import run_recent_changes_scan
from pr_guardian.models.scan import ScanType
from pr_guardian.persistence import storage
from pr_guardian.platform.factory import create_adapter

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["scans"])


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", re.IGNORECASE,
)
_ADO_URL_RE = re.compile(
    r"^(?:https?://)?dev\.azure\.com/[^/]+/([^/]+)/_git/([^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def normalize_repo(repo: str, platform: str) -> str:
    """Normalize user-provided repo string to canonical form.

    GitHub:  ``owner/repo``
    ADO:     ``project/repo``
    """
    repo = unquote(repo).strip().rstrip("/")

    # Strip .git suffix
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Full GitHub URL → owner/repo
    m = _GITHUB_URL_RE.match(repo)
    if m:
        return m.group(1)

    # Full ADO URL → project/repo
    m = _ADO_URL_RE.match(repo)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    # ADO: org/project/repo (3 segments) → project/repo
    if platform == "ado":
        parts = repo.split("/")
        if len(parts) == 3:
            return f"{parts[1]}/{parts[2]}"

    # Validation
    if "://" in repo:
        raise ValueError(
            f"Could not parse repository URL. "
            f"Expected format: {'owner/repo' if platform == 'github' else 'project/repo'}"
        )

    if platform == "github" and "/" not in repo:
        raise ValueError("GitHub repos must be in 'owner/repo' format")

    return repo


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
    try:
        repo = normalize_repo(req.repo, req.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        adapter = create_adapter(req.platform)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = await apply_global_settings(GuardianConfig(**load_service_defaults()))

    # Create DB record upfront so we can return scan_id for progress tracking
    scan_db_id: uuid.UUID | None = None
    try:
        scan_db_id = await storage.create_scan_record(
            scan_type=ScanType.RECENT_CHANGES.value,
            repo=repo,
            platform=req.platform,
            time_window_days=req.time_window_days,
        )
    except Exception as e:
        log.warning("db_scan_create_failed", error=str(e))

    async def _run():
        try:
            await run_recent_changes_scan(
                repo=repo,
                platform=req.platform,
                adapter=adapter,
                config=config,
                time_window_days=req.time_window_days,
                since=req.since,
                scan_db_id=scan_db_id,
            )
        except Exception as e:
            log.error("recent_scan_background_error", repo=repo, error=str(e), exc_info=True)
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.create_task(_run())
    return ScanResponse(
        status="started",
        scan_id=str(scan_db_id) if scan_db_id else "",
        scan_type="recent_changes",
        repo=repo,
    )


@router.post("/scan/maintenance", response_model=ScanResponse)
async def trigger_maintenance_scan(req: MaintenanceScanRequest):
    """Trigger a maintenance scan. Runs asynchronously."""
    try:
        repo = normalize_repo(req.repo, req.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        adapter = create_adapter(req.platform)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = await apply_global_settings(GuardianConfig(**load_service_defaults()))

    # Create DB record upfront so we can return scan_id for progress tracking
    scan_db_id: uuid.UUID | None = None
    try:
        scan_db_id = await storage.create_scan_record(
            scan_type=ScanType.MAINTENANCE.value,
            repo=repo,
            platform=req.platform,
            staleness_months=req.staleness_months,
        )
    except Exception as e:
        log.warning("db_scan_create_failed", error=str(e))

    async def _run():
        try:
            await run_maintenance_scan(
                repo=repo,
                platform=req.platform,
                adapter=adapter,
                config=config,
                staleness_months=req.staleness_months,
                max_files=req.max_files,
                scan_db_id=scan_db_id,
            )
        except Exception as e:
            log.error("maintenance_scan_background_error", repo=repo, error=str(e), exc_info=True)
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.create_task(_run())
    return ScanResponse(
        status="started",
        scan_id=str(scan_db_id) if scan_db_id else "",
        scan_type="maintenance",
        repo=repo,
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
