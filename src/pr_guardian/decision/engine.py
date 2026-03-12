from __future__ import annotations

from fnmatch import fnmatch

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.context import RepoRiskClass, ReviewContext, RiskTier, TrustTier, TrustTierResult
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision, ReviewResult

log = structlog.get_logger()

SEVERITY_SCORE = {
    Severity.LOW: 1,
    Severity.MEDIUM: 3,
    Severity.HIGH: 6,
    Severity.CRITICAL: 10,
}

CERTAINTY_WEIGHT = {
    Certainty.DETECTED: 1.0,
    Certainty.SUSPECTED: 0.5,
    Certainty.UNCERTAIN: 0.2,
}

DEFAULT_AGENT_WEIGHTS = {
    "security_privacy": 3.0,
    "test_quality": 2.5,
    "architecture_intent": 2.0,
    "performance": 1.5,
    "hotspot": 1.5,
    "code_quality_observability": 1.0,
}


def validated_certainty(finding: Finding, config: GuardianConfig) -> Certainty:
    """Validate agent's claimed certainty against evidence. Downgrade if unsupported."""
    evidence = finding.evidence_basis
    min_detected = config.certainty_validation.detected_min_signals
    min_suspected = config.certainty_validation.suspected_min_signals

    if finding.certainty == Certainty.DETECTED:
        signals = [
            evidence.pattern_match and evidence.cwe_id is not None,
            evidence.suggestion_is_concrete,
            evidence.saw_full_context,
            evidence.cross_references >= 1,
        ]
        if sum(signals) < min_detected:
            return Certainty.SUSPECTED

    if finding.certainty == Certainty.SUSPECTED:
        signals = [
            evidence.pattern_match,
            evidence.saw_full_context,
            evidence.suggestion_is_concrete,
        ]
        if sum(signals) < min_suspected:
            return Certainty.UNCERTAIN

    return finding.certainty


def finding_score(finding: Finding, config: GuardianConfig) -> float:
    """Score a single finding based on validated certainty and severity."""
    validated = validated_certainty(finding, config)
    return SEVERITY_SCORE[finding.severity] * CERTAINTY_WEIGHT[validated]


def agent_score(result: AgentResult, config: GuardianConfig) -> float:
    """Derive agent risk score from its findings. Scale 0-10."""
    if not result.findings:
        return 0.0
    scores = [finding_score(f, config) for f in result.findings]
    avg = sum(scores) / len(scores)
    peak = max(scores)
    return min(10.0, max(avg, peak))


def combined_score(
    agent_results: list[AgentResult],
    config: GuardianConfig,
) -> float:
    """Weighted average of agent scores."""
    weights_cfg = config.weights
    weight_map = {
        "security_privacy": weights_cfg.security_privacy,
        "test_quality": weights_cfg.test_quality,
        "architecture_intent": weights_cfg.architecture_intent,
        "performance": weights_cfg.performance,
        "hotspot": weights_cfg.hotspot,
        "code_quality_observability": weights_cfg.code_quality_observability,
    }

    total_weighted = 0.0
    total_weight = 0.0

    for result in agent_results:
        weight = weight_map.get(result.agent_name, 1.0)
        score = agent_score(result, config)
        total_weighted += score * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0
    return total_weighted / total_weight


def check_overrides(
    agent_results: list[AgentResult],
    context: ReviewContext,
    config: GuardianConfig,
) -> list[str]:
    """Check override rules that always force HUMAN_REVIEW."""
    reasons: list[str] = []

    detected_medium_plus = 0
    suspected_count = 0

    for result in agent_results:
        if result.verdict == Verdict.FLAG_HUMAN:
            reasons.append(f"Agent {result.agent_name} flagged for human review")

        for finding in result.findings:
            validated = validated_certainty(finding, config)
            if validated == Certainty.DETECTED and finding.severity in (
                Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL
            ):
                detected_medium_plus += 1
            if validated == Certainty.SUSPECTED:
                suspected_count += 1

    if detected_medium_plus > 0:
        reasons.append(
            f"{detected_medium_plus} finding(s) with detected certainty >= medium severity"
        )
    if suspected_count >= 3:
        reasons.append(f"{suspected_count} suspected findings (threshold: 3)")

    if context.change_profile.adds_dependencies:
        reasons.append("New external dependency added")

    return reasons


def decide(
    context: ReviewContext,
    agent_results: list[AgentResult],
    risk_tier: RiskTier,
    config: GuardianConfig,
    trust_tier_result: TrustTierResult | None = None,
) -> ReviewResult:
    """Apply decision matrix to produce final review decision.

    Trust tier governs *who reviews*, orthogonal to risk tier (analysis depth):
    - AI_ONLY: AI decides (auto-approve allowed)
    - SPOT_CHECK: auto-approve, but flag for optional human glance
    - MANDATORY_HUMAN: block until human approves
    - HUMAN_PRIMARY: block until designated reviewer group approves
    """
    score = combined_score(agent_results, config)
    override_reasons = check_overrides(agent_results, context, config)
    repo_risk = context.repo_risk_class
    trust_tier = trust_tier_result.resolved_tier if trust_tier_result else None

    # Check auto-approve branch rules
    target = context.pr.target_branch
    auto_approve_cfg = config.auto_approve
    branch_blocked = any(
        fnmatch(target, p) for p in auto_approve_cfg.blocked_target_branches
    )

    # Start with decision matrix (risk-based)
    decision = _apply_matrix(risk_tier, repo_risk, agent_results, score, config)

    # Trust tier overrides: MANDATORY_HUMAN and HUMAN_PRIMARY force human review
    reviewer_group_override: str | None = None
    if trust_tier in (TrustTier.MANDATORY_HUMAN, TrustTier.HUMAN_PRIMARY):
        if decision == Decision.AUTO_APPROVE:
            decision = Decision.HUMAN_REVIEW
            override_reasons.append(
                f"Trust tier {trust_tier.value} requires human review"
            )
        if trust_tier == TrustTier.HUMAN_PRIMARY and trust_tier_result:
            reviewer_group_override = trust_tier_result.reviewer_group_override

    # Override: always escalate if override rules triggered
    if override_reasons and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW

    # Override: blocked branches never auto-approve
    if branch_blocked and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        override_reasons.append(f"Target branch {target} is in blocked list")

    # Override: auto-approve disabled
    if not auto_approve_cfg.enabled and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        override_reasons.append("Auto-approve is disabled")

    # Reject: high-confidence, actionable findings — no human needed
    if decision == Decision.HUMAN_REVIEW:
        reject_reasons = _check_reject(agent_results, config)
        if reject_reasons:
            decision = Decision.REJECT
            override_reasons.extend(reject_reasons)

    # Hard block threshold
    if score >= config.thresholds.hard_block_score:
        decision = Decision.HARD_BLOCK

    # Build trust tier metadata for the result
    escalated_from: str | None = None
    trust_tier_reasons: list[str] = []
    trust_tier_files: dict[str, str] = {}
    if trust_tier_result:
        trust_tier_reasons = list(trust_tier_result.reasons + trust_tier_result.escalation_reasons)
        trust_tier_files = {f: t.value for f, t in trust_tier_result.file_tiers.items()}
        if trust_tier_result.escalated:
            # Find original tier from reasons (first escalation reason contains it)
            for r in trust_tier_result.escalation_reasons:
                if r.startswith("Trust tier escalated from "):
                    escalated_from = r.split("from ")[1].split(" to ")[0]
                    break

    result = ReviewResult(
        pr_id=context.pr.pr_id,
        repo=context.pr.repo,
        risk_tier=risk_tier,
        repo_risk_class=repo_risk,
        agent_results=agent_results,
        combined_score=score,
        decision=decision,
        override_reasons=override_reasons,
        trust_tier=trust_tier,
        trust_tier_reasons=trust_tier_reasons,
        trust_tier_files=trust_tier_files,
        reviewer_group_override=reviewer_group_override,
        escalated_from=escalated_from,
    )

    log.info(
        "decision_complete",
        pr_id=context.pr.pr_id,
        decision=decision.value,
        score=round(score, 2),
        risk_tier=risk_tier.value,
        trust_tier=trust_tier.value if trust_tier else "none",
        overrides=len(override_reasons),
    )
    return result


def _check_reject(
    agent_results: list[AgentResult],
    config: GuardianConfig,
) -> list[str]:
    """Check if findings are concrete enough to reject without human review.

    Criteria: at least one finding that is validated as 'detected' certainty
    with high or critical severity AND has a concrete suggestion.
    """
    reasons: list[str] = []
    actionable_count = 0

    for result in agent_results:
        for finding in result.findings:
            validated = validated_certainty(finding, config)
            if (
                validated == Certainty.DETECTED
                and finding.severity in (Severity.HIGH, Severity.CRITICAL)
                and finding.evidence_basis.suggestion_is_concrete
            ):
                actionable_count += 1

    if actionable_count >= 1:
        reasons.append(
            f"{actionable_count} high-confidence actionable finding(s) — "
            f"auto-rejected with fix suggestions"
        )
    return reasons


def recheck_reject(
    agent_results: list[AgentResult],
    config: GuardianConfig,
) -> bool:
    """Return True if the REJECT criteria still hold on the (filtered) findings.

    Called after post-decision noise reduction to verify the high-confidence
    actionable findings that triggered REJECT weren't removed by the severity
    floor, deduplication, or adversarial validator.
    """
    return bool(_check_reject(agent_results, config))


def _apply_matrix(
    tier: RiskTier,
    repo_risk: RepoRiskClass,
    agent_results: list[AgentResult],
    score: float,
    config: GuardianConfig,
) -> Decision:
    """Apply the decision matrix from the design doc."""
    has_flags = any(r.verdict == Verdict.FLAG_HUMAN for r in agent_results)
    has_warns = any(r.verdict == Verdict.WARN for r in agent_results)
    all_pass = all(r.verdict == Verdict.PASS for r in agent_results)
    threshold = config.thresholds.auto_approve_max_score

    if tier == RiskTier.TRIVIAL:
        if repo_risk == RepoRiskClass.CRITICAL:
            return Decision.HUMAN_REVIEW
        return Decision.AUTO_APPROVE

    if tier == RiskTier.LOW:
        if repo_risk == RepoRiskClass.CRITICAL:
            return Decision.HUMAN_REVIEW
        if repo_risk == RepoRiskClass.ELEVATED:
            return Decision.AUTO_APPROVE if all_pass else Decision.HUMAN_REVIEW
        # standard
        if has_flags:
            return Decision.HUMAN_REVIEW
        return Decision.AUTO_APPROVE

    if tier == RiskTier.MEDIUM:
        if repo_risk in (RepoRiskClass.ELEVATED, RepoRiskClass.CRITICAL):
            return Decision.HUMAN_REVIEW
        # standard
        if has_flags:
            return Decision.HUMAN_REVIEW
        if has_warns and score >= threshold:
            return Decision.HUMAN_REVIEW
        return Decision.AUTO_APPROVE

    # HIGH
    return Decision.HUMAN_REVIEW
