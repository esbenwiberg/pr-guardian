from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pr_guardian.models.context import RiskTier, RepoRiskClass, TrustTier
from pr_guardian.models.findings import AgentResult


class Decision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"
    HARD_BLOCK = "hard_block"


@dataclass
class MechanicalResult:
    """Result from a single mechanical check."""
    tool: str
    passed: bool
    severity: str = "info"  # info, warning, error
    findings: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class ReviewResult:
    """Final output of the entire review pipeline."""
    pr_id: str
    repo: str
    risk_tier: RiskTier
    repo_risk_class: RepoRiskClass

    review_id: str = ""  # DB UUID, set by orchestrator when persistence is active

    mechanical_results: list[MechanicalResult] = field(default_factory=list)
    mechanical_passed: bool = True

    agent_results: list[AgentResult] = field(default_factory=list)
    combined_score: float = 0.0
    decision: Decision = Decision.HUMAN_REVIEW

    override_reasons: list[str] = field(default_factory=list)
    summary: str = ""
    pipeline_log: list[dict] = field(default_factory=list)

    trust_tier: TrustTier | None = None
    trust_tier_reasons: list[str] = field(default_factory=list)
    trust_tier_files: dict[str, str] = field(default_factory=dict)
    reviewer_group_override: str | None = None
    escalated_from: str | None = None

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: float = 0.0

    # Feedback loop: populated on re-reviews with dismissals
    dismissal_summary: dict | None = None  # {new, resolved, carried_over, dismissed}
