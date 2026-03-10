from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog

from pr_guardian.agents.architecture_intent import ArchitectureIntentAgent
from pr_guardian.agents.code_quality_obs import CodeQualityObservabilityAgent
from pr_guardian.agents.hotspot import HotspotAgent
from pr_guardian.agents.performance import PerformanceAgent
from pr_guardian.agents.security_privacy import SecurityPrivacyAgent
from pr_guardian.agents.test_quality import TestQualityAgent
from pr_guardian.config.loader import load_repo_config
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import ReviewEvent, event_bus
from pr_guardian.decision.actions import build_summary_comment, get_review_labels
from pr_guardian.decision.engine import decide
from pr_guardian.discovery.blast_radius import compute_blast_radius
from pr_guardian.discovery.change_profile import build_change_profile
from pr_guardian.discovery.dep_graph import build_dep_graph
from pr_guardian.languages.detector import detect_languages
from pr_guardian.mechanical.runner import all_checks_passed, run_mechanical_checks
from pr_guardian.models.context import RepoRiskClass, ReviewContext
from pr_guardian.models.findings import AgentResult
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import PlatformPR
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.triage.classifier import classify
from pr_guardian.triage.hotspots import load_hotspots
from pr_guardian.triage.surface_map import build_security_surface
from pr_guardian.triage.trust_classifier import classify_trust_tier
from pr_guardian.triage.trust_escalation import maybe_escalate_trust

log = structlog.get_logger()

# Per-million-token pricing (input, output) — best-effort estimates.
# Users can override via config in the future; this covers common models.
_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus":      (15.0, 75.0),
    "claude-sonnet":    (3.0, 15.0),
    "claude-haiku":     (0.80, 4.0),
    "gpt-4o":           (2.50, 10.0),
    "gpt-4o-mini":      (0.15, 0.60),
    "gpt-4-turbo":      (10.0, 30.0),
    "gpt-4":            (30.0, 60.0),
    "gpt-3.5":          (0.50, 1.50),
}
_DEFAULT_PRICE = (3.0, 15.0)  # fallback


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts and model name (best-effort match)."""
    model_lower = model.lower()
    price = _DEFAULT_PRICE
    for prefix, p in _TOKEN_PRICES.items():
        if prefix in model_lower:
            price = p
            break
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _try_import_storage():
    """Lazily import storage to avoid failures when DB is not configured."""
    try:
        from pr_guardian.persistence import storage
        return storage
    except Exception:
        return None

AGENT_REGISTRY = {
    "security_privacy": SecurityPrivacyAgent,
    "performance": PerformanceAgent,
    "architecture_intent": ArchitectureIntentAgent,
    "code_quality_observability": CodeQualityObservabilityAgent,
    "test_quality": TestQualityAgent,
    "hotspot": HotspotAgent,
}


async def run_review(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    service_config: GuardianConfig | None = None,
    *,
    post_comment: bool = True,
    base_url: str = "",
) -> ReviewResult:
    """Main review pipeline: Discovery → Mechanical → Triage → Agents → Decision."""
    log.info("review_started", pr_id=pr.pr_id, repo=pr.repo)

    storage = _try_import_storage()
    review_db_id: uuid.UUID | None = None

    # Create DB record and emit event
    if storage:
        try:
            review_db_id = await storage.create_review_record(pr)
        except Exception as e:
            log.warning("db_create_failed", error=str(e))

    pipeline_log: list[dict] = []

    def _plog(level: str, stage: str, msg: str, **extra):
        pipeline_log.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "stage": stage,
            "msg": msg,
            **{k: v for k, v in extra.items() if v is not None},
        })

    def _emit(stage: str, detail: str = "", **extra):
        event_bus.publish(ReviewEvent(
            review_id=str(review_db_id) if review_db_id else "",
            pr_id=pr.pr_id,
            repo=pr.repo,
            stage=stage,
            detail=detail,
            extra=extra,
        ))

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and review_db_id:
            try:
                await storage.update_review_stage(review_db_id, stage, detail)
            except Exception as e:
                log.warning("db_stage_update_failed", stage=stage, error=str(e))

    # Set pending status
    await adapter.set_status(pr, "pending", "PR Guardian review in progress")

    try:
        return await _run_pipeline(pr, adapter, service_config, storage, review_db_id, pipeline_log, _plog, _emit, _update_stage, post_comment=post_comment, base_url=base_url)
    except Exception as exc:
        if storage and review_db_id:
            try:
                await storage.mark_review_failed(review_db_id, str(exc), pipeline_log=pipeline_log)
            except Exception as db_err:
                log.warning("db_mark_failed_error", error=str(db_err))
        _emit("error", str(exc))
        raise


async def _run_pipeline(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    service_config: GuardianConfig | None,
    storage,
    review_db_id: uuid.UUID | None,
    pipeline_log: list[dict],
    _plog,
    _emit,
    _update_stage,
    *,
    post_comment: bool = True,
    base_url: str = "",
) -> ReviewResult:
    """Inner pipeline logic, separated so run_review can handle errors."""

    # Fetch diff
    diff = await adapter.fetch_diff(pr)
    changed_files = diff.file_paths
    files_with_patch = sum(1 for f in diff.files if f.patch)
    _plog("info", "discovery",
          f"Fetched diff: {len(changed_files)} files, {files_with_patch} with patch content.")
    if changed_files and files_with_patch == 0:
        _plog("warn", "discovery",
              "No patch content retrieved — agents will have no code to review.")

    # Use temp dir as repo_path (in production, would be a shallow clone)
    repo_path = Path(tempfile.mkdtemp(prefix=f"review-{pr.pr_id}-"))

    # Stage 0: Discovery
    await _update_stage("discovery", "Parsing diff and building context")
    config = service_config or load_repo_config(repo_path)

    language_map = detect_languages(changed_files)
    security_surface = build_security_surface(config.security_surface, changed_files)
    dep_graph = build_dep_graph(config.path_risk.critical_consumers or None)
    blast_radius = compute_blast_radius(changed_files, security_surface, dep_graph)
    change_profile = build_change_profile(
        changed_files, diff, security_surface, blast_radius, config.file_roles,
    )
    hotspots = await load_hotspots(pr.repo)

    risk_class_map = {
        "standard": RepoRiskClass.STANDARD,
        "elevated": RepoRiskClass.ELEVATED,
        "critical": RepoRiskClass.CRITICAL,
    }

    context = ReviewContext(
        pr=pr,
        repo_path=repo_path,
        diff=diff,
        changed_files=changed_files,
        lines_changed=diff.lines_changed,
        language_map=language_map,
        primary_language=language_map.primary_language,
        cross_stack=language_map.cross_stack,
        repo_config=config.model_dump(),
        repo_risk_class=risk_class_map.get(config.repo_risk_class, RepoRiskClass.STANDARD),
        hotspots=hotspots,
        security_surface=security_surface,
        blast_radius=blast_radius,
        change_profile=change_profile,
    )

    # Trust tier classification (path-based, deterministic)
    trust_tier_result = classify_trust_tier(
        changed_files, config, context.repo_risk_class,
    )
    context.trust_tier_result = trust_tier_result
    _plog("info", "discovery",
          f"Trust tier: {trust_tier_result.resolved_tier.value}. "
          f"Triggering files: {', '.join(trust_tier_result.triggering_files[:5]) or 'none'}.")

    langs = list(language_map.languages.keys())
    _plog("info", "discovery",
          f"Parsed {len(changed_files)} files across {len(langs)} language(s): {', '.join(langs)}. "
          f"{diff.lines_changed} lines changed.")
    if security_surface.has_hits():
        surface_files = list(security_surface.classifications.keys())
        _plog("info", "discovery",
              f"Security surface files: {', '.join(surface_files[:10])}"
              f"{f' (+{len(surface_files)-10} more)' if len(surface_files) > 10 else ''}")
    log.info(
        "discovery_complete",
        languages=langs,
        files=len(changed_files),
        lines=diff.lines_changed,
    )

    # Stage 1: Mechanical Gates
    await _update_stage("mechanical", "Running mechanical checks")
    mechanical_results = await run_mechanical_checks(
        repo_path, language_map, changed_files, config, pr.target_branch,
    )

    passed_count = sum(1 for r in mechanical_results if r.passed)
    total_count = len(mechanical_results)
    _plog("info", "mechanical",
          f"Mechanical checks: {passed_count}/{total_count} passed.")
    for r in mechanical_results:
        if not r.passed:
            _plog("warn", "mechanical",
                  f"{r.tool}: FAILED — {r.error or f'{len(r.findings)} finding(s)'}")

    if not all_checks_passed(mechanical_results):
        log.info("mechanical_gate_failed", pr_id=pr.pr_id)
        from pr_guardian.models.context import RiskTier
        _plog("error", "mechanical", "Mechanical gate failed — PR blocked.")
        result = ReviewResult(
            pr_id=pr.pr_id,
            repo=pr.repo,
            risk_tier=RiskTier.HIGH,
            repo_risk_class=context.repo_risk_class,
            review_id=str(review_db_id) if review_db_id else "",
            mechanical_results=[
                _convert_mechanical(r) for r in mechanical_results
            ],
            mechanical_passed=False,
            decision=Decision.HARD_BLOCK,
            summary="Mechanical checks failed — PR blocked.",
            pipeline_log=pipeline_log,
        )
        if post_comment:
            await _post_results(adapter, pr, result, config, base_url=base_url)
        await _save_result(storage, review_db_id, result, _emit)
        return result

    # Stage 2: Triage
    await _update_stage("triage", "Classifying risk and selecting agents")
    triage_result = classify(context, config)
    _plog("info", "triage",
          f"Risk tier: {triage_result.risk_tier.value}. "
          f"Agents selected: {', '.join(sorted(triage_result.agent_set)) or 'none'}.")
    for reason in triage_result.reasons:
        _plog("info", "triage", f"Reason: {reason}")
    log.info("triage_complete", tier=triage_result.risk_tier.value, agents=sorted(triage_result.agent_set))

    # Stage 3: AI Agents (parallel)
    await _update_stage("agents", f"Running {len(triage_result.agent_set)} AI agents")
    agent_results: list[AgentResult] = []
    if triage_result.agent_set:
        agent_tasks = []
        for agent_name in triage_result.agent_set:
            agent_cls = AGENT_REGISTRY.get(agent_name)
            if agent_cls:
                agent = agent_cls(config)
                agent_tasks.append(agent.review(context))

        if agent_tasks:
            agent_results = await asyncio.gather(*agent_tasks)

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for ar in agent_results:
        extras = ar.extras or {}
        parts = [f"verdict={ar.verdict.value}", f"{len(ar.findings)} finding(s)"]
        if extras.get("model"):
            parts.append(f"model={extras['model']}")
        if extras.get("response_length"):
            parts.append(f"response={extras['response_length']} chars")
        in_tok = extras.get("input_tokens", 0)
        out_tok = extras.get("output_tokens", 0)
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        if in_tok or out_tok:
            agent_cost = _estimate_cost(extras.get("model", ""), in_tok, out_tok)
            total_cost += agent_cost
            parts.append(f"tokens={in_tok}+{out_tok}")
            parts.append(f"cost=${agent_cost:.4f}")
        level = "warn" if ar.verdict.value == "flag_human" else "info"
        _plog(level, "agents", f"Agent {ar.agent_name}: {', '.join(parts)}")
        if ar.error:
            _plog("error", "agents", f"Agent {ar.agent_name} error: {ar.error}")
        if extras.get("raw_response_preview"):
            _plog("debug", "agents",
                  f"Agent {ar.agent_name} raw response: {extras['raw_response_preview']}",
                  agent=ar.agent_name)

    # Trust tier escalation (post-agents, one-way upward)
    trust_tier_result = maybe_escalate_trust(
        context.trust_tier_result, agent_results, config.trust_tiers,
    )
    context.trust_tier_result = trust_tier_result
    if trust_tier_result.escalated:
        _plog("warn", "trust_escalation",
              f"Trust tier escalated: {' | '.join(trust_tier_result.escalation_reasons)}")

    # Stage 4: Decision
    await _update_stage("decision", "Computing final verdict")
    result = decide(context, agent_results, triage_result.risk_tier, config, trust_tier_result)
    result.review_id = str(review_db_id) if review_db_id else ""
    result.mechanical_results = [_convert_mechanical(r) for r in mechanical_results]
    result.mechanical_passed = True

    result.total_input_tokens = total_input_tokens
    result.total_output_tokens = total_output_tokens
    result.cost_usd = round(total_cost, 6)

    _plog("info", "decision",
          f"Decision: {result.decision.value}. Score: {result.combined_score:.2f}. "
          f"Risk tier: {result.risk_tier.value}.")
    if total_cost > 0:
        _plog("info", "decision",
              f"Total tokens: {total_input_tokens}+{total_output_tokens}. "
              f"Estimated cost: ${total_cost:.4f}.")
    for reason in result.override_reasons:
        _plog("info", "decision", f"Override: {reason}")
    result.pipeline_log = pipeline_log

    # Post results
    if post_comment:
        await _post_results(adapter, pr, result, config, base_url=base_url)

    # Persist to DB
    await _save_result(storage, review_db_id, result, _emit)

    log.info(
        "review_complete",
        pr_id=pr.pr_id,
        decision=result.decision.value,
        score=round(result.combined_score, 2),
    )
    return result


async def _save_result(storage, review_db_id, result, _emit) -> None:
    """Persist the review result and emit the 'complete' event."""
    if storage and review_db_id:
        try:
            await storage.save_review_result(review_db_id, result)
        except Exception as e:
            log.error("db_save_failed", error=str(e))
    _emit("complete", f"Decision: {result.decision.value}", score=result.combined_score)


def _convert_mechanical(r) -> object:
    """Convert MechanicalCheckResult to the output model."""
    from pr_guardian.models.output import MechanicalResult
    return MechanicalResult(
        tool=r.tool,
        passed=r.passed,
        severity=r.severity.value if hasattr(r.severity, 'value') else str(r.severity),
        findings=[{"file": f.file, "line": f.line, "rule": f.rule, "message": f.message}
                  for f in r.findings],
        error=r.error,
    )


async def _post_results(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    result: ReviewResult,
    config: GuardianConfig,
    *,
    base_url: str = "",
) -> None:
    """Post review results back to the platform."""
    comment = build_summary_comment(result, base_url=base_url)
    labels = get_review_labels(result)

    try:
        await adapter.post_comment(pr, comment)
        for label in labels:
            await adapter.add_label(pr, label)

        if result.decision == Decision.AUTO_APPROVE:
            await adapter.approve_pr(pr)
            await adapter.set_status(pr, "success", "PR Guardian: Auto-approved")
            # SPOT_CHECK: approved but request optional human glance
            if result.trust_tier and result.trust_tier.value == "spot_check":
                await adapter.request_reviewers(pr, config.human_review.reviewer_group)
        elif result.decision == Decision.REJECT:
            await adapter.request_changes(pr, comment)
            await adapter.set_status(pr, "failure", "PR Guardian: Changes requested")
        elif result.decision == Decision.HUMAN_REVIEW:
            await adapter.set_status(pr, "success", "PR Guardian: Human review required")
            reviewer_group = (
                result.reviewer_group_override
                or config.human_review.reviewer_group
            )
            await adapter.request_reviewers(pr, reviewer_group)
        elif result.decision == Decision.HARD_BLOCK:
            await adapter.set_status(pr, "failure", "PR Guardian: Blocked")
    except Exception as e:
        log.error("post_results_failed", pr_id=pr.pr_id, error=str(e))
