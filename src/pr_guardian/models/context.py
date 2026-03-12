from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pr_guardian.models.pr import PlatformPR, Diff
from pr_guardian.models.languages import LanguageMap


class RepoRiskClass(str, Enum):
    STANDARD = "standard"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class RiskTier(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FileRole(str, Enum):
    PRODUCTION = "production"
    TEST = "test"
    DOCS = "docs"
    CONFIG = "config"
    INFRA = "infra"
    GENERATED = "generated"
    BUILD = "build"
    DEPENDENCY = "dependency"


class TrustTier(str, Enum):
    AI_ONLY = "ai_only"
    SPOT_CHECK = "spot_check"
    MANDATORY_HUMAN = "mandatory_human"
    HUMAN_PRIMARY = "human_primary"


# Ordered from most trusting to least trusting — used for max() comparisons.
_TRUST_TIER_ORDER = {
    TrustTier.AI_ONLY: 0,
    TrustTier.SPOT_CHECK: 1,
    TrustTier.MANDATORY_HUMAN: 2,
    TrustTier.HUMAN_PRIMARY: 3,
}


def max_trust_tier(a: TrustTier, b: TrustTier) -> TrustTier:
    """Return the *least trusting* (highest governance) of two tiers."""
    return a if _TRUST_TIER_ORDER[a] >= _TRUST_TIER_ORDER[b] else b


@dataclass
class TrustTierResult:
    """Output of trust-tier classification (path-based + optional escalation)."""
    resolved_tier: TrustTier = TrustTier.SPOT_CHECK
    file_tiers: dict[str, TrustTier] = field(default_factory=dict)
    triggering_files: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    reviewer_group_override: str | None = None
    escalated: bool = False
    escalation_reasons: list[str] = field(default_factory=list)


@dataclass
class SecuritySurface:
    """File classifications from security surface patterns."""
    classifications: dict[str, set[str]] = field(default_factory=dict)

    def classify(self, file_path: str, classification: str) -> None:
        self.classifications.setdefault(file_path, set()).add(classification)

    def get_classifications(self, file_path: str) -> set[str]:
        return self.classifications.get(file_path, set())

    def has_hits(self) -> bool:
        return len(self.classifications) > 0


@dataclass
class BlastRadius:
    """Maps changed files to their downstream consumers and propagated risk."""
    consumers: dict[str, set[str]] = field(default_factory=dict)
    propagated_surface: dict[str, set[str]] = field(default_factory=dict)
    touches_shared_code: bool = False
    propagates_to_security: bool = False
    propagates_to_api: bool = False


@dataclass
class ChangeProfile:
    """Semantic classification of what this PR changes."""
    file_roles: dict[str, set[FileRole]] = field(default_factory=dict)

    has_production_changes: bool = False
    has_test_changes: bool = False
    has_docs_only: bool = False
    has_config_only: bool = False
    has_generated_only: bool = False

    touches_security_surface: bool = False
    touches_api_boundary: bool = False
    touches_data_layer: bool = False
    touches_shared_code: bool = False
    adds_dependencies: bool = False
    adds_api_endpoints: bool = False
    crosses_architecture_boundary: bool = False

    implied_agents: set[str] = field(default_factory=set)
    skip_agents: bool = False


@dataclass
class ReviewContext:
    """Built once in Stage 0, consumed by all downstream stages."""
    pr: PlatformPR
    repo_path: Path

    diff: Diff
    changed_files: list[str]
    lines_changed: int

    language_map: LanguageMap
    primary_language: str
    cross_stack: bool

    repo_config: dict = field(default_factory=dict)
    repo_risk_class: RepoRiskClass = RepoRiskClass.STANDARD
    review_guidelines: str = ""

    hotspots: set[str] = field(default_factory=set)
    security_surface: SecuritySurface = field(default_factory=SecuritySurface)
    blast_radius: BlastRadius = field(default_factory=BlastRadius)
    change_profile: ChangeProfile = field(default_factory=ChangeProfile)
    trust_tier_result: TrustTierResult = field(default_factory=TrustTierResult)
