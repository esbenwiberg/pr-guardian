from __future__ import annotations

import asyncio
import tempfile
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

log = structlog.get_logger()

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
) -> ReviewResult:
    """Main review pipeline: Discovery → Mechanical → Triage → Agents → Decision."""
    log.info("review_started", pr_id=pr.pr_id, repo=pr.repo)

    # Set pending status
    await adapter.set_status(pr, "pending", "PR Guardian review in progress")

    # Fetch diff
    diff = await adapter.fetch_diff(pr)
    changed_files = diff.file_paths

    # Use temp dir as repo_path (in production, would be a shallow clone)
    repo_path = Path(tempfile.mkdtemp(prefix=f"review-{pr.pr_id}-"))

    # Stage 0: Discovery
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

    log.info(
        "discovery_complete",
        languages=list(language_map.languages.keys()),
        files=len(changed_files),
        lines=diff.lines_changed,
    )

    # Stage 1: Mechanical Gates
    mechanical_results = await run_mechanical_checks(
        repo_path, language_map, changed_files, config, pr.target_branch,
    )

    if not all_checks_passed(mechanical_results):
        log.info("mechanical_gate_failed", pr_id=pr.pr_id)
        from pr_guardian.models.context import RiskTier
        result = ReviewResult(
            pr_id=pr.pr_id,
            repo=pr.repo,
            risk_tier=RiskTier.HIGH,
            repo_risk_class=context.repo_risk_class,
            mechanical_results=[
                _convert_mechanical(r) for r in mechanical_results
            ],
            mechanical_passed=False,
            decision=Decision.HARD_BLOCK,
            summary="Mechanical checks failed — PR blocked.",
        )
        await _post_results(adapter, pr, result, config)
        return result

    # Stage 2: Triage
    triage_result = classify(context, config)
    log.info("triage_complete", tier=triage_result.risk_tier.value, agents=sorted(triage_result.agent_set))

    # Stage 3: AI Agents (parallel)
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

    # Stage 4: Decision
    result = decide(context, agent_results, triage_result.risk_tier, config)
    result.mechanical_results = [_convert_mechanical(r) for r in mechanical_results]
    result.mechanical_passed = True

    # Post results
    await _post_results(adapter, pr, result, config)

    log.info(
        "review_complete",
        pr_id=pr.pr_id,
        decision=result.decision.value,
        score=round(result.combined_score, 2),
    )
    return result


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
) -> None:
    """Post review results back to the platform."""
    comment = build_summary_comment(result)
    labels = get_review_labels(result)

    try:
        await adapter.post_comment(pr, comment)
        for label in labels:
            await adapter.add_label(pr, label)

        if result.decision == Decision.AUTO_APPROVE:
            await adapter.approve_pr(pr)
            await adapter.set_status(pr, "success", "PR Guardian: Auto-approved")
        elif result.decision == Decision.HUMAN_REVIEW:
            await adapter.set_status(pr, "success", "PR Guardian: Human review required")
            await adapter.request_reviewers(pr, config.human_review.reviewer_group)
        elif result.decision == Decision.HARD_BLOCK:
            await adapter.set_status(pr, "failure", "PR Guardian: Blocked")
    except Exception as e:
        log.error("post_results_failed", pr_id=pr.pr_id, error=str(e))
