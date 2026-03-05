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

    hotspots: set[str] = field(default_factory=set)
    security_surface: SecuritySurface = field(default_factory=SecuritySurface)
    blast_radius: BlastRadius = field(default_factory=BlastRadius)
    change_profile: ChangeProfile = field(default_factory=ChangeProfile)
