from __future__ import annotations

from pr_guardian.models.findings import AgentResult, Verdict
from pr_guardian.models.output import Decision, ReviewResult


def build_summary_comment(result: ReviewResult) -> str:
    """Build the PR comment summarizing the review."""
    lines: list[str] = []

    # Header
    emoji = {"auto_approve": "✅", "human_review": "👀", "hard_block": "🚫"}
    decision_label = {
        "auto_approve": "Auto-Approved",
        "human_review": "Human Review Required",
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
    lines.append(f"| Combined Score | {result.combined_score:.1f} |")
    lines.append(f"| Decision | **{result.decision.value.replace('_', ' ').title()}** |")
    lines.append("")

    # Mechanical checks
    if result.mechanical_results:
        lines.append("### Mechanical Checks")
        for mech in result.mechanical_results:
            status = "✅" if mech.passed else "❌"
            lines.append(f"- {status} **{mech.tool}**")
            if mech.error:
                lines.append(f"  - ⚠️ {mech.error}")
        lines.append("")

    # Agent verdicts
    if result.agent_results:
        lines.append("### Agent Reviews")
        for agent in result.agent_results:
            verdict_emoji = {"pass": "✅", "warn": "⚠️", "flag_human": "🔍"}
            lines.append(
                f"- {verdict_emoji.get(agent.verdict.value, '❓')} "
                f"**{agent.agent_name}**: {agent.verdict.value}"
            )
            if agent.error:
                lines.append(f"  - Error: {agent.error}")
        lines.append("")

    # Findings (collapsible)
    all_findings = []
    for agent in result.agent_results:
        for finding in agent.findings:
            all_findings.append((agent.agent_name, finding))

    if all_findings:
        lines.append("<details>")
        lines.append("<summary>Findings ({} total)</summary>".format(len(all_findings)))
        lines.append("")
        for agent_name, finding in all_findings:
            sev_emoji = {"low": "ℹ️", "medium": "⚠️", "high": "🔶", "critical": "🔴"}
            lines.append(
                f"- {sev_emoji.get(finding.severity.value, '❓')} "
                f"**[{finding.severity.value.upper()}]** "
                f"`{finding.file}:{finding.line or '?'}` — {finding.description}"
            )
            if finding.suggestion:
                lines.append(f"  - 💡 {finding.suggestion}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Override reasons
    if result.override_reasons:
        lines.append("### Escalation Reasons")
        for reason in result.override_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("---")
    lines.append("*PR Guardian — automated review*")

    return "\n".join(lines)


def get_review_labels(result: ReviewResult) -> list[str]:
    """Get labels to apply to the PR."""
    labels: list[str] = []
    if result.decision == Decision.HUMAN_REVIEW:
        labels.append("needs-human-review")
    elif result.decision == Decision.HARD_BLOCK:
        labels.append("guardian-blocked")
    elif result.decision == Decision.AUTO_APPROVE:
        labels.append("guardian-approved")
    return labels
