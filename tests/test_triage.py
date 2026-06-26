from pr_guardian.config.schema import GuardianConfig
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
from pr_guardian.triage.classifier import classify, is_release_please_branch
from pathlib import Path


def _make_context(**overrides) -> ReviewContext:
    """Create a minimal ReviewContext for testing."""
    defaults = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="1",
            repo="test/repo",
            repo_url="",
            source_branch="feature/test",
            target_branch="develop",
            author="testuser",
            title="Test PR",
            head_commit_sha="abc123",
        ),
        repo_path=Path("/tmp/test"),
        diff=Diff(
            files=[DiffFile(path="src/main.py", status="modified", additions=10, deletions=5)]
        ),
        changed_files=["src/main.py"],
        lines_changed=15,
        language_map=LanguageMap(
            languages={"python": ["src/main.py"]}, primary_language="python", language_count=1
        ),
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


def _make_release_please_context(**profile_overrides) -> ReviewContext:
    """A release-please release PR: bot branch + version-bump churn.

    Models the realistic file set, including .release-please-manifest.json which
    classifies as PRODUCTION by default — the shortcut must still treat this as
    trivial, so has_production_changes is True here on purpose.
    """
    files = [
        "package.json",
        "package-lock.json",
        "CHANGELOG.md",
        ".release-please-manifest.json",
    ]
    profile_defaults = dict(
        file_roles={
            "package.json": {FileRole.DEPENDENCY},
            "package-lock.json": {FileRole.GENERATED},
            "CHANGELOG.md": {FileRole.DOCS},
            ".release-please-manifest.json": {FileRole.PRODUCTION},
        },
        has_production_changes=True,
        changes_dependency_lockfile=True,
    )
    profile_defaults.update(profile_overrides)
    return _make_context(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="42",
            repo="test/repo",
            repo_url="",
            source_branch="release-please--branches--main",
            target_branch="main",
            author="github-actions[bot]",
            title="chore(main): release 1.2.3",
            head_commit_sha="def456",
        ),
        changed_files=files,
        change_profile=ChangeProfile(**profile_defaults),
    )


class TestReleasePleaseShortcut:
    def test_release_please_version_bump_is_trivial(self):
        ctx = _make_release_please_context()
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.TRIVIAL
        assert len(result.agent_set) == 0
        assert any("release-please" in r for r in result.reasons)

    def test_release_please_with_dependency_add_still_escalates(self):
        # A real dependency snuck onto the release branch must NOT auto-pass.
        ctx = _make_release_please_context(adds_dependencies=True)
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.HIGH
        assert "new dependencies added" in result.reasons

    def test_release_please_touching_security_surface_still_escalates(self):
        ctx = _make_release_please_context(touches_security_surface=True)
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier == RiskTier.HIGH

    def test_non_release_branch_same_profile_is_not_shortcut_trivial(self):
        # Identical change set on a normal branch goes through normal scoring.
        ctx = _make_release_please_context()
        object.__setattr__(ctx.pr, "source_branch", "feature/manual-bump")
        result = classify(ctx, GuardianConfig())
        assert result.risk_tier != RiskTier.TRIVIAL


class TestIsReleasePleaseBranch:
    def test_double_dash_prefix(self):
        assert is_release_please_branch("release-please--branches--main")

    def test_slash_prefix(self):
        assert is_release_please_branch("release-please/branches/main")

    def test_unrelated_branch(self):
        assert not is_release_please_branch("feature/release-please-notes")
        assert not is_release_please_branch("main")
