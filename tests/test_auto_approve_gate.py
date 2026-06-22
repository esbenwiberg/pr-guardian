"""Auto-approve unlock gate, archmap-retires-globs, and dependency policy."""

from pathlib import Path

import pytest

from pr_guardian.config.schema import (
    DependencyPolicyConfig,
    GuardianConfig,
    TrustTierConfig,
    TrustTierRule,
)
from pr_guardian.decision.engine import decide
from pr_guardian.models.context import (
    ArchmapContext,
    ArchmapFile,
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
)
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pr_guardian.triage.trust_classifier import classify_trust_tier


def _archmap(classification: str = "leaf") -> ArchmapContext:
    return ArchmapContext(
        files={
            "src/app.py": ArchmapFile(
                path="src/app.py",
                classification=classification,
                ca=0,
                tca=0,
                instability=0.0,
                risk=0,
                overridden=False,
                reason=classification,
            )
        }
    )


def _ctx(*, archmap=None, changed_files=None, change_profile=None, **overrides) -> ReviewContext:
    defaults = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="1",
            repo="test/repo",
            repo_url="",
            source_branch="feature",
            target_branch="develop",
            author="dev",
            title="Test PR",
            head_commit_sha="abc",
        ),
        repo_path=Path("/tmp"),
        diff=Diff(),
        changed_files=changed_files or [],
        lines_changed=0,
        language_map=LanguageMap(),
        primary_language="python",
        cross_stack=False,
        repo_risk_class=RepoRiskClass.STANDARD,
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        change_profile=change_profile or ChangeProfile(),
        archmap=archmap if archmap is not None else ArchmapContext(),
    )
    defaults.update(overrides)
    return ReviewContext(**defaults)


# --------------------------------------------------------------------------- #
# Unlock gate
# --------------------------------------------------------------------------- #


class TestAutoApproveUnlockGate:
    def test_locked_without_rules_or_archmap_forces_human(self):
        ctx = _ctx()  # no archmap, default config (no explicit trust rules)
        result = decide(ctx, [], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW
        assert result.auto_approve_unlocked is False
        assert any("locked" in r.lower() for r in result.finding_reasons)

    def test_unlocked_by_explicit_trust_rules(self):
        config = GuardianConfig(
            trust_tiers=TrustTierConfig(
                default_tier="ai_only",
                rules=[TrustTierRule(tier="human_primary", patterns=["**/auth/**"])],
            )
        )
        trust = classify_trust_tier(["src/app.py"], config)
        ctx = _ctx(changed_files=["src/app.py"])
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE
        assert result.auto_approve_unlocked is True

    def test_unlocked_by_archmap_clean_leaf(self):
        ctx = _ctx(archmap=_archmap("leaf"), changed_files=["src/app.py"])
        config = GuardianConfig()
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE
        assert result.auto_approve_unlocked is True

    def test_archmap_hub_still_escalates(self):
        ctx = _ctx(archmap=_archmap("hub"), changed_files=["src/app.py"])
        config = GuardianConfig()
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.HUMAN_REVIEW
        assert any(t.kind == "archmap_hub" for t in result.sticky_triggers)


# --------------------------------------------------------------------------- #
# Archmap retires the built-in path globs
# --------------------------------------------------------------------------- #


class TestArchmapRetiresGlobs:
    def test_config_path_no_longer_floors_to_mandatory_human(self):
        # **/config/** is a built-in MANDATORY_HUMAN glob. With archmap available
        # and no explicit rules, the glob must not apply.
        config = GuardianConfig()
        no_archmap = classify_trust_tier(["src/config/settings.py"], config)
        with_archmap = classify_trust_tier(
            ["src/config/settings.py"], config, archmap_available=True
        )
        assert no_archmap.resolved_tier.value == "mandatory_human"
        assert with_archmap.resolved_tier.value == "spot_check"  # default tier

    def test_config_path_auto_approves_with_archmap(self):
        config = GuardianConfig()
        trust = classify_trust_tier(["src/config/settings.py"], config, archmap_available=True)
        # security_surface on the context is empty here (no computed hit), so the
        # only thing that could escalate is the (now-retired) trust-tier glob.
        ctx = _ctx(archmap=_archmap("leaf"), changed_files=["src/config/settings.py"])
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE

    def test_explicit_rules_win_over_archmap(self):
        config = GuardianConfig(
            trust_tiers=TrustTierConfig(
                rules=[TrustTierRule(tier="mandatory_human", patterns=["**/config/**"])]
            )
        )
        trust = classify_trust_tier(["src/config/settings.py"], config, archmap_available=True)
        assert trust.resolved_tier.value == "mandatory_human"


# --------------------------------------------------------------------------- #
# Dependency policy
# --------------------------------------------------------------------------- #


def _dep_profile(**kwargs) -> ChangeProfile:
    return ChangeProfile(**kwargs)


class TestDependencyPolicy:
    @pytest.mark.parametrize(
        "profile_kwargs,expect_locked",
        [
            (dict(adds_dependencies=True), True),
            (dict(changes_dependency_lockfile=True), True),
            (dict(removes_dependencies=True), True),
        ],
    )
    def test_dependency_changes_escalate_by_default(self, profile_kwargs, expect_locked):
        config = GuardianConfig()
        ctx = _ctx(
            archmap=_archmap("leaf"),
            changed_files=["src/app.py"],
            change_profile=_dep_profile(**profile_kwargs),
        )
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.HUMAN_REVIEW
        assert any(t.kind == "new_dep" for t in result.sticky_triggers)

    def test_require_human_off_does_not_escalate(self):
        config = GuardianConfig(dependency_policy=DependencyPolicyConfig(require_human=False))
        ctx = _ctx(
            archmap=_archmap("leaf"),
            changed_files=["src/app.py"],
            change_profile=_dep_profile(adds_dependencies=True),
        )
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE
        assert not any(t.kind == "new_dep" for t in result.sticky_triggers)

    def test_lockfiles_excluded_when_disabled(self):
        config = GuardianConfig(dependency_policy=DependencyPolicyConfig(include_lockfiles=False))
        ctx = _ctx(
            archmap=_archmap("leaf"),
            changed_files=["src/app.py"],
            change_profile=_dep_profile(changes_dependency_lockfile=True),
        )
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE

    def test_removals_excluded_when_disabled(self):
        config = GuardianConfig(dependency_policy=DependencyPolicyConfig(include_removals=False))
        ctx = _ctx(
            archmap=_archmap("leaf"),
            changed_files=["src/app.py"],
            change_profile=_dep_profile(removes_dependencies=True),
        )
        trust = classify_trust_tier(["src/app.py"], config, archmap_available=True)
        result = decide(ctx, [], RiskTier.TRIVIAL, config, trust)
        assert result.decision == Decision.AUTO_APPROVE
