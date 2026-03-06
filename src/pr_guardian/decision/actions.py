from __future__ import annotations

import json

from pr_guardian.models.findings import AgentResult, Verdict
from pr_guardian.models.output import Decision, ReviewResult


def build_summary_comment(result: ReviewResult) -> str:
    """Build the PR comment summarizing the review.

    Designed for dual consumption:
    - Humans see readable markdown with findings, verdicts, and action items.
    - Agents/tools parse the embedded JSON metadata block for structured data.
    """
    lines: list[str] = []

    # Header
    emoji = {
        "auto_approve": "✅",
        "human_review": "👀",
        "reject": "❌",
        "hard_block": "🚫",
    }
    decision_label = {
        "auto_approve": "Auto-Approved",
        "human_review": "Human Review Required",
        "reject": "Changes Requested",
        "hard_block": "Blocked",
    }
    lines.append(
        f"## PR Guardian {emoji.get(result.decision.value, '❓')} "
        f"{decision_label.get(result.decision.value, 'Unknown')}"
    )
    lines.append("")

    # Summary table
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Risk Tier | **{result.risk_tier.value.upper()}** |")
    lines.append(f"| Repo Risk Class | {result.repo_risk_class.value} |")
    lines.append(f"| Combined Score | {result.combined_score:.1f} / 10 |")
    lines.append(f"| Decision | **{result.decision.value.replace('_', ' ').title()}** |")
    lines.append("")

    # Mechanical checks
    if result.mechanical_results:
        lines.append("### Mechanical Checks")
        for mech in result.mechanical_results:
            status = "✅" if mech.passed else "❌"
            lines.append(f"- {status} **{mech.tool}**")
            if mech.error:
                lines.append(f"  - {mech.error}")
        lines.append("")

    # Agent verdicts
    if result.agent_results:
        lines.append("### Agent Reviews")
        for agent in result.agent_results:
            verdict_emoji = {"pass": "✅", "warn": "⚠️", "flag_human": "🔍"}
            finding_count = len(agent.findings)
            suffix = f" — {finding_count} finding(s)" if finding_count else ""
            lines.append(
                f"- {verdict_emoji.get(agent.verdict.value, '❓')} "
                f"**{agent.agent_name}**: {agent.verdict.value}{suffix}"
            )
            if agent.error:
                lines.append(f"  - Error: {agent.error}")
        lines.append("")

    # Findings — shown prominently for reject, collapsible otherwise
    all_findings = []
    for agent in result.agent_results:
        for finding in agent.findings:
            all_findings.append((agent.agent_name, finding))

    if all_findings:
        is_reject = result.decision in (Decision.REJECT, Decision.HARD_BLOCK)
        sev_emoji = {"low": "ℹ️", "medium": "⚠️", "high": "🔶", "critical": "🔴"}

        if is_reject:
            lines.append("### Issues to Fix")
            lines.append("")
        else:
            lines.append("<details>")
            lines.append(f"<summary><b>Findings ({len(all_findings)} total)</b></summary>")
            lines.append("")

        for agent_name, finding in all_findings:
            location = f"`{finding.file}"
            if finding.line:
                location += f":{finding.line}"
            location += "`"

            lines.append(
                f"- {sev_emoji.get(finding.severity.value, '❓')} "
                f"**[{finding.severity.value.upper()}]** "
                f"{location} — {finding.description}"
            )
            if finding.suggestion:
                lines.append(f"  - **Suggestion:** {finding.suggestion}")
            if finding.cwe:
                lines.append(f"  - CWE: {finding.cwe}")

        lines.append("")
        if not is_reject:
            lines.append("</details>")
            lines.append("")

    # Override / escalation reasons
    if result.override_reasons:
        lines.append("### Escalation Reasons")
        for reason in result.override_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("---")
    lines.append("*PR Guardian — automated review*")

    # Machine-readable metadata for downstream agents/tools
    metadata = _build_metadata(result, all_findings)
    lines.append("")
    lines.append(f"<!-- pr-guardian-metadata: {json.dumps(metadata, separators=(',', ':'))} -->")

    return "\n".join(lines)


def _build_metadata(
    result: ReviewResult,
    all_findings: list[tuple[str, object]],
) -> dict:
    """Build structured JSON metadata for agent consumption."""
    return {
        "version": "1",
        "decision": result.decision.value,
        "risk_tier": result.risk_tier.value,
        "repo_risk_class": result.repo_risk_class.value,
        "combined_score": round(result.combined_score, 2),
        "mechanical_passed": result.mechanical_passed,
        "agents": {
            agent.agent_name: {
                "verdict": agent.verdict.value,
                "finding_count": len(agent.findings),
                "error": agent.error,
            }
            for agent in result.agent_results
        },
        "findings": [
            {
                "agent": agent_name,
                "severity": f.severity.value,
                "certainty": f.certainty.value,
                "file": f.file,
                "line": f.line,
                "category": f.category,
                "cwe": f.cwe,
            }
            for agent_name, f in all_findings
        ],
        "override_reasons": result.override_reasons,
        "cost_usd": result.cost_usd,
    }


def get_review_labels(result: ReviewResult) -> list[str]:
    """Get labels to apply to the PR."""
    labels: list[str] = []
    if result.decision == Decision.HUMAN_REVIEW:
        labels.append("needs-human-review")
    elif result.decision == Decision.REJECT:
        labels.append("changes-requested")
    elif result.decision == Decision.HARD_BLOCK:
        labels.append("guardian-blocked")
    elif result.decision == Decision.AUTO_APPROVE:
        labels.append("guardian-approved")
    return labels
