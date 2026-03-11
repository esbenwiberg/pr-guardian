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
    """Build a compact PR comment: decision, key metrics, and a link to the full review.

    All detailed findings live on the review detail page — the PR comment is
    deliberately short so it never hits platform comment size limits.
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

    # ── Trust tier ──────────────────────────────────────────────────
    if result.trust_tier:
        trust_display = _trust_tier_display(result)
        if trust_display:
            lines.append(trust_display)

    # ── Finding counts ──────────────────────────────────────────────
    total_findings = sum(len(a.findings) for a in result.agent_results)
    if total_findings:
        sev_counts: dict[str, int] = {}
        for agent in result.agent_results:
            for f in agent.findings:
                sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1
        parts = []
        for sev in ("critical", "high", "medium", "low"):
            count = sev_counts.get(sev, 0)
            if count:
                parts.append(f"{count} {sev}")
        lines.append("")
        lines.append(f"**{total_findings} finding(s):** {', '.join(parts)}")

    # ── Detail page link ────────────────────────────────────────────
    detail_url = _detail_url(result.review_id, base_url)
    if detail_url:
        lines.append("")
        lines.append(
            f"[\U0001f50e View full findings \u2192]({detail_url})"
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
