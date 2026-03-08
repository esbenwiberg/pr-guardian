from __future__ import annotations

import json
import os

from pr_guardian.models.findings import AgentResult, Verdict
from pr_guardian.models.output import Decision, ReviewResult

# Agent display names for the PR comment
_AGENT_LABELS: dict[str, str] = {
    "security_privacy": "Security & Privacy",
    "performance": "Performance",
    "architecture_intent": "Architecture & Intent",
    "code_quality_observability": "Code Quality",
    "test_quality": "Test Quality",
    "hotspot": "Hotspots",
}


def _detail_url(review_id: str, base_url: str = "") -> str | None:
    """Build the URL to the findings detail page.

    Uses the provided base_url (inferred from the incoming request), falling
    back to the GUARDIAN_BASE_URL env var for reverse-proxy overrides.
    """
    base = base_url.rstrip("/") or os.environ.get("GUARDIAN_BASE_URL", "").rstrip("/")
    if not base or not review_id:
        return None
    return f"{base}/reviews/{review_id}"


def build_summary_comment(result: ReviewResult, *, base_url: str = "") -> str:
    """Build a slim PR comment with per-area summaries and a link to full details.

    Designed for dual consumption:
    - Humans see a compact overview with high/medium finding counts per area.
    - Agents/tools parse the embedded JSON metadata block for structured data.
    """
    lines: list[str] = []

    # Header
    emoji = {
        "auto_approve": "\u2705",
        "human_review": "\U0001f440",
        "reject": "\u274c",
        "hard_block": "\U0001f6ab",
    }
    decision_label = {
        "auto_approve": "Auto-Approved",
        "human_review": "Human Review Required",
        "reject": "Changes Requested",
        "hard_block": "Blocked",
    }
    lines.append(
        f"## PR Guardian {emoji.get(result.decision.value, '\u2753')} "
        f"{decision_label.get(result.decision.value, 'Unknown')}"
    )
    lines.append("")

    # Compact metrics row
    lines.append(
        f"**Risk** {result.risk_tier.value.upper()} "
        f"\u00b7 **Score** {result.combined_score:.1f}/10 "
        f"\u00b7 **Mechanical** {'passed' if result.mechanical_passed else 'FAILED'}"
    )
    lines.append("")

    # Per-agent area summaries (only when agents ran)
    if result.agent_results:
        verdict_emoji = {"pass": "\u2705", "warn": "\u26a0\ufe0f", "flag_human": "\U0001f50d"}
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

        for agent in result.agent_results:
            label = _AGENT_LABELS.get(agent.agent_name, agent.agent_name)
            icon = verdict_emoji.get(agent.verdict.value, "\u2753")

            # Count high/medium+ findings
            counts: dict[str, int] = {}
            for f in agent.findings:
                sev = f.severity.value
                if sev in ("critical", "high", "medium"):
                    counts[sev] = counts.get(sev, 0) + 1

            if counts:
                parts = []
                for sev in sorted(counts, key=lambda s: sev_order.get(s, 99)):
                    parts.append(f"{counts[sev]} {sev}")
                summary = " \u00b7 ".join(parts)
                lines.append(f"- {icon} **{label}** \u2014 {summary}")
            else:
                lines.append(f"- {icon} **{label}** \u2014 no issues")
        lines.append("")

    # Top findings for reject/hard_block (keep actionable items visible)
    if result.decision in (Decision.REJECT, Decision.HARD_BLOCK):
        high_findings = []
        for agent in result.agent_results:
            for f in agent.findings:
                if f.severity.value in ("critical", "high"):
                    high_findings.append((agent.agent_name, f))

        if high_findings:
            lines.append("### Top Issues")
            lines.append("")
            sev_emoji = {"high": "\U0001f536", "critical": "\U0001f534"}
            for agent_name, finding in high_findings[:5]:
                location = f"`{finding.file}"
                if finding.line:
                    location += f":{finding.line}"
                location += "`"
                lines.append(
                    f"- {sev_emoji.get(finding.severity.value, '\u26a0\ufe0f')} "
                    f"**[{finding.severity.value.upper()}]** "
                    f"{location} \u2014 {finding.description}"
                )
            if len(high_findings) > 5:
                lines.append(f"- *...and {len(high_findings) - 5} more*")
            lines.append("")

    # Escalation reasons
    if result.override_reasons:
        lines.append("### Escalation Reasons")
        for reason in result.override_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    # Link to detail page
    detail_url = _detail_url(result.review_id, base_url)
    if detail_url:
        lines.append(f"[\U0001f50e View full findings & export for fix \u2192]({detail_url})")
        lines.append("")

    lines.append("---")
    lines.append("*PR Guardian \u2014 automated review*")

    # Machine-readable metadata for downstream agents/tools
    all_findings = []
    for agent in result.agent_results:
        for finding in agent.findings:
            all_findings.append((agent.agent_name, finding))
    metadata = _build_metadata(result, all_findings, base_url)
    lines.append("")
    lines.append(f"<!-- pr-guardian-metadata: {json.dumps(metadata, separators=(',', ':'))} -->")

    return "\n".join(lines)


def _build_metadata(
    result: ReviewResult,
    all_findings: list[tuple[str, object]],
    base_url: str = "",
) -> dict:
    """Build structured JSON metadata for agent consumption."""
    meta: dict = {
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
    if result.review_id:
        detail_url = _detail_url(result.review_id, base_url)
        if detail_url:
            meta["detail_url"] = detail_url
    return meta


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
