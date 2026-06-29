"""Deep ("fat nightly") scan: re-review every merged PR at full PR-review depth.

The thin PR gate runs a cheap subset synchronously while a PR is open. This scan
is its fat counterpart — run off-hours, it re-runs the *full* PR-review pipeline
(6 agents → triage → decision engine → verdict) against each PR merged in the
window, using the same machinery the range/repo review uses: ``run_review`` with
a pre-built ``diff_override``, ``persist=False`` (no review rows, no queue
pollution) and platform side effects suppressed (the PRs are already merged —
there is nothing to approve or block).

Each PR's review collapses into one ``ScanAgentResult`` so it slots straight into
the existing scan result/persistence/dashboard plumbing: ``agent_name`` carries
the PR identity, ``verdict`` carries the decision, ``summary`` carries the
decision label + score + a link to the real PR, and ``findings`` carry every
agent finding (tagged with the originating lens in ``category``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import ReviewEvent, event_bus
from pr_guardian.core.orchestrator import run_review
from pr_guardian.core.pr_like_synthesis import synthesize_cross_pr
from pr_guardian.core.range_review import build_range_diff
from pr_guardian.models.findings import AgentResult, Finding, Verdict
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.models.scan import ScanAgentResult, ScanFinding, ScanResult, ScanType
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()

# Decision → the display label and the coarse scan Verdict the existing badge CSS
# understands (pass/warn/flag_human). Reject and hard-block both read as
# flag_human; the precise decision is preserved in the per-PR summary.
_DECISION_LABEL: dict[Decision, str] = {
    Decision.AUTO_APPROVE: "Auto-approve",
    Decision.HUMAN_REVIEW: "Human review",
    Decision.REJECT: "Reject",
    Decision.HARD_BLOCK: "Hard block",
}
_DECISION_VERDICT: dict[Decision, Verdict] = {
    Decision.AUTO_APPROVE: Verdict.PASS,
    Decision.HUMAN_REVIEW: Verdict.WARN,
    Decision.REJECT: Verdict.FLAG_HUMAN,
    Decision.HARD_BLOCK: Verdict.FLAG_HUMAN,
}
_AGENT_LABEL: dict[str, str] = {
    "security_privacy": "Security/Privacy",
    "performance": "Performance",
    "architecture_intent": "Architecture Intent",
    "code_quality_observability": "Code Quality",
    "test_quality": "Test Quality",
    "hotspot": "Hotspot",
}


def _try_import_storage():
    try:
        from pr_guardian.persistence import storage

        return storage
    except Exception:
        return None


def _pr_refs(pr: dict, platform: str) -> dict | None:
    """Extract the identity + base/head SHAs needed to re-review a merged PR.

    Returns ``None`` when the SHAs can't be resolved (so the PR is skipped with a
    log line rather than producing a bogus empty review). GitHub PR objects are
    the common case; ADO uses a different shape, handled best-effort.
    """
    if platform == "github":
        base = pr.get("base") or {}
        head = pr.get("head") or {}
        base_sha = base.get("sha", "")
        head_sha = head.get("sha", "")
        if not (base_sha and head_sha):
            return None
        return {
            "number": str(pr.get("number", "")),
            "title": pr.get("title", ""),
            "author": (pr.get("user") or {}).get("login", ""),
            "source_branch": head.get("ref", ""),
            "target_branch": base.get("ref", ""),
            "base_sha": base_sha,
            "head_sha": head_sha,
        }
    # ADO: lastMergeSourceCommit/lastMergeTargetCommit carry the SHAs.
    src = pr.get("lastMergeSourceCommit") or {}
    tgt = pr.get("lastMergeTargetCommit") or {}
    base_sha = tgt.get("commitId", "")
    head_sha = src.get("commitId", "")
    if not (base_sha and head_sha):
        return None
    return {
        "number": str(pr.get("pullRequestId", "")),
        "title": pr.get("title", ""),
        "author": (pr.get("createdBy") or {}).get("displayName", ""),
        "source_branch": (pr.get("sourceRefName", "") or "").replace("refs/heads/", ""),
        "target_branch": (pr.get("targetRefName", "") or "").replace("refs/heads/", ""),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "_ado_project": pr.get("_ado_project", ""),
    }


def _findings_to_scan(ar: AgentResult) -> list[ScanFinding]:
    """Flatten one PR-review agent's findings into ScanFindings, tagging the lens."""
    label = _AGENT_LABEL.get(ar.agent_name, ar.agent_name)
    out: list[ScanFinding] = []
    for f in list(ar.findings) + list(ar.cross_language_findings):
        if not isinstance(f, Finding):
            continue
        out.append(
            ScanFinding(
                severity=f.severity,
                certainty=f.certainty,
                category=label,
                file=f.file,
                line=f.line,
                description=f.description,
                suggestion=f.suggestion,
                agent_name=ar.agent_name,
            )
        )
    return out


def _review_to_scan_agent(refs: dict, pr_url: str, result: ReviewResult) -> ScanAgentResult:
    """Collapse one PR's full review into a single ScanAgentResult card."""
    decision = result.decision
    label = _DECISION_LABEL.get(decision, decision.value)
    verdict = _DECISION_VERDICT.get(decision, Verdict.WARN)

    findings: list[ScanFinding] = []
    for ar in result.agent_results:
        findings.extend(_findings_to_scan(ar))

    pr_ref = f"[PR #{refs['number']}]({pr_url})" if pr_url else f"PR #{refs['number']}"
    head = f"**{label}** · score {result.combined_score:.2f} · {pr_ref}"
    body = result.summary.strip()
    summary = f"{head}\n\n{body}" if body else head

    return ScanAgentResult(
        agent_name=f"PR #{refs['number']}: {refs['title']}".strip().rstrip(":"),
        verdict=verdict,
        findings=findings,
        summary=summary,
    )


async def run_pr_like_scan(
    repo: str,
    platform: str,
    adapter: PlatformAdapter,
    config: GuardianConfig,
    *,
    time_window_days: int | None = None,
    since: str | None = None,
    scan_db_id: uuid.UUID | None = None,
) -> ScanResult:
    """Re-review each PR merged in the window at full PR-review depth.

    Discovery enumerates merged PRs (newest first). By default every merged PR
    in the window is re-reviewed; ``config.recent_changes.deep_max_prs`` is a
    safety ceiling (newest first, rest logged as skipped), and ``0`` disables it
    entirely. Each PR is re-reviewed concurrently (capped to ``deep_concurrency``)
    via the real PR pipeline with no persistence and no platform side effects.
    Returns a ``ScanResult`` whose ``agent_results`` are one-per-PR.
    """
    log.info("pr_like_scan_started", repo=repo)

    storage = _try_import_storage()
    days = time_window_days or config.recent_changes.time_window_days
    branch = config.recent_changes.branch
    max_prs = config.recent_changes.deep_max_prs
    concurrency = max(1, config.recent_changes.deep_concurrency)
    scan_id = str(scan_db_id) if scan_db_id else str(uuid.uuid4())

    if storage and not scan_db_id:
        try:
            scan_db_id = await storage.create_scan_record(
                scan_type=ScanType.RECENT_CHANGES_DEEP.value,
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
        pipeline_log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "stage": stage,
                "msg": msg,
            }
        )

    def _emit(stage: str, detail: str = "", **extra):
        event_bus.publish(
            ReviewEvent(
                review_id=scan_id,
                pr_id="",
                repo=repo,
                stage=stage,
                detail=detail,
                extra={"scan_type": ScanType.RECENT_CHANGES_DEEP.value, **extra},
            )
        )

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and scan_db_id:
            try:
                await storage.update_scan_stage(scan_db_id, stage, detail)
            except Exception as e:
                log.warning("db_scan_stage_update_failed", stage=stage, error=str(e))

    try:
        return await _run_deep_pipeline(
            repo,
            platform,
            adapter,
            config,
            days,
            branch,
            max_prs,
            concurrency,
            since,
            scan_id,
            storage,
            scan_db_id,
            pipeline_log,
            started_at,
            _plog,
            _emit,
            _update_stage,
        )
    except Exception as exc:
        if storage and scan_db_id:
            try:
                await storage.mark_scan_failed(scan_db_id, str(exc), pipeline_log=pipeline_log)
            except Exception as db_err:
                log.warning("db_scan_mark_failed_error", error=str(db_err))
        _emit("scan_error", str(exc))
        raise


async def _run_deep_pipeline(
    repo,
    platform,
    adapter,
    config,
    days,
    branch,
    max_prs,
    concurrency,
    since,
    scan_id,
    storage,
    scan_db_id,
    pipeline_log,
    started_at,
    _plog,
    _emit,
    _update_stage,
) -> ScanResult:
    # Stage 1: Discovery
    await _update_stage("scan_discovery", "Fetching merged PRs to re-review")
    if since is None:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    merged_prs = await adapter.fetch_merged_prs(repo, since=since, base=branch)
    _plog("info", "discovery", f"Found {len(merged_prs)} merged PR(s) in {days} days.")

    # Resolve refs; drop PRs we can't reconstruct a diff for.
    resolvable: list[dict] = []
    for pr in merged_prs:
        refs = _pr_refs(pr, platform)
        if refs:
            resolvable.append(refs)
        else:
            _plog(
                "warn",
                "discovery",
                f"Skipping PR (could not resolve base/head SHAs): {pr.get('title', '?')}",
            )

    # max_prs <= 0 means "no cap — review every merged PR in the window".
    if max_prs > 0 and len(resolvable) > max_prs:
        _plog(
            "warn",
            "discovery",
            f"Capped at {max_prs} PRs (deep_max_prs); {len(resolvable) - max_prs} older "
            f"PR(s) not reviewed this run.",
        )
        resolvable = resolvable[:max_prs]

    if not resolvable:
        result = _empty_result(scan_id, repo, platform, days, started_at, pipeline_log)
        await _save(storage, scan_db_id, result)
        _emit("scan_complete", "No PRs to review")
        return result

    # Stage 2: Per-PR full review (concurrency-capped)
    await _update_stage("scan_analysis", f"Re-reviewing {len(resolvable)} PR(s) at full depth")
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def _review_one(refs: dict) -> ScanAgentResult:
        nonlocal done
        async with sem:
            number = refs["number"]
            try:
                project = refs.get("_ado_project", "")
                diff, _meta = await build_range_diff(
                    adapter, repo, refs["base_sha"], refs["head_sha"], project=project
                )
                pr = PlatformPR(
                    platform=Platform.GITHUB if platform == "github" else Platform.ADO,
                    pr_id=number,
                    repo=repo,
                    repo_url="",
                    source_branch=refs["source_branch"],
                    target_branch=refs["target_branch"] or branch,
                    author=refs["author"],
                    title=refs["title"],
                    head_commit_sha=refs["head_sha"],
                    project=project,
                )
                result = await run_review(
                    pr,
                    adapter,
                    service_config=config,
                    post_comment=False,
                    diff_override=diff,
                    skip_platform_side_effects=True,
                    persist=False,
                )
                scan_agent = _review_to_scan_agent(refs, pr.pr_url, result)
                scan_agent.extras = {
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.total_input_tokens,
                    "output_tokens": result.total_output_tokens,
                }
                _plog(
                    "info",
                    "analysis",
                    f"PR #{number}: {result.decision.value}, "
                    f"{len(scan_agent.findings)} finding(s).",
                )
                return scan_agent
            except Exception as e:  # one bad PR must not kill the scan
                log.warning("pr_like_review_failed", pr=number, error=str(e))
                _plog("error", "analysis", f"PR #{number} review failed: {e}")
                return ScanAgentResult(
                    agent_name=f"PR #{number}: {refs['title']}".strip().rstrip(":"),
                    verdict=Verdict.WARN,
                    findings=[],
                    summary=f"Review failed: {e}",
                    error=str(e),
                )
            finally:
                done += 1
                _emit("scan_analysis", f"Reviewed {done}/{len(resolvable)} PRs")

    agent_results = await asyncio.gather(*[_review_one(r) for r in resolvable])

    # Stage 3: Report. Stats are computed over the per-PR cards only (the synthesis
    # card carries no findings and is not itself a reviewed PR).
    await _update_stage("scan_report", "Building scan report")
    pr_cards = list(agent_results)
    total_findings = sum(len(ar.findings) for ar in pr_cards)
    total_cost = sum((ar.extras or {}).get("cost_usd", 0.0) for ar in pr_cards)
    total_in = sum((ar.extras or {}).get("input_tokens", 0) for ar in pr_cards)
    total_out = sum((ar.extras or {}).get("output_tokens", 0) for ar in pr_cards)
    blocked = sum(1 for ar in pr_cards if ar.verdict == Verdict.FLAG_HUMAN)
    review_count = len(pr_cards)
    summary = (
        f"Deep re-review of {review_count} merged PR(s): "
        f"{blocked} would need human attention (reject/block at full depth), "
        f"{total_findings} finding(s) total."
    )
    _plog("info", "report", summary)

    # Cross-PR synthesis: one narrative over the per-PR outcomes. Additive — a
    # failure returns None and the deep scan still saves every per-PR result. The
    # card is prepended so it renders first; its token cost rolls into the totals.
    final_results = pr_cards
    synth = await synthesize_cross_pr(pr_cards, repo, config)
    if synth is not None:
        total_cost += (synth.extras or {}).get("cost_usd", 0.0)
        total_in += (synth.extras or {}).get("input_tokens", 0)
        total_out += (synth.extras or {}).get("output_tokens", 0)
        final_results = [synth, *pr_cards]
        _plog("info", "report", "Added cross-PR synthesis.")

    result = ScanResult(
        scan_id=scan_id,
        scan_type=ScanType.RECENT_CHANGES_DEEP,
        repo=repo,
        platform=platform,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        time_window_days=days,
        agent_results=final_results,
        total_findings=total_findings,
        summary=summary,
        pipeline_log=pipeline_log,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        cost_usd=total_cost,
    )
    await _save(storage, scan_db_id, result)
    _emit("scan_complete", summary)
    return result


def _empty_result(scan_id, repo, platform, days, started_at, pipeline_log) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        scan_type=ScanType.RECENT_CHANGES_DEEP,
        repo=repo,
        platform=platform,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        time_window_days=days,
        summary="No merged PRs to re-review in the specified window.",
        pipeline_log=pipeline_log,
    )


async def _save(storage, scan_db_id, result: ScanResult) -> None:
    # Let a save failure propagate: the outer handler marks the scan failed and
    # emits scan_error. Swallowing it here emitted scan_complete anyway, so the UI
    # showed "complete" while the DB kept stage=scan_report with nothing persisted
    # (a PR title once overflowed scan_agent_results.agent_name and rolled back the
    # whole save — see migration 006, mirroring the recent_changes #94 fix).
    if storage and scan_db_id:
        await storage.save_scan_result(scan_db_id, result)
