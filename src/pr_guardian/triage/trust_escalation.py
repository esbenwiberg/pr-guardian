"""Post-agent trust tier escalation.

After AI agents run, inspect their findings for security-relevant signals
in files that the path rules classified as low-sensitivity. Escalation is
one-way upward (never lowers the tier) and fully deterministic — no extra
LLM call, just inspecting existing agent output through a trust-tier lens.
"""
from __future__ import annotations

from copy import deepcopy

import structlog

from pr_guardian.config.schema import TrustTierConfig
from pr_guardian.models.context import TrustTier, TrustTierResult, max_trust_tier
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict

log = structlog.get_logger()

# Severity ordering for threshold comparison.
_SEVERITY_ORDER = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def _parse_min_severity(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.MEDIUM


def maybe_escalate_trust(
    trust_result: TrustTierResult,
    agent_results: list[AgentResult],
    config: TrustTierConfig,
) -> TrustTierResult:
    """Check agent findings for trust-relevant escalation triggers.

    Escalation rules (all deterministic):
    1. Security-category finding (severity >= threshold) in a file
       classified below MANDATORY_HUMAN → escalate to MANDATORY_HUMAN.
    2. Agent verdict FLAG_HUMAN → at least MANDATORY_HUMAN.
    3. Critical severity + DETECTED certainty → at least MANDATORY_HUMAN.

    Returns a new TrustTierResult (never mutates the input).
    """
    result = deepcopy(trust_result)
    original_tier = result.resolved_tier
    keywords = {kw.lower() for kw in config.escalation_keywords}
    min_severity = _parse_min_severity(config.escalation_min_severity)

    for agent in agent_results:
        # Trigger 2: Agent verdict FLAG_HUMAN
        if agent.verdict == Verdict.FLAG_HUMAN:
            result.resolved_tier = max_trust_tier(
                result.resolved_tier, TrustTier.MANDATORY_HUMAN,
            )
            result.escalation_reasons.append(
                f"Agent {agent.agent_name} verdict FLAG_HUMAN"
            )

        for finding in agent.findings:
            # Trigger 3: Critical severity + DETECTED certainty
            if (
                finding.severity == Severity.CRITICAL
                and finding.certainty == Certainty.DETECTED
            ):
                result.resolved_tier = max_trust_tier(
                    result.resolved_tier, TrustTier.MANDATORY_HUMAN,
                )
                result.escalation_reasons.append(
                    f"Critical+detected finding in {finding.file}: {finding.category}"
                )
                continue

            # Trigger 1: Security-category finding in low-tier file
            if not _is_security_relevant(finding, keywords, min_severity):
                continue

            file_tier = result.file_tiers.get(finding.file, TrustTier.SPOT_CHECK)
            if _tier_order(file_tier) < _tier_order(TrustTier.MANDATORY_HUMAN):
                # Escalate this file
                result.file_tiers[finding.file] = TrustTier.MANDATORY_HUMAN
                result.resolved_tier = max_trust_tier(
                    result.resolved_tier, TrustTier.MANDATORY_HUMAN,
                )
                result.escalation_reasons.append(
                    f"Security-relevant finding ({finding.category}) "
                    f"in {finding.file}:{finding.line or '?'} "
                    f"(path tier was {file_tier.value})"
                )

    if result.resolved_tier != original_tier:
        result.escalated = True
        result.escalation_reasons.insert(
            0,
            f"Trust tier escalated from {original_tier.value} "
            f"to {result.resolved_tier.value}",
        )
        # Propagate triggering files
        result.triggering_files = [
            fp for fp, ft in result.file_tiers.items()
            if ft == result.resolved_tier
        ]
        log.info(
            "trust_tier_escalated",
            from_tier=original_tier.value,
            to_tier=result.resolved_tier.value,
            reasons=result.escalation_reasons,
        )

    return result


def _is_security_relevant(
    finding: Finding,
    keywords: set[str],
    min_severity: Severity,
) -> bool:
    """Check if a finding is security-relevant based on category keywords and severity."""
    if _SEVERITY_ORDER[finding.severity] < _SEVERITY_ORDER[min_severity]:
        return False
    category_lower = finding.category.lower()
    return any(kw in category_lower for kw in keywords)


def _tier_order(tier: TrustTier) -> int:
    return {
        TrustTier.AI_ONLY: 0,
        TrustTier.SPOT_CHECK: 1,
        TrustTier.MANDATORY_HUMAN: 2,
        TrustTier.HUMAN_PRIMARY: 3,
    }[tier]
