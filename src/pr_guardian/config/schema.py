from __future__ import annotations

from pydantic import BaseModel, Field


class LLMProviderConfig(BaseModel):
    type: str  # "anthropic", "azure-openai", "openai-compatible"
    api_key_env: str = ""
    endpoint_env: str = ""
    base_url: str = ""
    api_key: str = ""
    default_model: str = "claude-sonnet-4-6"
    models: list[str] = Field(default_factory=list)


class AgentOverride(BaseModel):
    model: str | None = None


class LLMConfig(BaseModel):
    default_provider: str = "anthropic"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_seconds: int = 120
    agent_overrides: dict[str, AgentOverride] = Field(default_factory=dict)


class HumanReviewConfig(BaseModel):
    reviewer_group: str = "Developers"


class ThresholdsConfig(BaseModel):
    auto_approve_max_score: float = 4.0
    human_review_min_score: float = 4.0
    hard_block_score: float = 8.0


class WeightsConfig(BaseModel):
    security_privacy: float = 3.0
    test_quality: float = 2.5
    architecture_intent: float = 2.0
    performance: float = 1.5
    hotspot: float = 1.5
    code_quality_observability: float = 1.0


class CertaintyValidationConfig(BaseModel):
    detected_min_signals: int = 2
    suspected_min_signals: int = 1


class AgentContextThresholds(BaseModel):
    compact: int = 100
    standard: int = 500
    deep: int = 500


class TriageConfig(BaseModel):
    agent_context_thresholds: AgentContextThresholds = Field(default_factory=AgentContextThresholds)


class AutoApproveConfig(BaseModel):
    enabled: bool = True
    allowed_target_branches: list[str] = Field(default_factory=lambda: ["develop", "feature/*"])
    blocked_target_branches: list[str] = Field(
        default_factory=lambda: ["main", "master", "release/*"]
    )
    require_all_checks_pass: bool = True


class AgentsConfig(BaseModel):
    max_context_tokens: int = 120_000
    timeout_seconds: int = 120


class IntentVerificationConfig(BaseModel):
    enabled: bool = True
    work_item_source: str = "auto"
    require_linked_work_item: bool = False


class PrivacyConfig(BaseModel):
    data_classification_file: str = "data-classification.yml"
    compliance_frameworks: list[str] = Field(default_factory=lambda: ["gdpr"])


class TestQualityConfig(BaseModel):
    min_assertion_quality_score: float = 0.5
    max_untested_path_ratio: float = 0.5


class FeedbackConfig(BaseModel):
    enabled: bool = True
    log_all_decisions: bool = True
    override_tracking: bool = True
    weekly_report: bool = True


class PathRiskEntry(BaseModel):
    pattern: str
    min_tier: str = ""
    max_tier: str = ""
    reason: str = ""
    condition: str = ""


class PathRiskConfig(BaseModel):
    critical_paths: list[PathRiskEntry] = Field(default_factory=list)
    safe_paths: list[PathRiskEntry] = Field(default_factory=list)
    critical_consumers: dict[str, list[str]] = Field(default_factory=dict)


class FileRolesConfig(BaseModel):
    test_patterns: list[str] = Field(
        default_factory=lambda: ["**/tests/**", "**/*.test.*", "**/*.spec.*"]
    )
    docs_patterns: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/docs/**", "CHANGELOG*"]
    )
    generated_patterns: list[str] = Field(
        default_factory=lambda: ["**/migrations/**", "**/package-lock.json", "**/*.lock"]
    )
    build_patterns: list[str] = Field(
        default_factory=lambda: ["**/Dockerfile*", "**/Makefile", "**/*.csproj"]
    )


class SecuritySurfaceConfig(BaseModel):
    security_critical: list[str] = Field(
        default_factory=lambda: ["**/auth/**", "**/crypto/**", "**/middleware/auth*"]
    )
    input_handling: list[str] = Field(
        default_factory=lambda: ["**/controllers/**", "**/api/**", "**/handlers/**"]
    )
    data_access: list[str] = Field(
        default_factory=lambda: ["**/repositories/**", "**/models/**", "**/queries/**"]
    )
    configuration: list[str] = Field(
        default_factory=lambda: ["**/config/**", "**/.env*", "**/settings*"]
    )
    infrastructure: list[str] = Field(
        default_factory=lambda: ["**/terraform/**", "**/docker/**", "**/k8s/**"]
    )


class RecentChangesConfig(BaseModel):
    time_window_days: int = 7
    branch: str = "main"
    max_commits: int = 200
    group_by: str = "module"  # module, author, area


class MaintenanceConfig(BaseModel):
    staleness_months: int = 6
    max_files: int = 100
    exclude_patterns: list[str] = Field(
        default_factory=lambda: ["**/migrations/**", "**/*.lock", "**/node_modules/**"]
    )
    include_patterns: list[str] = Field(default_factory=list)


class GuardianConfig(BaseModel):
    """Top-level config: merged from service defaults + per-repo review.yml."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    repo_risk_class: str = "standard"
    human_review: HumanReviewConfig = Field(default_factory=HumanReviewConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    certainty_validation: CertaintyValidationConfig = Field(
        default_factory=CertaintyValidationConfig
    )
    triage: TriageConfig = Field(default_factory=TriageConfig)
    auto_approve: AutoApproveConfig = Field(default_factory=AutoApproveConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    intent_verification: IntentVerificationConfig = Field(
        default_factory=IntentVerificationConfig
    )
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    test_quality: TestQualityConfig = Field(default_factory=TestQualityConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    path_risk: PathRiskConfig = Field(default_factory=PathRiskConfig)
    file_roles: FileRolesConfig = Field(default_factory=FileRolesConfig)
    security_surface: SecuritySurfaceConfig = Field(default_factory=SecuritySurfaceConfig)
    recent_changes: RecentChangesConfig = Field(default_factory=RecentChangesConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
