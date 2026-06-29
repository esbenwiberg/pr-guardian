"""Scans API: trigger and query recent changes / maintenance scans."""

from __future__ import annotations

import asyncio
import re
import uuid
from urllib.parse import unquote

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pr_guardian.auth.dependencies import require_write
from pr_guardian.auth.identity import Identity
from pr_guardian.config.profile_resolver import (
    ProfileResolutionError,
    ResolvedProfileConfig,
    profile_allows_side_effect,
    resolve_profile_config,
)
from pr_guardian.core.maintenance import run_maintenance_scan
from pr_guardian.core.recent_changes import run_recent_changes_scan
from pr_guardian.models.scan import ScanType
from pr_guardian.persistence import storage
from pr_guardian.platform.factory import create_adapter, create_github_adapter

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["scans"])


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
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
    # Commit-range mode: when base_ref is set, analyze base..head directly
    # instead of enumerating merged PRs over a time window.
    base_ref: str | None = None
    head_ref: str | None = None  # defaults to the configured branch


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


async def _create_adapter_for_resolution(
    platform: str,
    resolved_profile: ResolvedProfileConfig,
):
    snapshot = resolved_profile.connection_snapshot or {}
    # GitHub App connections mint an installation token per-request from the
    # connection's private key — they store NO static token, so
    # get_connection_token() returns "" and the generic path below would build an
    # UNauthenticated client that 404s on private repos. Route them through the
    # App-auth path, mirroring create_adapter_for_review. Legacy token-style
    # GitHub connections (auth_kind != "github_app") still use the static token.
    if (
        platform == "github"
        and snapshot.get("auth_kind") == "github_app"
        and resolved_profile.connection_id
    ):
        return await create_github_adapter(str(resolved_profile.connection_id))
    if resolved_profile.connection_id:
        token = await storage.get_connection_token(resolved_profile.connection_id)
        org_url = ""
        if resolved_profile.connection_snapshot:
            org_url = resolved_profile.connection_snapshot.get("org_url") or ""
        return create_adapter(
            platform,
            token_override=token,
            org_url_override=org_url or None,
        )
    return create_adapter(platform)


# ---------------------------------------------------------------------------
# Trigger endpoints
# ---------------------------------------------------------------------------


@router.post("/scan/recent", response_model=ScanResponse)
async def trigger_recent_scan(
    req: RecentChangesScanRequest,
    identity: Identity = Depends(require_write),
):
    """Trigger a recent changes scan. Runs asynchronously."""
    try:
        repo = normalize_repo(req.repo, req.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        resolved_profile = await resolve_profile_config(
            platform=req.platform,
            repo=repo,
            require_connection=True,
        )
        adapter = await _create_adapter_for_resolution(req.platform, resolved_profile)
    except ProfileResolutionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = resolved_profile.config

    # Create DB record upfront so we can return scan_id for progress tracking
    scan_db_id: uuid.UUID | None = None
    try:
        scan_db_id = await storage.create_scan_record(
            scan_type=ScanType.RECENT_CHANGES.value,
            repo=repo,
            platform=req.platform,
            time_window_days=req.time_window_days,
            base_sha=req.base_ref or "",
            head_sha=req.head_ref or "",
        )
        await storage.set_scan_provenance(
            scan_db_id,
            **resolved_profile.scan_provenance(scan_source="scan"),
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
                base_ref=req.base_ref,
                head_ref=req.head_ref,
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
async def trigger_maintenance_scan(
    req: MaintenanceScanRequest,
    identity: Identity = Depends(require_write),
):
    """Trigger a maintenance scan. Runs asynchronously."""
    try:
        repo = normalize_repo(req.repo, req.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        resolved_profile = await resolve_profile_config(
            platform=req.platform,
            repo=repo,
            require_connection=True,
        )
        adapter = await _create_adapter_for_resolution(req.platform, resolved_profile)
    except ProfileResolutionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    config = resolved_profile.config

    # Create DB record upfront so we can return scan_id for progress tracking
    scan_db_id: uuid.UUID | None = None
    try:
        scan_db_id = await storage.create_scan_record(
            scan_type=ScanType.MAINTENANCE.value,
            repo=repo,
            platform=req.platform,
            staleness_months=req.staleness_months,
        )
        await storage.set_scan_provenance(
            scan_db_id,
            **resolved_profile.scan_provenance(scan_source="scan"),
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
    try:
        return await storage.list_scans(
            limit=limit,
            offset=offset,
            repo=repo,
            scan_type=scan_type,
        )
    except Exception:
        return []


@router.get("/scans/stats")
async def scan_stats():
    """Aggregate statistics for scans."""
    try:
        return await storage.get_scan_stats()
    except Exception:
        return {
            "total": 0,
            "by_type": {"recent_changes": 0, "maintenance": 0},
            "by_severity": {"low": 0, "medium": 0, "high": 0, "critical": 0},
            "total_cost_usd": None,
            "avg_cost_usd": None,
        }


@router.get("/scans/{scan_id}")
async def scan_detail(scan_id: uuid.UUID):
    """Full detail for a single scan."""
    try:
        row = await storage.get_scan(scan_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Database not available")
    if not row:
        raise HTTPException(status_code=404, detail="Scan not found")
    return row


# ---------------------------------------------------------------------------
# Issue creation from scan findings
# ---------------------------------------------------------------------------


class CreateIssuesRequest(BaseModel):
    mode: str  # "single" | "per_finding" | "per_group"
    finding_ids: list[str]  # list of ScanFindingRow UUIDs


class CreatedIssue(BaseModel):
    issue_url: str
    issue_number: str
    title: str
    finding_ids: list[str]


def _compose_issue_body(findings: list[dict], scan: dict) -> str:
    lines = [
        f"**PR Guardian Scan** — {scan['repo']}",
        f"Scan type: {scan['scan_type']}",
        "",
        "## Findings",
        "",
    ]
    for i, f in enumerate(findings, 1):
        loc = f.get("file", "")
        if f.get("line"):
            loc = f"{loc}:{f['line']}"
        sev = (f.get("severity") or "unknown").upper()
        cat = f.get("category") or "Issue"
        lines.append(f"**{i}. [{sev}] {cat}**{' — `' + loc + '`' if loc else ''}")
        if f.get("description"):
            lines.append(f"> {f['description']}")
        if f.get("suggestion"):
            lines.append(f"> Suggestion: {f['suggestion']}")
        lines.append("")
    lines.append("---")
    lines.append("*Created by [PR Guardian](https://github.com/anthropics/pr-guardian)*")
    return "\n".join(lines)


@router.post("/scans/{scan_id}/create-issues")
async def create_scan_issues(scan_id: uuid.UUID, req: CreateIssuesRequest):
    """Create platform issues from selected scan findings.

    Modes:
    - single: one issue summarising all selected findings
    - per_finding: one issue per finding
    - per_group: one issue per agent group among selected findings
    """
    if req.mode not in ("single", "per_finding", "per_group"):
        raise HTTPException(
            status_code=400, detail="mode must be single, per_finding, or per_group"
        )
    if not req.finding_ids:
        raise HTTPException(status_code=400, detail="No finding_ids provided")

    scan = await storage.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    if not profile_allows_side_effect(scan.get("profile_snapshot"), "scan_issues"):
        raise HTTPException(
            status_code=403,
            detail="Scan issue creation is disabled by the resolved Profile.",
        )

    # Build lookup: finding_id → (agent_name, finding dict)
    finding_map: dict[str, tuple[str, dict]] = {}
    for agent in scan.get("agent_results", []):
        for f in agent.get("findings", []):
            if f.get("id"):
                finding_map[f["id"]] = (agent["agent_name"], f)

    selected = [(fid, finding_map[fid]) for fid in req.finding_ids if fid in finding_map]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid findings matched")

    # Create platform adapter
    try:
        adapter = create_adapter(scan["platform"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Platform adapter error: {e}")

    repo = scan["repo"]

    def _issue_title(findings: list[dict], label: str = "") -> str:
        if len(findings) == 1:
            f = findings[0]
            sev = (f.get("severity") or "unknown").upper()
            cat = f.get("category") or "Issue"
            loc = f.get("file") or ""
            return f"[PR Guardian] [{sev}] {cat}" + (f" — {loc}" if loc else "")
        prefix = f"[PR Guardian] {label}" if label else "[PR Guardian]"
        return f"{prefix} — {len(findings)} finding{'s' if len(findings) != 1 else ''} in {repo}"

    # Group findings according to mode
    groups: list[tuple[str, list[str], list[dict]]] = []
    if req.mode == "single":
        all_findings = [f for _, (_, f) in selected]
        title = _issue_title(all_findings)
        groups = [(title, [fid for fid, _ in selected], all_findings)]
    elif req.mode == "per_finding":
        for fid, (agent_name, f) in selected:
            groups.append((_issue_title([f]), [fid], [f]))
    else:  # per_group
        agent_groups: dict[str, tuple[list[str], list[dict]]] = {}
        for fid, (agent_name, f) in selected:
            if agent_name not in agent_groups:
                agent_groups[agent_name] = ([], [])
            agent_groups[agent_name][0].append(fid)
            agent_groups[agent_name][1].append(f)
        for agent_name, (fids, findings) in agent_groups.items():
            groups.append((_issue_title(findings, agent_name), fids, findings))

    created: list[dict] = []
    errors: list[str] = []

    from typing import cast

    from pr_guardian.platform.ado import ADOAdapter
    from pr_guardian.platform.github import GitHubAdapter

    for title, group_finding_ids, findings in groups:
        body = _compose_issue_body(findings, scan)
        try:
            if scan["platform"] == "github":
                gh_adapter = cast(GitHubAdapter, adapter)
                result = await gh_adapter.create_issue(
                    repo=repo,
                    title=title,
                    body=body,
                    labels=["pr-guardian"],
                )
                issue_url = result.get("url", "")
                issue_number = str(result.get("number", ""))
            else:
                ado_adapter = cast(ADOAdapter, adapter)
                # ADO: project/repo format → extract project
                project = repo.split("/")[0] if "/" in repo else repo
                result = await ado_adapter.create_work_item(
                    project=project,
                    title=title,
                    body=body,
                )
                issue_url = result.get("url", "")
                issue_number = str(result.get("id", ""))

            await storage.create_scan_issue(
                scan_id=scan_id,
                finding_ids=group_finding_ids,
                issue_url=issue_url,
                issue_number=issue_number,
                title=title,
                platform=scan["platform"],
                repo=repo,
            )
            created.append(
                {
                    "issue_url": issue_url,
                    "issue_number": issue_number,
                    "title": title,
                    "finding_ids": group_finding_ids,
                }
            )
        except Exception as e:
            log.error("scan_issue_create_failed", error=str(e), title=title)
            errors.append(str(e))

    if hasattr(adapter, "close"):
        await adapter.close()

    if not created and errors:
        raise HTTPException(
            status_code=500,
            detail={"message": "All issue creations failed", "errors": errors},
        )
    return {"created": created, "errors": errors}


@router.get("/scans/{scan_id}/issues")
async def list_scan_issues(scan_id: uuid.UUID):
    """List all issues created for a scan."""
    try:
        return await storage.get_scan_issues(scan_id)
    except Exception:
        return []
