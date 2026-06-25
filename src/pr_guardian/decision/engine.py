from __future__ import annotations

from fnmatch import fnmatch

import structlog

from pr_guardian.config.schema import DependencyPolicyConfig, GuardianConfig
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import (
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    TrustTier,
    TrustTierResult,
    trust_tier_label,
)
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    Finding,
    GateResult,
    Severity,
    Verdict,
)
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.triage.hotspots import check_hotspot_hits

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


def finding_overrides(
    agent_results: list[AgentResult],
    config: GuardianConfig,
) -> list[str]:
    """Finding-derived escalation reasons.

    Depends only on the findings + config — no ReviewContext — so it can be
    recomputed from re-evaluated findings during a re-review without rebuilding
    discovery/triage state. The structural sticky triggers (new dep, hotspot,
    trust tier, …) are computed separately in :func:`check_overrides`.
    """
    finding_reasons: list[str] = []

    detected_medium_plus = 0
    suspected_count = 0
    for result in agent_results:
        if result.verdict == Verdict.FLAG_HUMAN:
            # A FLAG_HUMAN verdict reaches here three ways (see agents/base.py):
            # a degraded run (LLM/JSON error → fail-safe escalation), a genuine
            # flag carrying the agent's reasoning, or a flag with neither. Keep
            # these honest — an errored agent did not *judge* the PR, and a bare
            # flag with no evidence shouldn't read like a cited finding.
            if result.error:
                finding_reasons.append(
                    f"Agent {result.agent_name} could not complete its review "
                    f"({result.error}) — escalated to a human as a safety fallback"
                )
            elif result.verdict_explanation:
                finding_reasons.append(
                    f"Agent {result.agent_name} flagged for human review: "
                    f"{result.verdict_explanation}"
                )
            else:
                finding_reasons.append(
                    f"Agent {result.agent_name} flagged for human review "
                    f"(no specific finding cited)"
                )
        for finding in result.findings:
            validated = validated_certainty(finding, config)
            if validated == Certainty.DETECTED and finding.severity in (
                Severity.MEDIUM,
                Severity.HIGH,
                Severity.CRITICAL,
            ):
                detected_medium_plus += 1
            if validated == Certainty.SUSPECTED:
                suspected_count += 1

    if detected_medium_plus > 0:
        finding_reasons.append(
            f"{detected_medium_plus} finding(s) with detected certainty >= medium severity"
        )
    if suspected_count >= 3:
        finding_reasons.append(f"{suspected_count} suspected findings (threshold: 3)")

    return finding_reasons


def _dependency_trigger(
    profile: ChangeProfile, policy: DependencyPolicyConfig
) -> StickyTrigger | None:
    """Build the dependency-change sticky trigger per the configured policy.

    Returns ``None`` when no escalation is warranted (policy off, or the only
    dependency change is of a kind the policy excludes).
    """
    if not policy.require_human:
        return None

    if profile.adds_dependencies:
        return StickyTrigger(
            kind="new_dep",
            label="Dependency added or changed",
            source="adds_dependencies",
            reason="PR adds or changes an external dependency",
        )
    if policy.include_lockfiles and profile.changes_dependency_lockfile:
        return StickyTrigger(
            kind="new_dep",
            label="Dependency lockfile changed",
            source="changes_dependency_lockfile",
            reason="PR changes a dependency lockfile (resolved/transitive deps)",
        )
    if policy.include_removals and profile.removes_dependencies:
        return StickyTrigger(
            kind="new_dep",
            label="Dependency removed",
            source="removes_dependencies",
            reason="PR removes an external dependency",
        )
    return None


def check_overrides(
    agent_results: list[AgentResult],
    context: ReviewContext,
    config: GuardianConfig,
) -> tuple[list[StickyTrigger], list[str]]:
    """Split escalation reasons into sticky structural triggers and finding-derived reasons."""
    sticky: list[StickyTrigger] = []
    finding_reasons: list[str] = finding_overrides(agent_results, config)

    dep_trigger = _dependency_trigger(context.change_profile, config.dependency_policy)
    if dep_trigger is not None:
        sticky.append(dep_trigger)

    if context.repo_risk_class in (RepoRiskClass.ELEVATED, RepoRiskClass.CRITICAL):
        sticky.append(
            StickyTrigger(
                kind="repo_risk",
                label=f"Elevated repo risk: {context.repo_risk_class.value}",
                source=context.repo_risk_class.value,
                reason=f"Repository is classified as {context.repo_risk_class.value} risk",
            )
        )

    hotspot_hits = check_hotspot_hits(context.changed_files, context.hotspots)
    if hotspot_hits:
        source = hotspot_hits[0]
        sticky.append(
            StickyTrigger(
                kind="hotspot",
                label=f"Hotspot file touched: {source}",
                source=source,
                reason=f"{len(hotspot_hits)} hotspot file(s) changed: {', '.join(hotspot_hits[:3])}",
            )
        )

    if context.security_surface.has_hits():
        hit_files = list(context.security_surface.classifications.keys())
        source = hit_files[0]
        sticky.append(
            StickyTrigger(
                kind="path_risk",
                label=f"Security surface touched: {source}",
                source=source,
                reason=f"{len(hit_files)} security-surface file(s) changed",
            )
        )

    archmap_hubs = context.archmap.hub_files()
    if archmap_hubs:
        source = archmap_hubs[0].path
        examples = ", ".join(
            f"{f.path} "
            f"(Ca={f.ca}, dependents={len(f.dependents)}, "
            f"risk={f.risk if f.risk is not None else 'n/a'})"
            for f in archmap_hubs[:3]
        )
        sticky.append(
            StickyTrigger(
                kind="archmap_hub",
                label=f"Archmap hub touched: {source}",
                source=source,
                reason=f"Archmap classified {len(archmap_hubs)} changed file(s) as hub: {examples}",
            )
        )

    return sticky, finding_reasons


def resolve_decision(
    *,
    risk_tier: RiskTier,
    repo_risk: RepoRiskClass,
    agent_results: list[AgentResult],
    score: float,
    config: GuardianConfig,
    trust_tier: TrustTier | None,
    sticky_triggers: list[StickyTrigger],
    finding_reasons: list[str],
    target_branch: str,
    auto_approve_unlocked: bool = True,
    gate_result: GateResult | None = None,
) -> Decision:
    """Apply the decision matrix + overrides to a set of findings.

    This is the single source of truth for turning (risk, structure, findings,
    trust) into a verdict. It is shared by the full-review path (:func:`decide`)
    and the re-review path so the two can never diverge — a re-review with the
    same inputs yields the same decision as a first review.

    The structural inputs (``sticky_triggers``, ``trust_tier``, ``repo_risk``,
    ``target_branch``) are *replayed* on re-review from the stored original
    review; only the finding-derived inputs (``score``, ``finding_reasons``,
    ``agent_results``) are recomputed from the re-evaluated findings.

    ``gate_result`` is ``None`` in standard mode and in re-review (the cached
    gate verdict is already in ``sticky_triggers``). In structural_only full
    review the orchestrator computes it and passes it here.

    Mutates ``sticky_triggers`` and ``finding_reasons`` in place to record the
    trust-tier and branch/disabled escalations on the audit trail.
    """
    # structural_only: bypass matrix + finding-based human escalation.
    if config.escalation_policy.mode == "structural_only":
        return _resolve_structural_only(
            agent_results=agent_results,
            score=score,
            config=config,
            trust_tier=trust_tier,
            sticky_triggers=sticky_triggers,
            finding_reasons=finding_reasons,
            auto_approve_unlocked=auto_approve_unlocked,
            gate_result=gate_result,
        )

    auto_approve_cfg = config.auto_approve
    branch_blocked = any(
        fnmatch(target_branch, p) for p in auto_approve_cfg.blocked_target_branches
    )

    # Start with decision matrix (risk-based)
    decision = _apply_matrix(risk_tier, repo_risk, agent_results, score, config)

    # Trust tier overrides: MANDATORY_HUMAN and HUMAN_PRIMARY force human review.
    # The sticky trigger is recorded unconditionally so the structural audit
    # trail reflects the restrictive tier even when findings already escalated.
    if trust_tier in (TrustTier.MANDATORY_HUMAN, TrustTier.HUMAN_PRIMARY):
        sticky_triggers.append(
            StickyTrigger(
                kind="trust_tier",
                label=f"Trust tier: {trust_tier_label(trust_tier)}",
                source=trust_tier.value,
                reason=f"{trust_tier_label(trust_tier)} tier requires human review",
            )
        )
        if decision == Decision.AUTO_APPROVE:
            decision = Decision.HUMAN_REVIEW

    # Override: always escalate if sticky triggers or finding reasons exist
    if (sticky_triggers or finding_reasons) and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW

    # Override: blocked branches never auto-approve
    if branch_blocked and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        finding_reasons.append(f"Target branch {target_branch} is in blocked list")

    # Override: auto-approve disabled
    if not auto_approve_cfg.enabled and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        finding_reasons.append("Auto-approve is disabled")

    # Override: auto-approve is locked until the repo is configured to be judged
    # (explicit trust-tier rules) or Archmap topology is available. Unconfigured
    # repos are never auto-approved — they always go to a human.
    if not auto_approve_unlocked and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        finding_reasons.append(
            "Auto-approve locked: profile has no trust-tier rules and no archmap"
        )

    # Reject: high-confidence, actionable findings — no human needed
    if decision == Decision.HUMAN_REVIEW:
        reject_reasons = _check_reject(agent_results, config)
        if reject_reasons:
            decision = Decision.REJECT
            finding_reasons.extend(reject_reasons)

    # Hard block threshold
    if score >= config.thresholds.hard_block_score:
        decision = Decision.HARD_BLOCK

    return decision


def decide(
    context: ReviewContext,
    agent_results: list[AgentResult],
    risk_tier: RiskTier,
    config: GuardianConfig,
    trust_tier_result: TrustTierResult | None = None,
    gate_result: GateResult | None = None,
) -> ReviewResult:
    """Apply decision matrix to produce final review decision.

    Trust tier governs *who reviews*, orthogonal to risk tier (analysis depth):
    - AI_ONLY: AI decides (auto-approve allowed)
    - SPOT_CHECK: auto-approve, but flag for optional human glance
    - MANDATORY_HUMAN: block until human approves
    - HUMAN_PRIMARY: block until designated reviewer group approves
    """
    score = combined_score(agent_results, config)
    sticky_triggers, finding_reasons = check_overrides(agent_results, context, config)
    repo_risk = context.repo_risk_class
    trust_tier = trust_tier_result.resolved_tier if trust_tier_result else None

    # Auto-approve is locked unless the repo is configured to be judged: explicit
    # trust-tier rules, or Archmap topology available for this PR.
    archmap_available = bool(context.archmap.files) and not context.archmap.error
    auto_approve_unlocked = bool(config.trust_tiers.rules) or archmap_available

    decision = resolve_decision(
        risk_tier=risk_tier,
        repo_risk=repo_risk,
        agent_results=agent_results,
        score=score,
        config=config,
        trust_tier=trust_tier,
        sticky_triggers=sticky_triggers,
        finding_reasons=finding_reasons,
        target_branch=context.pr.target_branch,
        auto_approve_unlocked=auto_approve_unlocked,
        gate_result=gate_result,
    )

    reviewer_group_override: str | None = None
    if trust_tier == TrustTier.HUMAN_PRIMARY and trust_tier_result:
        reviewer_group_override = trust_tier_result.reviewer_group_override

    # Build trust tier metadata for the result
    escalated_from: str | None = None
    trust_tier_files: dict[str, str] = {}
    if trust_tier_result:
        trust_tier_files = {f: t.value for f, t in trust_tier_result.file_tiers.items()}
        if trust_tier_result.escalated:
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
        sticky_triggers=sticky_triggers,
        finding_reasons=finding_reasons,
        trust_tier=trust_tier,
        trust_tier_files=trust_tier_files,
        reviewer_group_override=reviewer_group_override,
        escalated_from=escalated_from,
        auto_approve_unlocked=auto_approve_unlocked,
    )

    log.info(
        "decision_complete",
        pr_id=context.pr.pr_id,
        decision=decision.value,
        score=round(score, 2),
        risk_tier=risk_tier.value,
        trust_tier=trust_tier.value if trust_tier else "none",
        overrides=len(sticky_triggers) + len(finding_reasons),
    )
    return result


def _reject_predicate(finding: Finding, config: GuardianConfig, threshold: str) -> bool:
    """Return True when a finding meets the configured reject threshold."""
    if threshold == "any":
        return True
    validated = validated_certainty(finding, config)
    if validated != Certainty.DETECTED:
        return False
    if threshold == "medium_plus":
        return (
            finding.severity in (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
            and finding.evidence_basis.suggestion_is_concrete
        )
    # confident_only (default)
    return (
        finding.severity in (Severity.HIGH, Severity.CRITICAL)
        and finding.evidence_basis.suggestion_is_concrete
    )


def finding_meets_reject_threshold(finding: Finding, config: GuardianConfig) -> bool:
    """Whether a finding meets the configured reject threshold — i.e. it's one of
    the findings that bounces the PR back. Surfaces (e.g. inline comments) use this
    to guarantee that whatever drove the verdict is shown to the author, regardless
    of any display-level severity floor."""
    return _reject_predicate(finding, config, config.escalation_policy.reject_threshold)


def _check_reject(
    agent_results: list[AgentResult],
    config: GuardianConfig,
    reject_threshold: str = "confident_only",
) -> list[str]:
    """Check if findings meet the reject threshold.

    Criteria for the default confident_only threshold: at least one finding
    validated as 'detected' with high/critical severity and a concrete suggestion.
    The threshold can be widened to medium_plus or any via the escalation policy.
    """
    actionable_count = sum(
        1
        for result in agent_results
        for finding in result.findings
        if _reject_predicate(finding, config, reject_threshold)
    )
    if actionable_count >= 1:
        return [
            f"{actionable_count} high-confidence actionable finding(s) — "
            f"auto-rejected with fix suggestions"
        ]
    return []


# Sticky trigger kinds that escalate to HUMAN_REVIEW in structural_only mode.
# gate_agent is included so that replayed triggers from the original review
# correctly re-escalate on re-review without a new gate agent call.
_STRUCTURAL_STICKY_KINDS = frozenset(
    {"new_dep", "repo_risk", "hotspot", "path_risk", "archmap_hub", "gate_agent"}
)


def _resolve_structural_only(
    *,
    agent_results: list[AgentResult],
    score: float,
    config: GuardianConfig,
    trust_tier: TrustTier | None,
    sticky_triggers: list[StickyTrigger],
    finding_reasons: list[str],
    auto_approve_unlocked: bool,
    gate_result: GateResult | None,
) -> Decision:
    """Structural-only decision branch.

    Escalates to HUMAN_REVIEW only on structural triggers (trust tier, archmap hub,
    existing structural stickies, or a gated gate_result). Finding-derived reasons
    do not gate humans; they drive _check_reject (REJECT) or stay as comments.
    Hard block still applies unconditionally.
    """
    decision = Decision.AUTO_APPROVE

    # Trust tier: mandatory_human / human_primary always require a human.
    if trust_tier in (TrustTier.MANDATORY_HUMAN, TrustTier.HUMAN_PRIMARY):
        sticky_triggers.append(
            StickyTrigger(
                kind="trust_tier",
                label=f"Trust tier: {trust_tier_label(trust_tier)}",
                source=trust_tier.value,
                reason=f"{trust_tier_label(trust_tier)} tier requires human review",
            )
        )
        decision = Decision.HUMAN_REVIEW

    # Existing structural stickies (new_dep / repo_risk / hotspot / path_risk /
    # archmap_hub) — also catches gate_agent triggers replayed from re-review.
    if any(st.kind in _STRUCTURAL_STICKY_KINDS for st in sticky_triggers):
        decision = Decision.HUMAN_REVIEW

    # Gate agent: gated=True means the semantic gate fired (or errored closed).
    # Guard against duplicate if a gate_agent sticky was already replayed from
    # a prior review cycle (block 2 above already escalated via it).
    if gate_result is not None and gate_result.gated:
        if not any(st.kind == "gate_agent" for st in sticky_triggers):
            sticky_triggers.append(
                StickyTrigger(
                    kind="gate_agent",
                    label=f"Gate agent: {gate_result.level.upper()} danger",
                    source="gate_agent",
                    reason=gate_result.reason,
                )
            )
        decision = Decision.HUMAN_REVIEW

    # Locked auto-approve (no trust-tier rules, no archmap) → human review.
    if not auto_approve_unlocked and decision == Decision.AUTO_APPROVE:
        decision = Decision.HUMAN_REVIEW
        finding_reasons.append(
            "Auto-approve locked: profile has no trust-tier rules and no archmap"
        )

    # Findings drive REJECT (not human review) at the configured threshold.
    # This runs unconditionally — a confident finding on safe paths → REJECT,
    # not AUTO_APPROVE; and REJECT overrides HUMAN_REVIEW when both fire.
    reject_threshold = config.escalation_policy.reject_threshold
    reject_reasons = _check_reject(agent_results, config, reject_threshold=reject_threshold)
    if reject_reasons:
        decision = Decision.REJECT
        finding_reasons.extend(reject_reasons)

    # Hard block is unconditional and always wins.
    if score >= config.thresholds.hard_block_score:
        decision = Decision.HARD_BLOCK

    return decision


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

    # HIGH: only auto-approve when agents actually ran and all pass clean
    if agent_results and all_pass and score <= threshold:
        return Decision.AUTO_APPROVE
    return Decision.HUMAN_REVIEW
