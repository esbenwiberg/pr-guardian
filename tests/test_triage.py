from pr_guardian.config.schema import GuardianConfig, WeightsConfig
from pr_guardian.decision.actions import _AGENT_LABELS
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    FileRole,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
)
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.triage.classifier import ALL_AGENTS, classify
from pathlib import Path


def _make_context(**overrides) -> ReviewContext:
    """Create a minimal ReviewContext for testing."""
    defaults = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB, pr_id="1", repo="test/repo",
            repo_url="", source_branch="feature/test", target_branch="develop",
            author="testuser", title="Test PR", head_commit_sha="abc123",
        ),
        repo_path=Path("/tmp/test"),
        diff=Diff(files=[DiffFile(path="src/main.py", status="modified", additions=10, deletions=5)]),
        changed_files=["src/main.py"],
        lines_changed=15,
        language_map=LanguageMap(languages={"python": ["src/main.py"]}, primary_language="python", language_count=1),
        primary_language="python",
        cross_stack=False,
        repo_config={},
        repo_risk_class=RepoRiskClass.STANDARD,
        hotspots=set(),
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        change_profile=ChangeProfile(
            file_roles={"src/main.py": {FileRole.PRODUCTION}},
            has_production_changes=True,
        ),
    )
    defaults.update(overrides)
    return ReviewContext(**defaults)


class TestTriage:
    def test_trivial_docs_only(self):
        ctx = _make_context(
            changed_files=["README.md"],
            change_profile=ChangeProfile(
                file_roles={"README.md": {FileRole.DOCS}},
                has_docs_only=True,
                skip_agents=True,
            ),
        )
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.TRIVIAL
        assert len(result.agent_set) == 0

    def test_low_simple_change(self):
        ctx = _make_context()
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.LOW
        assert "code_quality_observability" in result.agent_set
        assert "test_quality" in result.agent_set

    def test_high_security_surface(self):
        surface = SecuritySurface()
        surface.classify("src/auth/handler.py", "security_critical")
        ctx = _make_context(
            changed_files=["src/auth/handler.py"],
            security_surface=surface,
            change_profile=ChangeProfile(
                file_roles={"src/auth/handler.py": {FileRole.PRODUCTION}},
                has_production_changes=True,
                touches_security_surface=True,
            ),
        )
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.HIGH
        assert "security_privacy" in result.agent_set

    def test_critical_repo_forces_high(self):
        ctx = _make_context(repo_risk_class=RepoRiskClass.CRITICAL)
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.HIGH

    def test_cross_stack_bumps_tier(self):
        ctx = _make_context(
            cross_stack=True,
            language_map=LanguageMap(
                languages={"python": ["a.py"], "typescript": ["b.ts"]},
                primary_language="python",
                language_count=2,
                cross_stack=True,
            ),
        )
        result = classify(ctx, GuardianConfig())
        # LOW bumped to MEDIUM by cross-stack
        assert result.risk_tier in (RiskTier.MEDIUM, RiskTier.HIGH)

    def test_sql_always_triggers_security(self):
        ctx = _make_context(
            language_map=LanguageMap(
                languages={"python": ["a.py"], "sql": ["b.sql"]},
                primary_language="python",
                language_count=2,
                cross_stack=True,
            ),
        )
        result = classify(ctx, GuardianConfig())
        assert "security_privacy" in result.agent_set


class Test_split_agents:
    def test_intent_has_default_weight_1_0(self):
        cfg = GuardianConfig()
        assert cfg.weights.intent == 1.0

    def test_architecture_has_default_weight_1_0(self):
        cfg = GuardianConfig()
        assert cfg.weights.architecture == 1.0

    def test_architecture_intent_not_in_default_weights(self):
        cfg = GuardianConfig()
        assert not hasattr(cfg.weights, "architecture_intent")

    def test_intent_in_all_agents(self):
        assert "intent" in ALL_AGENTS

    def test_architecture_in_all_agents(self):
        assert "architecture" in ALL_AGENTS

    def test_architecture_intent_not_in_all_agents(self):
        assert "architecture_intent" not in ALL_AGENTS

    def test_intent_has_display_label(self):
        assert "intent" in _AGENT_LABELS

    def test_architecture_has_display_label(self):
        assert "architecture" in _AGENT_LABELS

    def test_architecture_intent_not_in_display_labels(self):
        assert "architecture_intent" not in _AGENT_LABELS


class TestIntentScheduling:
    def test_intent_low_not_scheduled(self):
        """Intent agent must never be in the agent set for a low-risk PR."""
        ctx = _make_context()  # default: LOW risk, single file, no risk signals
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.LOW
        assert "intent" not in result.agent_set

    def test_intent_scheduled_for_medium(self):
        """Intent agent is included in the agent set for medium-risk PRs."""
        from pr_guardian.models.context import BlastRadius
        ctx = _make_context(
            change_profile=ChangeProfile(
                file_roles={"src/main.py": {FileRole.PRODUCTION}},
                has_production_changes=True,
                touches_data_layer=True,  # medium signal
            ),
        )
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.MEDIUM
        assert "intent" in result.agent_set

    def test_intent_scheduled_for_high(self):
        """Intent agent is included in the agent set for high-risk PRs."""
        surface = SecuritySurface()
        surface.classify("src/auth/handler.py", "security_critical")
        ctx = _make_context(
            changed_files=["src/auth/handler.py"],
            security_surface=surface,
            change_profile=ChangeProfile(
                file_roles={"src/auth/handler.py": {FileRole.PRODUCTION}},
                has_production_changes=True,
                touches_security_surface=True,
            ),
        )
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.HIGH
        assert "intent" in result.agent_set

    def test_intent_not_scheduled_when_disabled(self):
        """Intent agent is skipped when intent_verification.enabled=False."""
        from pr_guardian.config.schema import IntentVerificationConfig
        cfg = GuardianConfig()
        cfg.intent_verification = IntentVerificationConfig(enabled=False)
        surface = SecuritySurface()
        surface.classify("src/auth/handler.py", "security_critical")
        ctx = _make_context(
            changed_files=["src/auth/handler.py"],
            security_surface=surface,
            change_profile=ChangeProfile(
                file_roles={"src/auth/handler.py": {FileRole.PRODUCTION}},
                has_production_changes=True,
                touches_security_surface=True,
            ),
        )
        result = classify(ctx, cfg)
        assert "intent" not in result.agent_set
