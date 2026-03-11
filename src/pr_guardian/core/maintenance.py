"""Orchestrator for maintenance scan: identifies stale files needing attention."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

import structlog

from pr_guardian.agents.scan.dead_code import DeadCodeAgent
from pr_guardian.agents.scan.refactor_candidate import RefactorCandidateAgent
from pr_guardian.agents.scan.security_hygiene import SecurityHygieneAgent
from pr_guardian.agents.scan.tech_debt import TechDebtAgent
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import ReviewEvent, event_bus
from pr_guardian.models.scan import ScanContext, ScanResult, ScanType
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()

_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}
_DEFAULT_PRICE = (3.0, 15.0)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    model_lower = model.lower()
    price = _DEFAULT_PRICE
    for prefix, p in _TOKEN_PRICES.items():
        if prefix in model_lower:
            price = p
            break
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _try_import_storage():
    try:
        from pr_guardian.persistence import storage
        return storage
    except Exception:
        return None


MAINTENANCE_AGENTS = {
    "tech_debt": TechDebtAgent,
    "security_hygiene": SecurityHygieneAgent,
    "refactor_candidate": RefactorCandidateAgent,
    "dead_code": DeadCodeAgent,
}

# File extensions worth analyzing
_ANALYZABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".rs",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".php", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".sql", ".yaml", ".yml", ".json", ".toml",
}


def _is_analyzable(path: str) -> bool:
    """Check if a file is worth analyzing (source code, not binary/generated)."""
    for ext in _ANALYZABLE_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


def _matches_patterns(path: str, patterns: list[str]) -> bool:
    """Check if a path matches any glob pattern."""
    for pattern in patterns:
        if fnmatch(path, pattern):
            return True
    return False


def _importance_score(path: str, security_patterns: list[str]) -> float:
    """Score file importance for prioritization (0.0-1.0)."""
    score = 0.5
    # Boost security-related files
    for pattern in security_patterns:
        if fnmatch(path, pattern):
            score = max(score, 0.9)
    # Boost based on file type
    if any(path.endswith(ext) for ext in (".py", ".js", ".ts", ".go", ".java", ".rs")):
        score = max(score, 0.6)
    # Config/infra files are important
    if any(kw in path.lower() for kw in ("config", "settings", "docker", "terraform", "k8s")):
        score = max(score, 0.7)
    return score


async def run_maintenance_scan(
    repo: str,
    platform: str,
    adapter: PlatformAdapter,
    config: GuardianConfig,
    *,
    staleness_months: int | None = None,
    max_files: int | None = None,
    scan_db_id: uuid.UUID | None = None,
) -> ScanResult:
    """Run a maintenance scan: Discovery → Sampling → Analysis → Report."""
    log.info("maintenance_scan_started", repo=repo)

    storage = _try_import_storage()
    months = staleness_months or config.maintenance.staleness_months
    file_limit = max_files or config.maintenance.max_files
    scan_id = str(scan_db_id) if scan_db_id else str(uuid.uuid4())

    # Create DB record if not pre-created by the API layer
    if storage and not scan_db_id:
        try:
            scan_db_id = await storage.create_scan_record(
                scan_type=ScanType.MAINTENANCE.value,
                repo=repo,
                platform=platform,
                staleness_months=months,
            )
            scan_id = str(scan_db_id)
        except Exception as e:
            log.warning("db_scan_create_failed", error=str(e))

    pipeline_log: list[dict] = []
    started_at = datetime.now(timezone.utc)

    def _plog(level: str, stage: str, msg: str):
        pipeline_log.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "stage": stage,
            "msg": msg,
        })

    def _emit(stage: str, detail: str = "", **extra):
        event_bus.publish(ReviewEvent(
            review_id=scan_id,
            pr_id="",
            repo=repo,
            stage=stage,
            detail=detail,
            extra={"scan_type": "maintenance", **extra},
        ))

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and scan_db_id:
            try:
                await storage.update_scan_stage(scan_db_id, stage, detail)
            except Exception as e:
                log.warning("db_scan_stage_update_failed", stage=stage, error=str(e))

    try:
        return await _run_maintenance_pipeline(
            repo, platform, adapter, config, months, file_limit,
            scan_id, storage, scan_db_id, pipeline_log, started_at,
            _plog, _emit, _update_stage,
        )
    except Exception as exc:
        if storage and scan_db_id:
            try:
                await storage.mark_scan_failed(scan_db_id, str(exc), pipeline_log=pipeline_log)
            except Exception as db_err:
                log.warning("db_scan_mark_failed_error", error=str(db_err))
        _emit("scan_error", str(exc))
        raise


async def _run_maintenance_pipeline(
    repo, platform, adapter, config, months, file_limit,
    scan_id, storage, scan_db_id, pipeline_log, started_at,
    _plog, _emit, _update_stage,
) -> ScanResult:
    # Stage 1: Discovery — list repo files and identify stale ones
    await _update_stage("scan_discovery", "Listing repository files and checking staleness")

    all_files = await adapter.list_repo_files(repo)
    _plog("info", "discovery", f"Found {len(all_files)} files in repository.")

    # Filter to analyzable files, apply exclude/include patterns
    exclude = config.maintenance.exclude_patterns
    include = config.maintenance.include_patterns
    candidates = []
    for path in all_files:
        if not _is_analyzable(path):
            continue
        if _matches_patterns(path, exclude):
            continue
        if include and not _matches_patterns(path, include):
            continue
        candidates.append(path)

    _plog("info", "discovery", f"Filtered to {len(candidates)} analyzable files.")

    # Check last commit date for each candidate (batched to respect rate limits)
    staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    cutoff_iso = staleness_cutoff.isoformat()

    # Collect security patterns for importance scoring
    sec_config = config.security_surface
    security_patterns = (
        sec_config.security_critical + sec_config.input_handling +
        sec_config.data_access + sec_config.configuration
    )

    stale_files: list[dict] = []
    sem = asyncio.Semaphore(10)  # limit concurrent API calls

    async def _check_staleness(path: str):
        async with sem:
            try:
                path_commits = await adapter.fetch_commits_for_path(repo, path, per_page=1)
                if not path_commits:
                    return  # No history available

                last_commit_date = path_commits[0].get("commit", {}).get("committer", {}).get("date", "")
                if last_commit_date and last_commit_date < cutoff_iso:
                    importance = _importance_score(path, security_patterns)
                    stale_files.append({
                        "path": path,
                        "last_modified": last_commit_date,
                        "staleness_score": importance,
                    })
            except Exception as e:
                log.debug("staleness_check_failed", path=path, error=str(e))

    # Check staleness in batches
    batch_size = 20
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        await asyncio.gather(*[_check_staleness(p) for p in batch])
        _plog("info", "discovery",
               f"Checked staleness: {min(i + batch_size, len(candidates))}/{len(candidates)} files.")

    # Sort by importance (staleness_score) descending and limit
    stale_files.sort(key=lambda x: x["staleness_score"], reverse=True)
    stale_files = stale_files[:file_limit]

    _plog("info", "discovery",
           f"Found {len(stale_files)} stale files (>{months} months since last change).")

    if not stale_files:
        result = ScanResult(
            scan_id=scan_id,
            scan_type=ScanType.MAINTENANCE,
            repo=repo,
            platform=platform,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            staleness_months=months,
            summary=f"No files found that haven't been modified in {months}+ months.",
            pipeline_log=pipeline_log,
        )
        if storage and scan_db_id:
            try:
                await storage.save_scan_result(scan_db_id, result)
            except Exception as e:
                log.warning("db_scan_save_failed", error=str(e))
        _emit("scan_complete", "No stale files found")
        return result

    # Stage 2: Sampling — fetch file content for top stale files
    await _update_stage("scan_sampling", f"Fetching content for {len(stale_files)} stale files")

    file_contents: dict[str, str] = {}

    async def _fetch_content(sf: dict):
        async with sem:
            try:
                content = await adapter.fetch_file_content(repo, sf["path"])
                file_contents[sf["path"]] = content
                sf["size"] = len(content)
            except Exception as e:
                _plog("warn", "sampling", f"Failed to fetch {sf['path']}: {e}")
                sf["size"] = 0

    await asyncio.gather(*[_fetch_content(sf) for sf in stale_files])
    _plog("info", "sampling", f"Fetched content for {len(file_contents)} files.")

    # Stage 3: Analysis
    await _update_stage("scan_analysis", f"Running {len(MAINTENANCE_AGENTS)} scan agents")

    context = ScanContext(
        scan_id=scan_id,
        scan_type=ScanType.MAINTENANCE,
        repo=repo,
        platform=platform,
        stale_files=stale_files,
        file_contents=file_contents,
        staleness_months=months,
    )

    agent_tasks = []
    for agent_name, agent_cls in MAINTENANCE_AGENTS.items():
        agent = agent_cls(config)
        agent_tasks.append(agent.analyze(context))

    agent_results = await asyncio.gather(*agent_tasks)

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    total_findings = 0

    for ar in agent_results:
        extras = ar.extras or {}
        total_findings += len(ar.findings)
        in_tok = extras.get("input_tokens", 0)
        out_tok = extras.get("output_tokens", 0)
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        if in_tok or out_tok:
            agent_cost = _estimate_cost(extras.get("model", ""), in_tok, out_tok)
            total_cost += agent_cost
        _plog(
            "warn" if ar.verdict.value == "flag_human" else "info",
            "analysis",
            f"Agent {ar.agent_name}: verdict={ar.verdict.value}, "
            f"{len(ar.findings)} finding(s), tokens={in_tok}+{out_tok}",
        )
        if ar.error:
            _plog("error", "analysis", f"Agent {ar.agent_name} error: {ar.error}")

    # Enrich findings with last_modified from stale_files
    stale_lookup = {sf["path"]: sf for sf in stale_files}
    for ar in agent_results:
        for finding in ar.findings:
            sf = stale_lookup.get(finding.file)
            if sf:
                finding.last_modified = sf.get("last_modified")

    # Stage 4: Report
    await _update_stage("scan_report", "Building scan report")

    summaries = [ar.summary for ar in agent_results if ar.summary]
    overall_summary = " | ".join(summaries) if summaries else "Maintenance scan complete."

    _plog("info", "report",
           f"Scan complete: {total_findings} findings across {len(stale_files)} stale files, "
           f"${total_cost:.4f} estimated cost.")

    result = ScanResult(
        scan_id=scan_id,
        scan_type=ScanType.MAINTENANCE,
        repo=repo,
        platform=platform,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        staleness_months=months,
        agent_results=agent_results,
        total_findings=total_findings,
        summary=overall_summary,
        pipeline_log=pipeline_log,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        cost_usd=round(total_cost, 6),
    )

    if storage and scan_db_id:
        try:
            await storage.save_scan_result(scan_db_id, result)
        except Exception as e:
            log.error("db_scan_save_failed", error=str(e))

    _emit("scan_complete", f"{total_findings} findings", cost=total_cost)

    log.info(
        "maintenance_scan_complete",
        repo=repo,
        findings=total_findings,
        stale_files=len(stale_files),
        cost=round(total_cost, 4),
    )
    return result
