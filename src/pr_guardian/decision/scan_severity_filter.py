"""Post-analysis severity floor for scan findings.

Mirrors the PR review severity floor (severity_filter.py) but operates on
ScanAgentResult / ScanFinding models. Scans don't have per-PR risk tiers,
so a single set of rules (config.severity_floor.scan_suppress) is applied.
"""
from __future__ import annotations

from dataclasses import replace

import structlog

from pr_guardian.config.schema import GuardianConfig, SeverityFloorRule
from pr_guardian.models.findings import Verdict
from pr_guardian.models.scan import ScanAgentResult, ScanFinding

log = structlog.get_logger()


def _matches_rule(finding: ScanFinding, rule: SeverityFloorRule) -> bool:
    if finding.severity.value != rule.severity:
        return False
    if rule.certainty is not None and finding.certainty.value != rule.certainty:
        return False
    return True


def _should_suppress(finding: ScanFinding, rules: list[SeverityFloorRule]) -> bool:
    return any(_matches_rule(finding, rule) for rule in rules)


def filter_scan_findings(
    agent_results: list[ScanAgentResult],
    config: GuardianConfig,
) -> tuple[list[ScanAgentResult], int]:
    """Filter scan findings based on severity floor rules.

    Returns:
        (filtered_agent_results, total_suppressed_count)
    """
    floor_cfg = config.severity_floor
    if not floor_cfg.enabled:
        return agent_results, 0

    rules = floor_cfg.scan_suppress
    if not rules:
        return agent_results, 0

    filtered: list[ScanAgentResult] = []
    total_suppressed = 0

    for result in agent_results:
        kept: list[ScanFinding] = []
        suppressed = 0

        for finding in result.findings:
            if _should_suppress(finding, rules):
                suppressed += 1
            else:
                kept.append(finding)

        total_suppressed += suppressed

        new_result = replace(
            result,
            findings=kept,
            verdict=(
                Verdict.PASS
                if suppressed > 0 and not kept and result.verdict != Verdict.FLAG_HUMAN
                else result.verdict
            ),
        )
        filtered.append(new_result)

    if total_suppressed:
        log.info("scan_severity_floor_applied", suppressed=total_suppressed)

    return filtered, total_suppressed
