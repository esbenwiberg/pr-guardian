from __future__ import annotations

import os

from pr_guardian.models.context import TrustTier
from pr_guardian.models.findings import Verdict
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
    """Build a slim PR comment with per-area verdicts/summaries and a detail link.

    The comment is for humans — short category labels per area so a dev can
    scan in seconds.  Structured data lives on the detail-page API; we only
    embed a tiny metadata tag (decision + detail URL) for downstream tooling.
    """
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────
    decision_display = {
        "auto_approve": ("\u2705", "Auto-Approved"),
        "human_review": ("\U0001f440", "Human Review Required"),
        "reject": ("\u274c", "Changes Requested"),
        "hard_block": ("\U0001f6ab", "Blocked"),
    }
    emoji, label = decision_display.get(result.decision.value, ("\u2753", "Unknown"))
    lines.append(f"## PR Guardian {emoji} {label}")
    lines.append("")

    # ── Metrics ─────────────────────────────────────────────────────
    metrics = (
        f"**Risk** {result.risk_tier.value.upper()} "
        f"\u00b7 **Score** {result.combined_score:.1f}/10 "
        f"\u00b7 **Mechanical** {'passed' if result.mechanical_passed else 'FAILED'}"
    )
    lines.append(metrics)

    # ── Trust tier line ──────────────────────────────────────────
    if result.trust_tier:
        trust_display = _trust_tier_display(result)
        if trust_display:
            lines.append(trust_display)

    lines.append("")

    # ── Per-area verdicts with short category summaries ─────────────
    if result.agent_results:
        verdict_tag = {
            "pass": "\u2705 Pass",
            "warn": "\u26a0\ufe0f Warn",
            "flag_human": "\U0001f50d Review",
        }
        sev_rank = {"critical": 0, "high": 1, "medium": 2}

        for agent in result.agent_results:
            area = _AGENT_LABELS.get(agent.agent_name, agent.agent_name)
            tag = verdict_tag.get(agent.verdict.value, "\u2753")

            # Collect notable findings (medium+) sorted by severity
            notable = [
                f for f in agent.findings
                if f.severity.value in sev_rank
            ]
            notable.sort(key=lambda f: sev_rank[f.severity.value])

            if not notable:
                lines.append(f"- **{area}** \u2014 {tag}")
            else:
                # Use category labels (short) — deduplicate to avoid repetition
                categories = list(dict.fromkeys(f.category for f in notable))
                summary = "; ".join(categories[:3])
                if len(categories) > 3:
                    summary += f" (+{len(categories) - 3} more)"
                lines.append(f"- **{area}** \u2014 {tag}")
                lines.append(f"  {summary}")

        lines.append("")

    # ── Escalation reasons ──────────────────────────────────────────
    if result.override_reasons:
        lines.append(
            "**Escalated:** "
            + " \u00b7 ".join(result.override_reasons)
        )
        lines.append("")

    # ── Detail page link ────────────────────────────────────────────
    detail_url = _detail_url(result.review_id, base_url)
    if detail_url:
        lines.append(
            f"[\U0001f50e Full findings & export for fix \u2192]({detail_url})"
        )
        lines.append("")

    # ── Escalation notice (trust tier escalated by agent findings) ──
    if result.escalated_from:
        lines.append(
            f"> **Trust tier escalated** from {result.escalated_from.upper()} "
            f"to {result.trust_tier.value.upper() if result.trust_tier else '?'} "
            f"based on agent findings."
        )
        lines.append("")

    lines.append("---")
    lines.append("*PR Guardian \u2014 automated review*")

    return "\n".join(lines)



def _trust_tier_display(result: ReviewResult) -> str:
    """Build the trust tier display line for the PR comment."""
    tier = result.trust_tier
    if not tier:
        return ""

    labels = {
        TrustTier.AI_ONLY: "AI-only review",
        TrustTier.SPOT_CHECK: "reviewer spot-check requested",
        TrustTier.MANDATORY_HUMAN: "AI first-pass complete, human approval required",
        TrustTier.HUMAN_PRIMARY: "security team review required",
    }
    return f"**Trust** {tier.value.upper()} \u2014 {labels.get(tier, tier.value)}"


def get_review_labels(result: ReviewResult) -> list[str]:
    """Get labels to apply to the PR based on decision and trust tier."""
    labels: list[str] = []

    # Decision-based labels
    if result.decision == Decision.HARD_BLOCK:
        labels.append("guardian-blocked")
    elif result.decision == Decision.REJECT:
        labels.append("changes-requested")
    elif result.decision == Decision.HUMAN_REVIEW:
        # Trust tier differentiates the human review label
        if result.trust_tier == TrustTier.HUMAN_PRIMARY:
            labels.append("needs-security-review")
        else:
            labels.append("needs-human-review")
    elif result.decision == Decision.AUTO_APPROVE:
        if result.trust_tier == TrustTier.SPOT_CHECK:
            labels.append("guardian-spot-check")
        else:
            labels.append("guardian-approved")

    return labels
