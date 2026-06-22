"""Tests for the sticky_triggers / finding_reasons split in check_overrides()."""

from pathlib import Path


from pr_guardian.config.schema import GuardianConfig
from pr_guardian.decision.engine import check_overrides, decide
from pr_guardian.persistence.storage import _unpack_override_reasons
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
    TrustTier,
    TrustTierResult,
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
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import Diff, Platform, PlatformPR


def _ctx(**overrides) -> ReviewContext:
    base = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="1",
            repo="test/repo",
            repo_url="",
            source_branch="feat",
            target_branch="main",
            author="dev",
            title="PR",
            head_commit_sha="abc",
        ),
        repo_path=Path("/tmp"),
        diff=Diff(),
        changed_files=[],
        lines_changed=10,
        language_map=LanguageMap(),
        primary_language="python",
        cross_stack=False,
        repo_risk_class=RepoRiskClass.STANDARD,
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        change_profile=ChangeProfile(),
        hotspots=set(),
    )
    base.update(overrides)
    return ReviewContext(**base)


def _high_sev_finding() -> Finding:
    return Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="security",
        language="python",
        file="auth.py",
        line=1,
        description="hardcoded secret",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-798",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )


def _agent_result(findings=None, verdict=Verdict.PASS) -> AgentResult:
    return AgentResult(
        agent_name="security_privacy",
        verdict=verdict,
        findings=findings or [],
    )


CONFIG = GuardianConfig()


class TestBucketSplit:
    def test_new_dep_only(self):
        profile = ChangeProfile(adds_dependencies=True)
        ctx = _ctx(change_profile=profile)
        sticky, finding_reasons = check_overrides([], ctx, CONFIG)

        assert len(sticky) == 1
        assert sticky[0].kind == "new_dep"
        assert finding_reasons == []

    def test_high_sev_finding_only(self):
        ctx = _ctx()
        agent = _agent_result(findings=[_high_sev_finding()])
        sticky, finding_reasons = check_overrides([agent], ctx, CONFIG)

        assert sticky == []
        assert len(finding_reasons) >= 1
        assert any("detected" in r for r in finding_reasons)

    def test_both_new_dep_and_high_sev(self):
        profile = ChangeProfile(adds_dependencies=True)
        ctx = _ctx(change_profile=profile)
        agent = _agent_result(findings=[_high_sev_finding()])
        sticky, finding_reasons = check_overrides([agent], ctx, CONFIG)

        assert len(sticky) >= 1
        assert any(t.kind == "new_dep" for t in sticky)
        assert len(finding_reasons) >= 1
        # No overlap: every sticky trigger's reason/label text must NOT appear
        # in any finding_reason, and no finding_reason text should describe a
        # structural condition (e.g. dependency-related wording belongs only to
        # the sticky bucket). Each escalation reason lives in exactly one bucket.
        for trigger in sticky:
            for fr in finding_reasons:
                assert trigger.reason not in fr
                assert trigger.label not in fr
        assert all("dependency" not in fr.lower() for fr in finding_reasons)
        sticky_text_blob = " ".join(t.reason + " " + t.label for t in sticky).lower()
        assert "detected certainty" not in sticky_text_blob
        assert "suspected findings" not in sticky_text_blob

    def test_clean_pr(self):
        ctx = _ctx()
        sticky, finding_reasons = check_overrides([], ctx, CONFIG)

        assert sticky == []
        assert finding_reasons == []

    def test_flag_human_goes_to_finding_reasons(self):
        ctx = _ctx()
        agent = _agent_result(verdict=Verdict.FLAG_HUMAN)
        sticky, finding_reasons = check_overrides([agent], ctx, CONFIG)

        assert sticky == []
        assert any("flagged for human review" in r for r in finding_reasons)

    def test_flag_human_bare_says_no_finding_cited(self):
        ctx = _ctx()
        agent = _agent_result(verdict=Verdict.FLAG_HUMAN)
        _, finding_reasons = check_overrides([agent], ctx, CONFIG)
        assert any("no specific finding cited" in r for r in finding_reasons)

    def test_flag_human_with_error_reads_as_degraded_run(self):
        ctx = _ctx()
        agent = AgentResult(
            agent_name="hotspot",
            verdict=Verdict.FLAG_HUMAN,
            error="Invalid JSON response from LLM",
        )
        _, finding_reasons = check_overrides([agent], ctx, CONFIG)
        # An errored agent did not *judge* the PR — the reason must say so, not
        # masquerade as a finding signal.
        assert any(
            "could not complete" in r and "safety fallback" in r for r in finding_reasons
        )

    def test_flag_human_with_explanation_surfaces_reasoning(self):
        ctx = _ctx()
        agent = AgentResult(
            agent_name="test_quality",
            verdict=Verdict.FLAG_HUMAN,
            verdict_explanation="No new tests cover the rAF timing path.",
        )
        _, finding_reasons = check_overrides([agent], ctx, CONFIG)
        assert any("No new tests cover the rAF timing path." in r for r in finding_reasons)

    def test_suspected_threshold_goes_to_finding_reasons(self):
        suspected_finding = Finding(
            severity=Severity.MEDIUM,
            certainty=Certainty.SUSPECTED,
            category="quality",
            language="python",
            file="main.py",
            line=1,
            description="possible issue",
            evidence_basis=EvidenceBasis(
                saw_full_context=True,
                pattern_match=True,
                suggestion_is_concrete=True,
            ),
        )
        agent = _agent_result(findings=[suspected_finding] * 3)
        ctx = _ctx()
        sticky, finding_reasons = check_overrides([agent], ctx, CONFIG)

        assert sticky == []
        assert any("suspected" in r for r in finding_reasons)

    def test_hotspot_goes_to_sticky_triggers(self):
        ctx = _ctx(changed_files=["src/auth/login.py"], hotspots={"src/auth/login.py"})
        sticky, finding_reasons = check_overrides([], ctx, CONFIG)

        assert any(t.kind == "hotspot" for t in sticky)
        assert finding_reasons == []

    def test_elevated_repo_risk_goes_to_sticky_triggers(self):
        ctx = _ctx(repo_risk_class=RepoRiskClass.ELEVATED)
        sticky, finding_reasons = check_overrides([], ctx, CONFIG)

        assert any(t.kind == "repo_risk" for t in sticky)
        assert finding_reasons == []

    def test_security_surface_goes_to_sticky_triggers(self):
        surface = SecuritySurface()
        surface.classify("src/auth/secrets.py", "credentials")
        ctx = _ctx(security_surface=surface)
        sticky, finding_reasons = check_overrides([], ctx, CONFIG)

        assert any(t.kind == "path_risk" for t in sticky)
        assert finding_reasons == []


class TestBreakCleanlyInvariant:
    def test_review_result_has_no_override_reasons(self):
        assert (
            not hasattr(ReviewResult, "override_reasons")
            or "override_reasons" not in ReviewResult.__dataclass_fields__
        )

    def test_review_result_has_no_trust_tier_reasons(self):
        assert (
            not hasattr(ReviewResult, "trust_tier_reasons")
            or "trust_tier_reasons" not in ReviewResult.__dataclass_fields__
        )

    def test_review_result_has_sticky_triggers(self):
        assert "sticky_triggers" in ReviewResult.__dataclass_fields__

    def test_review_result_has_finding_reasons(self):
        assert "finding_reasons" in ReviewResult.__dataclass_fields__

    def test_sticky_trigger_is_dataclass(self):
        t = StickyTrigger(
            kind="new_dep",
            label="New dependency added",
            source="requests==2.32.3",
            reason="PR introduces a new external dependency",
        )
        assert t.kind == "new_dep"
        assert t.source == "requests==2.32.3"


class TestUnpackOverrideReasons:
    """Unit tests for the three code paths in _unpack_override_reasons()."""

    def test_new_dict_format(self):
        raw = {
            "sticky_triggers": [{"kind": "new_dep", "label": "l", "source": "s", "reason": "r"}],
            "finding_reasons": ["3 high-sev findings"],
        }
        out = _unpack_override_reasons(raw)
        assert out["sticky_triggers"] == raw["sticky_triggers"]
        assert out["finding_reasons"] == ["3 high-sev findings"]

    def test_empty_dict_format(self):
        out = _unpack_override_reasons({"sticky_triggers": [], "finding_reasons": []})
        assert out == {"sticky_triggers": [], "finding_reasons": [], "gate_read": None}

    def test_legacy_list_format(self):
        raw = ["Old override reason A", "Old override reason B"]
        out = _unpack_override_reasons(raw)
        assert out["sticky_triggers"] == []
        assert out["finding_reasons"] == raw

    def test_none_input(self):
        out = _unpack_override_reasons(None)
        assert out == {"sticky_triggers": [], "finding_reasons": [], "gate_read": None}

    def test_unexpected_type_returns_empty(self):
        out = _unpack_override_reasons("unexpected string")
        assert out == {"sticky_triggers": [], "finding_reasons": [], "gate_read": None}


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestDashboardFieldCleanness:
    """Verify that the dashboard API layer does not reference removed fields."""

    def test_dashboard_py_has_no_override_reasons(self):
        content = (REPO_ROOT / "src/pr_guardian/api/dashboard.py").read_text()
        assert "override_reasons" not in content

    def test_dashboard_py_has_no_trust_tier_reasons(self):
        content = (REPO_ROOT / "src/pr_guardian/api/dashboard.py").read_text()
        assert "trust_tier_reasons" not in content


class TestTrustTierStickyTrigger:
    """Verify the trust_tier sticky trigger emitted inside decide()."""

    def test_mandatory_human_emits_trust_tier_sticky(self):
        ctx = _ctx()
        ttr = TrustTierResult(resolved_tier=TrustTier.MANDATORY_HUMAN)
        result = decide(
            context=ctx,
            agent_results=[],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        trust_tier_triggers = [t for t in result.sticky_triggers if t.kind == "trust_tier"]
        assert len(trust_tier_triggers) == 1
        assert trust_tier_triggers[0].source == TrustTier.MANDATORY_HUMAN.value
        assert result.decision == Decision.HUMAN_REVIEW

    def test_human_primary_emits_trust_tier_sticky(self):
        ctx = _ctx()
        ttr = TrustTierResult(
            resolved_tier=TrustTier.HUMAN_PRIMARY,
            reviewer_group_override="security-team",
        )
        result = decide(
            context=ctx,
            agent_results=[],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        trust_tier_triggers = [t for t in result.sticky_triggers if t.kind == "trust_tier"]
        assert len(trust_tier_triggers) == 1
        assert trust_tier_triggers[0].source == TrustTier.HUMAN_PRIMARY.value
        assert result.decision == Decision.HUMAN_REVIEW
        assert result.reviewer_group_override == "security-team"

    def test_ai_only_does_not_emit_trust_tier_sticky(self):
        ctx = _ctx()
        ttr = TrustTierResult(resolved_tier=TrustTier.AI_ONLY)
        result = decide(
            context=ctx,
            agent_results=[],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        assert all(t.kind != "trust_tier" for t in result.sticky_triggers)
        assert result.decision == Decision.AUTO_APPROVE

    def test_spot_check_does_not_emit_trust_tier_sticky(self):
        ctx = _ctx()
        ttr = TrustTierResult(resolved_tier=TrustTier.SPOT_CHECK)
        result = decide(
            context=ctx,
            agent_results=[],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        assert all(t.kind != "trust_tier" for t in result.sticky_triggers)
        assert result.decision == Decision.AUTO_APPROVE

    def test_mandatory_human_emits_sticky_even_with_findings(self):
        # When findings already escalate to HUMAN_REVIEW, the trust_tier sticky
        # trigger must still be recorded so the structural audit panel reflects
        # the restrictive tier.
        ctx = _ctx()
        agent = _agent_result(findings=[_high_sev_finding()])
        ttr = TrustTierResult(resolved_tier=TrustTier.MANDATORY_HUMAN)
        result = decide(
            context=ctx,
            agent_results=[agent],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        trust_tier_triggers = [t for t in result.sticky_triggers if t.kind == "trust_tier"]
        assert len(trust_tier_triggers) == 1
        assert trust_tier_triggers[0].source == TrustTier.MANDATORY_HUMAN.value
        assert len(result.finding_reasons) >= 1

    def test_human_primary_emits_sticky_even_with_findings(self):
        ctx = _ctx()
        agent = _agent_result(verdict=Verdict.FLAG_HUMAN)
        ttr = TrustTierResult(
            resolved_tier=TrustTier.HUMAN_PRIMARY,
            reviewer_group_override="security-team",
        )
        result = decide(
            context=ctx,
            agent_results=[agent],
            risk_tier=RiskTier.LOW,
            config=CONFIG,
            trust_tier_result=ttr,
        )

        trust_tier_triggers = [t for t in result.sticky_triggers if t.kind == "trust_tier"]
        assert len(trust_tier_triggers) == 1
        assert result.reviewer_group_override == "security-team"
