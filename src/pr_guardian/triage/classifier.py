from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.context import (
    ChangeProfile,
    BlastRadius,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
)
from pr_guardian.triage.hotspots import check_hotspot_hits
from pr_guardian.triage.path_risk import apply_path_risk

log = structlog.get_logger()


@dataclass
class TriageResult:
    """Output of the triage stage."""
    risk_tier: RiskTier
    agent_set: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    hotspot_hits: list[str] = field(default_factory=list)


ALL_AGENTS = frozenset({
    "security_privacy", "performance", "architecture_intent",
    "code_quality_observability", "test_quality", "hotspot",
})


def classify(context: ReviewContext, config: GuardianConfig) -> TriageResult:
    """Classify PR risk tier and select agents to run."""
    profile = context.change_profile
    result = TriageResult(risk_tier=RiskTier.LOW)

    # TRIVIAL: skip agents
    if profile.skip_agents:
        result.risk_tier = RiskTier.TRIVIAL
        result.reasons.append("Trivial: docs/generated/config-only change")
        return _apply_amplifiers(result, context, config)

    # Check for HIGH signals
    high_signals: list[str] = []
    if profile.touches_security_surface:
        high_signals.append("touches security surface")
    if profile.touches_shared_code and _wide_blast_radius(context.blast_radius):
        high_signals.append("wide blast radius (>10 consumers)")
    if profile.crosses_architecture_boundary:
        high_signals.append("crosses architecture boundaries")
    if profile.adds_dependencies:
        high_signals.append("new dependencies added")
    if profile.adds_api_endpoints:
        high_signals.append("new API endpoints added")

    # Check hotspots
    hotspot_hits = check_hotspot_hits(context.changed_files, context.hotspots)
    result.hotspot_hits = hotspot_hits

    if high_signals:
        result.risk_tier = RiskTier.HIGH
        result.reasons.extend(high_signals)
        result.agent_set = set(ALL_AGENTS)
    elif _has_medium_signals(profile, hotspot_hits, context.blast_radius):
        result.risk_tier = RiskTier.MEDIUM
        result.reasons.append("Medium risk: contained risk signals")
        result.agent_set = {"code_quality_observability"} | profile.implied_agents
        if hotspot_hits:
            result.agent_set.add("hotspot")
            result.reasons.append(f"Hotspot files: {', '.join(hotspot_hits[:3])}")
    else:
        result.risk_tier = RiskTier.LOW
        result.reasons.append("Low risk: no risk flags raised")
        result.agent_set = {"code_quality_observability"}

    # Always add test_quality if production code changed
    if profile.has_production_changes and result.risk_tier != RiskTier.TRIVIAL:
        result.agent_set.add("test_quality")

    return _apply_amplifiers(result, context, config)


def _has_medium_signals(
    profile: ChangeProfile,
    hotspot_hits: list[str],
    blast_radius: BlastRadius,
) -> bool:
    return bool(
        profile.touches_data_layer
        or profile.touches_api_boundary
        or hotspot_hits
        or (profile.touches_shared_code and not _wide_blast_radius(blast_radius))
    )


def _wide_blast_radius(blast_radius: BlastRadius) -> bool:
    return any(len(consumers) > 10 for consumers in blast_radius.consumers.values())


def _apply_amplifiers(
    result: TriageResult,
    context: ReviewContext,
    config: GuardianConfig,
) -> TriageResult:
    """Apply language and repo-risk amplifiers."""
    # Cross-stack amplifier
    if context.cross_stack and result.risk_tier != RiskTier.HIGH:
        result.risk_tier = _bump_tier(result.risk_tier)
        result.reasons.append("Amplifier: cross-stack change")

    # Many languages
    if context.language_map.language_count > 3:
        result.risk_tier = RiskTier.HIGH
        result.agent_set = set(ALL_AGENTS)
        result.reasons.append("Amplifier: >3 languages")

    # SQL/terraform/dockerfile always trigger security
    for lang in ("sql", "terraform", "dockerfile"):
        if context.language_map.has(lang):
            result.agent_set.add("security_privacy")

    # Repo risk class amplifier
    risk_class = context.repo_risk_class
    if risk_class == RepoRiskClass.ELEVATED and result.risk_tier not in (RiskTier.HIGH,):
        result.risk_tier = _bump_tier(result.risk_tier)
        result.reasons.append("Amplifier: elevated repo risk class")
    elif risk_class == RepoRiskClass.CRITICAL:
        result.risk_tier = RiskTier.HIGH
        result.agent_set = set(ALL_AGENTS)
        result.reasons.append("Amplifier: critical repo risk class")

    # Apply path-level risk from config
    result.risk_tier, path_reasons = apply_path_risk(
        result.risk_tier,
        context.changed_files,
        config.path_risk,
        context.change_profile.has_production_changes,
    )
    result.reasons.extend(path_reasons)

    log.info(
        "triage_complete",
        risk_tier=result.risk_tier.value,
        agents=sorted(result.agent_set),
        reasons=result.reasons,
    )
    return result


def _bump_tier(tier: RiskTier) -> RiskTier:
    order = [RiskTier.TRIVIAL, RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH]
    idx = order.index(tier)
    return order[min(idx + 1, len(order) - 1)]
