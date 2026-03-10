"""Orchestrator for recent changes scan: analyzes merged code changes as a whole."""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import structlog

from pr_guardian.agents.scan.architecture_drift import ArchitectureDriftAgent
from pr_guardian.agents.scan.consistency import ConsistencyAgent
from pr_guardian.agents.scan.integration_risk import IntegrationRiskAgent
from pr_guardian.agents.scan.trend import TrendAgent
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import ReviewEvent, event_bus
from pr_guardian.models.scan import ScanContext, ScanResult, ScanType
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()

# Same pricing table as the main orchestrator
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


RECENT_CHANGES_AGENTS = {
    "trend": TrendAgent,
    "consistency": ConsistencyAgent,
    "integration_risk": IntegrationRiskAgent,
    "architecture_drift": ArchitectureDriftAgent,
}


def _group_changes_by_module(files: list[dict]) -> dict[str, list[dict]]:
    """Group changed files by top-level module/directory."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        path = f.get("filename", f.get("path", ""))
        parts = path.split("/")
        module = parts[0] if len(parts) > 1 else "(root)"
        groups[module].append(f)
    return dict(groups)


def _build_change_summary(merged_prs: list[dict], commits: list[dict], changes_by_module: dict) -> str:
    """Build a human-readable summary of recent changes."""
    lines = []
    lines.append(f"Total merged PRs: {len(merged_prs)}")
    lines.append(f"Total commits: {len(commits)}")
    lines.append(f"Modules touched: {len(changes_by_module)}")

    # Author distribution
    authors: dict[str, int] = defaultdict(int)
    for pr in merged_prs:
        author = pr.get("user", {}).get("login", "unknown") if isinstance(pr.get("user"), dict) else "unknown"
        authors[author] += 1
    if authors:
        lines.append(f"Authors: {', '.join(f'{a} ({c})' for a, c in sorted(authors.items(), key=lambda x: -x[1])[:10])}")

    # Module sizes
    lines.append("\nChanges per module:")
    for module, files in sorted(changes_by_module.items(), key=lambda x: -len(x[1])):
        total_add = sum(f.get("additions", 0) for f in files)
        total_del = sum(f.get("deletions", 0) for f in files)
        lines.append(f"  {module}: {len(files)} files (+{total_add}/-{total_del})")

    return "\n".join(lines)


async def run_recent_changes_scan(
    repo: str,
    platform: str,
    adapter: PlatformAdapter,
    config: GuardianConfig,
    *,
    time_window_days: int | None = None,
    since: str | None = None,
) -> ScanResult:
    """Run a recent changes scan: Discovery → Analysis → Report."""
    log.info("recent_changes_scan_started", repo=repo)

    storage = _try_import_storage()
    days = time_window_days or config.recent_changes.time_window_days
    scan_id = str(uuid.uuid4())
    scan_db_id: uuid.UUID | None = None

    if storage:
        try:
            scan_db_id = await storage.create_scan_record(
                scan_type=ScanType.RECENT_CHANGES.value,
                repo=repo,
                platform=platform,
                time_window_days=days,
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
            extra={"scan_type": "recent_changes", **extra},
        ))

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and scan_db_id:
            try:
                await storage.update_scan_stage(scan_db_id, stage, detail)
            except Exception as e:
                log.warning("db_scan_stage_update_failed", stage=stage, error=str(e))

    try:
        return await _run_recent_pipeline(
            repo, platform, adapter, config, days, since,
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


async def _run_recent_pipeline(
    repo, platform, adapter, config, days, since,
    scan_id, storage, scan_db_id, pipeline_log, started_at,
    _plog, _emit, _update_stage,
) -> ScanResult:
    # Stage 1: Discovery
    await _update_stage("scan_discovery", "Fetching recent merged PRs and commits")

    if since is None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since = since_dt.isoformat()

    branch = config.recent_changes.branch

    merged_prs, commits = await asyncio.gather(
        adapter.fetch_merged_prs(repo, since=since, base=branch),
        adapter.fetch_recent_commits(repo, branch=branch, since=since),
    )

    _plog("info", "discovery", f"Found {len(merged_prs)} merged PRs and {len(commits)} commits in {days} days.")

    if not merged_prs and not commits:
        _plog("info", "discovery", "No recent changes found.")
        result = ScanResult(
            scan_id=scan_id,
            scan_type=ScanType.RECENT_CHANGES,
            repo=repo,
            platform=platform,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            time_window_days=days,
            summary="No recent changes found in the specified time window.",
            pipeline_log=pipeline_log,
        )
        if storage and scan_db_id:
            try:
                await storage.save_scan_result(scan_db_id, result)
            except Exception as e:
                log.warning("db_scan_save_failed", error=str(e))
        _emit("scan_complete", "No changes to analyze")
        return result

    # Collect changed files from merged PRs
    all_changed_files: list[dict] = []
    for pr in merged_prs:
        pr_number = pr.get("number")
        if pr_number:
            try:
                ado_project = pr.get("_ado_project", "")
                ado_repo = pr.get("_ado_repo", "")
                pr_repo = f"{ado_project}/{ado_repo}" if ado_project else repo
                files = await adapter.fetch_pr_files(pr_repo, pr_number, project=ado_project)
                for f in files:
                    f["_pr_number"] = pr_number
                    f["_pr_title"] = pr.get("title", "")
                all_changed_files.extend(files)
            except Exception as e:
                _plog("warn", "discovery", f"Failed to fetch files for PR #{pr_number}: {e}")

    changes_by_module = _group_changes_by_module(all_changed_files)
    change_summary = _build_change_summary(merged_prs, commits, changes_by_module)

    _plog("info", "discovery",
           f"Aggregated {len(all_changed_files)} file changes across {len(changes_by_module)} modules.")

    # Stage 2: Analysis
    await _update_stage("scan_analysis", f"Running {len(RECENT_CHANGES_AGENTS)} scan agents")

    context = ScanContext(
        scan_id=scan_id,
        scan_type=ScanType.RECENT_CHANGES,
        repo=repo,
        platform=platform,
        merged_prs=merged_prs,
        commits=commits,
        changes_by_module=changes_by_module,
        change_summary=change_summary,
        time_window_days=days,
    )

    agent_tasks = []
    for agent_name, agent_cls in RECENT_CHANGES_AGENTS.items():
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

    # Stage 3: Report
    await _update_stage("scan_report", "Building scan report")

    summaries = [ar.summary for ar in agent_results if ar.summary]
    overall_summary = " | ".join(summaries) if summaries else "Scan complete."

    _plog("info", "report",
           f"Scan complete: {total_findings} findings, ${total_cost:.4f} estimated cost.")

    result = ScanResult(
        scan_id=scan_id,
        scan_type=ScanType.RECENT_CHANGES,
        repo=repo,
        platform=platform,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        time_window_days=days,
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
        "recent_changes_scan_complete",
        repo=repo,
        findings=total_findings,
        cost=round(total_cost, 4),
    )
    return result
