"""Post-decision severity floor: suppress low-value findings from review output.

The decision engine scores ALL findings for correct risk assessment. This module
runs *after* scoring to remove findings that are noise for the given risk tier,
so developers only see actionable items. Suppressed findings are still reflected
in the combined_score and decision — this is display-level filtering only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import structlog

from pr_guardian.config.schema import GuardianConfig, SeverityFloorRule
from pr_guardian.models.context import RiskTier
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict

log = structlog.get_logger()

# Map risk tier to the config field name holding its suppression rules.
_TIER_RULES_ATTR = {
    RiskTier.LOW: "low_tier_suppress",
    RiskTier.MEDIUM: "medium_tier_suppress",
    RiskTier.HIGH: "high_tier_suppress",
}


def _matches_rule(finding: Finding, rule: SeverityFloorRule) -> bool:
    """Check whether a finding matches a suppression rule."""
    if finding.severity.value != rule.severity:
        return False
    if rule.certainty is not None and finding.certainty.value != rule.certainty:
        return False
    return True


def _should_suppress(finding: Finding, rules: list[SeverityFloorRule]) -> bool:
    """Return True if the finding matches ANY suppression rule."""
    return any(_matches_rule(finding, rule) for rule in rules)


def filter_findings(
    agent_results: list[AgentResult],
    risk_tier: RiskTier,
    config: GuardianConfig,
) -> tuple[list[AgentResult], int]:
    """Filter findings based on risk-tier severity floor.

    Returns:
        (filtered_agent_results, total_suppressed_count)

    The returned AgentResult objects are copies — originals are not mutated.
    If an agent's findings are ALL suppressed, its verdict is downgraded to PASS
    to avoid a confusing "Warn" label with no listed issues.
    """
    floor_cfg = config.severity_floor
    if not floor_cfg.enabled:
        return agent_results, 0

    # TRIVIAL tier runs no agents, nothing to filter.
    if risk_tier == RiskTier.TRIVIAL:
        return agent_results, 0

    rules_attr = _TIER_RULES_ATTR.get(risk_tier)
    if not rules_attr:
        return agent_results, 0

    rules: list[SeverityFloorRule] = getattr(floor_cfg, rules_attr)
    if not rules:
        return agent_results, 0

    filtered: list[AgentResult] = []
    total_suppressed = 0

    for result in agent_results:
        kept: list[Finding] = []
        suppressed = 0

        for finding in result.findings:
            if _should_suppress(finding, rules):
                suppressed += 1
            else:
                kept.append(finding)

        total_suppressed += suppressed

        # Build a copy with filtered findings.
        new_result = replace(
            result,
            findings=kept,
            # Downgrade verdict if all findings were suppressed.
            verdict=(
                Verdict.PASS
                if suppressed > 0 and not kept and result.verdict != Verdict.FLAG_HUMAN
                else result.verdict
            ),
        )
        filtered.append(new_result)

    if total_suppressed:
        log.info(
            "severity_floor_applied",
            risk_tier=risk_tier.value,
            suppressed=total_suppressed,
        )

    return filtered, total_suppressed
