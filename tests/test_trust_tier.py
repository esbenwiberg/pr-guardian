"""Tests for tiered trust: classification, escalation, and decision integration.

These tests verify the core invariants of the trust tier system:
- Path classification resolves to the correct tier with correct fallback layers
- Escalation is one-way upward (never lowers)
- The decision engine respects trust tier governance
- Labels and comments reflect trust tier state
"""
from pathlib import Path

from pr_guardian.config.schema import (
    GuardianConfig,
    SecuritySurfaceConfig,
    TrustTierConfig,
    TrustTierRule,
)
from pr_guardian.decision.actions import build_summary_comment, get_review_labels
from pr_guardian.decision.engine import decide
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
    TrustTier,
    TrustTierResult,
    max_trust_tier,
)
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pr_guardian.triage.trust_classifier import classify_trust_tier
from pr_guardian.triage.trust_escalation import maybe_escalate_trust


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides) -> ReviewContext:
    defaults = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB, pr_id="1", repo="test/repo",
            repo_url="", source_branch="feature", target_branch="develop",
            author="dev", title="Test PR", head_commit_sha="abc",
        ),
        repo_path=Path("/tmp"),
        diff=Diff(),
        changed_files=[],
        lines_changed=0,
        language_map=LanguageMap(),
        primary_language="python",
        cross_stack=False,
        repo_risk_class=RepoRiskClass.STANDARD,
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        change_profile=ChangeProfile(),
    )
    defaults.update(overrides)
    return ReviewContext(**defaults)


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="test",
        language="python",
        file="src/utils.py",
        line=10,
        description="test finding",
        evidence_basis=EvidenceBasis(
            saw_full_context=True, pattern_match=True,
            cwe_id="CWE-89", suggestion_is_concrete=True, cross_references=1,
        ),
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _make_trust_result(**overrides) -> TrustTierResult:
    defaults = dict(
        resolved_tier=TrustTier.SPOT_CHECK,
        file_tiers={},
        triggering_files=[],
        reasons=[],
    )
    defaults.update(overrides)
    return TrustTierResult(**defaults)


# ---------------------------------------------------------------------------
# max_trust_tier utility
# ---------------------------------------------------------------------------

class TestMaxTrustTier:
    def test_returns_higher_governance_tier(self):
        assert max_trust_tier(TrustTier.AI_ONLY, TrustTier.HUMAN_PRIMARY) == TrustTier.HUMAN_PRIMARY
        assert max_trust_tier(TrustTier.HUMAN_PRIMARY, TrustTier.AI_ONLY) == TrustTier.HUMAN_PRIMARY

    def test_same_tier_returns_itself(self):
        assert max_trust_tier(TrustTier.SPOT_CHECK, TrustTier.SPOT_CHECK) == TrustTier.SPOT_CHECK

    def test_adjacent_tiers(self):
        assert max_trust_tier(TrustTier.SPOT_CHECK, TrustTier.MANDATORY_HUMAN) == TrustTier.MANDATORY_HUMAN


# ---------------------------------------------------------------------------
# Trust Tier Classification
# ---------------------------------------------------------------------------

class TestTrustClassifierBuiltinDefaults:
    """Layer 1: Built-in defaults (no config)."""

    def test_docs_file_classified_ai_only(self):
        result = classify_trust_tier(["README.md"], GuardianConfig())
        assert result.file_tiers["README.md"] == TrustTier.AI_ONLY
        assert result.resolved_tier == TrustTier.AI_ONLY

    def test_lock_file_classified_ai_only(self):
        result = classify_trust_tier(["package-lock.json"], GuardianConfig())
        assert result.file_tiers["package-lock.json"] == TrustTier.AI_ONLY

    def test_test_file_classified_spot_check(self):
        result = classify_trust_tier(["tests/test_foo.py"], GuardianConfig())
        assert result.file_tiers["tests/test_foo.py"] == TrustTier.SPOT_CHECK

    def test_auth_file_classified_human_primary(self):
        result = classify_trust_tier(["src/auth/middleware.py"], GuardianConfig())
        assert result.file_tiers["src/auth/middleware.py"] == TrustTier.HUMAN_PRIMARY
        assert result.resolved_tier == TrustTier.HUMAN_PRIMARY

    def test_infra_file_classified_mandatory_human(self):
        result = classify_trust_tier(["infra/terraform/main.tf"], GuardianConfig())
        assert result.file_tiers["infra/terraform/main.tf"] == TrustTier.MANDATORY_HUMAN

    def test_unknown_file_gets_default_tier(self):
        result = classify_trust_tier(["src/app.py"], GuardianConfig())
        assert result.file_tiers["src/app.py"] == TrustTier.SPOT_CHECK

    def test_empty_files_returns_default(self):
        result = classify_trust_tier([], GuardianConfig())
        assert result.resolved_tier == TrustTier.SPOT_CHECK


class TestTrustClassifierMixedPR:
    """PR-level tier = highest across all files."""

    def test_mixed_pr_takes_highest_tier(self):
        """A PR touching both README.md and auth/ should be HUMAN_PRIMARY."""
        files = ["README.md", "src/auth/tokens.py", "src/api/users.py"]
        result = classify_trust_tier(files, GuardianConfig())
        assert result.resolved_tier == TrustTier.HUMAN_PRIMARY
        assert "src/auth/tokens.py" in result.triggering_files
        # Individual files still have their own tiers
        assert result.file_tiers["README.md"] == TrustTier.AI_ONLY
        assert result.file_tiers["src/api/users.py"] == TrustTier.SPOT_CHECK

    def test_all_ai_only_files(self):
        files = ["README.md", "docs/guide.md", "CHANGELOG.md"]
        result = classify_trust_tier(files, GuardianConfig())
        assert result.resolved_tier == TrustTier.AI_ONLY


class TestTrustClassifierExplicitRules:
    """Layer 3: Explicit trust_tiers.rules override everything."""

    def test_explicit_rules_override_builtins(self):
        """A team marks their API dir as mandatory_human (overriding the
        built-in default of spot_check for controllers)."""
        config = GuardianConfig(trust_tiers=TrustTierConfig(
            rules=[
                TrustTierRule(tier="mandatory_human", patterns=["**/api/**"],
                              reason="All API changes need review"),
            ],
        ))
        result = classify_trust_tier(["src/api/users.py"], config)
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN

    def test_explicit_rules_dont_use_builtins(self):
        """When explicit rules are defined, built-in rules are NOT used.
        Files that don't match any explicit rule get the default tier."""
        config = GuardianConfig(trust_tiers=TrustTierConfig(
            rules=[
                TrustTierRule(tier="human_primary", patterns=["**/auth/**"]),
            ],
        ))
        # README.md would be ai_only under builtins, but with explicit rules
        # and no matching rule, it gets the default (spot_check)
        result = classify_trust_tier(["README.md"], config)
        assert result.resolved_tier == TrustTier.SPOT_CHECK


class TestTrustClassifierSurfaceDerivation:
    """Layer 2: Derived from security_surface when no explicit rules exist."""

    def test_custom_surface_derives_tiers(self):
        """Custom security_surface patterns auto-derive trust tiers."""
        config = GuardianConfig(
            security_surface=SecuritySurfaceConfig(
                security_critical=["**/custom-auth/**"],
                infrastructure=["**/deploy/**"],
            ),
        )
        result = classify_trust_tier(
            ["src/custom-auth/handler.py", "deploy/prod.yml"],
            config,
        )
        assert result.file_tiers["src/custom-auth/handler.py"] == TrustTier.HUMAN_PRIMARY
        assert result.file_tiers["deploy/prod.yml"] == TrustTier.MANDATORY_HUMAN

    def test_default_surface_falls_through_to_builtins(self):
        """If security_surface is all defaults, builtins are used instead."""
        config = GuardianConfig()  # default SecuritySurfaceConfig
        result = classify_trust_tier(["src/auth/handler.py"], config)
        # Should match builtin **/auth/** → HUMAN_PRIMARY
        assert result.resolved_tier == TrustTier.HUMAN_PRIMARY


class TestTrustClassifierRepoRiskFloor:
    def test_critical_repo_floors_at_mandatory_human(self):
        """CRITICAL repos can't have trust below MANDATORY_HUMAN."""
        result = classify_trust_tier(
            ["README.md"],  # normally ai_only
            GuardianConfig(),
            repo_risk_class=RepoRiskClass.CRITICAL,
        )
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN

    def test_critical_repo_doesnt_lower_human_primary(self):
        """CRITICAL floor shouldn't lower HUMAN_PRIMARY."""
        result = classify_trust_tier(
            ["src/auth/handler.py"],  # human_primary
            GuardianConfig(),
            repo_risk_class=RepoRiskClass.CRITICAL,
        )
        assert result.resolved_tier == TrustTier.HUMAN_PRIMARY


class TestTrustClassifierReviewerGroup:
    def test_human_primary_sets_reviewer_group(self):
        result = classify_trust_tier(["src/auth/handler.py"], GuardianConfig())
        assert result.reviewer_group_override == "security-team"

    def test_mandatory_human_no_reviewer_group(self):
        result = classify_trust_tier(["infra/terraform/main.tf"], GuardianConfig())
        assert result.reviewer_group_override is None


# ---------------------------------------------------------------------------
# Trust Tier Escalation
# ---------------------------------------------------------------------------

class TestTrustEscalation:
    def test_no_escalation_when_no_triggers(self):
        """Clean agent results shouldn't escalate."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/utils.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.AI_ONLY
        assert not result.escalated

    def test_security_finding_in_low_tier_file_escalates(self):
        """Auth-related finding in an AI_ONLY file should escalate to MANDATORY_HUMAN."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/utils/helpers.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(
                category="auth_bypass",
                severity=Severity.MEDIUM,
                file="src/utils/helpers.py",
            )],
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN
        assert result.escalated
        assert result.file_tiers["src/utils/helpers.py"] == TrustTier.MANDATORY_HUMAN

    def test_low_severity_security_finding_does_not_escalate(self):
        """LOW severity findings should NOT trigger escalation (noise reduction)."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/utils.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(
                category="auth_check",
                severity=Severity.LOW,
                file="src/utils.py",
            )],
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.AI_ONLY
        assert not result.escalated

    def test_flag_human_verdict_escalates(self):
        """Agent verdict FLAG_HUMAN should escalate to MANDATORY_HUMAN."""
        trust = _make_trust_result(resolved_tier=TrustTier.SPOT_CHECK)
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.FLAG_HUMAN)
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN
        assert result.escalated

    def test_critical_detected_finding_escalates(self):
        """Critical severity + DETECTED certainty should escalate regardless of category."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/app.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(
            agent_name="code_quality",
            verdict=Verdict.WARN,
            findings=[_make_finding(
                category="data_corruption",  # not a security keyword
                severity=Severity.CRITICAL,
                certainty=Certainty.DETECTED,
                file="src/app.py",
            )],
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN
        assert result.escalated

    def test_escalation_never_lowers_tier(self):
        """A file already at HUMAN_PRIMARY stays there even with no findings."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.HUMAN_PRIMARY,
            file_tiers={"src/auth/handler.py": TrustTier.HUMAN_PRIMARY},
        )
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.HUMAN_PRIMARY
        assert not result.escalated

    def test_escalation_doesnt_affect_already_high_tier_file(self):
        """Security finding in a file already at MANDATORY_HUMAN shouldn't re-escalate."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.MANDATORY_HUMAN,
            file_tiers={"src/services/billing.py": TrustTier.MANDATORY_HUMAN},
        )
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(
                category="credential_exposure",
                severity=Severity.HIGH,
                file="src/services/billing.py",
            )],
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        # Still MANDATORY_HUMAN (not escalated further since no HUMAN_PRIMARY trigger)
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN
        assert not result.escalated

    def test_non_security_category_does_not_escalate(self):
        """A MEDIUM severity finding with a non-security category should not escalate."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/utils.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(
            agent_name="performance",
            verdict=Verdict.WARN,
            findings=[_make_finding(
                category="n_plus_one_query",
                severity=Severity.MEDIUM,
                file="src/utils.py",
            )],
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert result.resolved_tier == TrustTier.AI_ONLY
        assert not result.escalated

    def test_input_not_mutated(self):
        """The original TrustTierResult should not be modified by escalation."""
        trust = _make_trust_result(
            resolved_tier=TrustTier.AI_ONLY,
            file_tiers={"src/utils.py": TrustTier.AI_ONLY},
        )
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.FLAG_HUMAN,
        )
        result = maybe_escalate_trust(trust, [agent], TrustTierConfig())
        assert trust.resolved_tier == TrustTier.AI_ONLY  # original unchanged
        assert result.resolved_tier == TrustTier.MANDATORY_HUMAN


# ---------------------------------------------------------------------------
# Decision Engine with Trust Tier
# ---------------------------------------------------------------------------

class TestDecisionWithTrustTier:
    def test_mandatory_human_forces_human_review(self):
        """Even when risk tier would auto-approve, MANDATORY_HUMAN blocks."""
        ctx = _make_context()
        trust = _make_trust_result(resolved_tier=TrustTier.MANDATORY_HUMAN)
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig(), trust)
        assert result.decision == Decision.HUMAN_REVIEW
        assert result.trust_tier == TrustTier.MANDATORY_HUMAN

    def test_human_primary_forces_human_review_with_group(self):
        """HUMAN_PRIMARY blocks and sets reviewer_group_override."""
        ctx = _make_context()
        trust = _make_trust_result(
            resolved_tier=TrustTier.HUMAN_PRIMARY,
            reviewer_group_override="security-team",
        )
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig(), trust)
        assert result.decision == Decision.HUMAN_REVIEW
        assert result.reviewer_group_override == "security-team"

    def test_ai_only_allows_auto_approve(self):
        """AI_ONLY tier should not prevent auto-approval."""
        ctx = _make_context()
        trust = _make_trust_result(resolved_tier=TrustTier.AI_ONLY)
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig(), trust)
        assert result.decision == Decision.AUTO_APPROVE
        assert result.trust_tier == TrustTier.AI_ONLY

    def test_spot_check_allows_auto_approve(self):
        """SPOT_CHECK allows auto-approval (reviewers requested separately)."""
        ctx = _make_context()
        trust = _make_trust_result(resolved_tier=TrustTier.SPOT_CHECK)
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig(), trust)
        assert result.decision == Decision.AUTO_APPROVE
        assert result.trust_tier == TrustTier.SPOT_CHECK

    def test_high_risk_overrides_ai_only_trust(self):
        """HIGH risk tier forces human review even for AI_ONLY trust."""
        ctx = _make_context()
        trust = _make_trust_result(resolved_tier=TrustTier.AI_ONLY)
        result = decide(ctx, [], RiskTier.HIGH, GuardianConfig(), trust)
        assert result.decision == Decision.HUMAN_REVIEW

    def test_no_trust_tier_preserves_existing_behavior(self):
        """When no trust tier is provided, decision engine works as before."""
        ctx = _make_context()
        agent = AgentResult(agent_name="code_quality", verdict=Verdict.PASS)
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig())
        assert result.decision == Decision.AUTO_APPROVE
        assert result.trust_tier is None

    def test_escalated_trust_recorded_in_result(self):
        """Escalation metadata should flow through to ReviewResult."""
        ctx = _make_context()
        trust = _make_trust_result(
            resolved_tier=TrustTier.MANDATORY_HUMAN,
            escalated=True,
            escalation_reasons=[
                "Trust tier escalated from ai_only to mandatory_human",
                "Security finding in src/utils.py",
            ],
        )
        result = decide(ctx, [], RiskTier.LOW, GuardianConfig(), trust)
        assert result.escalated_from == "ai_only"


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

class TestLabelsWithTrustTier:
    def test_spot_check_auto_approve_label(self):
        """SPOT_CHECK + AUTO_APPROVE → guardian-spot-check label."""
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.LOW,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.AUTO_APPROVE,
            trust_tier=TrustTier.SPOT_CHECK,
        )
        labels = get_review_labels(result)
        assert "guardian-spot-check" in labels
        assert "guardian-approved" not in labels

    def test_human_primary_label(self):
        """HUMAN_PRIMARY + HUMAN_REVIEW → needs-security-review label."""
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.HIGH,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HUMAN_REVIEW,
            trust_tier=TrustTier.HUMAN_PRIMARY,
        )
        labels = get_review_labels(result)
        assert "needs-security-review" in labels
        assert "needs-human-review" not in labels

    def test_mandatory_human_label(self):
        """MANDATORY_HUMAN + HUMAN_REVIEW → needs-human-review label."""
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.MEDIUM,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HUMAN_REVIEW,
            trust_tier=TrustTier.MANDATORY_HUMAN,
        )
        labels = get_review_labels(result)
        assert "needs-human-review" in labels

    def test_ai_only_auto_approve_label(self):
        """AI_ONLY + AUTO_APPROVE → guardian-approved (standard label)."""
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.LOW,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.AUTO_APPROVE,
            trust_tier=TrustTier.AI_ONLY,
        )
        labels = get_review_labels(result)
        assert "guardian-approved" in labels

    def test_hard_block_label_unchanged(self):
        """Trust tier shouldn't affect HARD_BLOCK label."""
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.HIGH,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HARD_BLOCK,
            trust_tier=TrustTier.MANDATORY_HUMAN,
        )
        labels = get_review_labels(result)
        assert "guardian-blocked" in labels


# ---------------------------------------------------------------------------
# Comment rendering
# ---------------------------------------------------------------------------

class TestCommentWithTrustTier:
    def test_comment_includes_trust_tier_line(self):
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.MEDIUM,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HUMAN_REVIEW,
            trust_tier=TrustTier.MANDATORY_HUMAN,
        )
        comment = build_summary_comment(result)
        assert "MANDATORY_HUMAN" in comment
        assert "human approval required" in comment

    def test_comment_includes_escalation_notice(self):
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.LOW,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HUMAN_REVIEW,
            trust_tier=TrustTier.MANDATORY_HUMAN,
            escalated_from="ai_only",
        )
        comment = build_summary_comment(result)
        assert "Trust tier escalated" in comment
        assert "AI_ONLY" in comment

    def test_comment_without_trust_tier_unchanged(self):
        from pr_guardian.models.output import ReviewResult
        result = ReviewResult(
            pr_id="1", repo="test", risk_tier=RiskTier.LOW,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.AUTO_APPROVE,
        )
        comment = build_summary_comment(result)
        assert "Trust" not in comment
