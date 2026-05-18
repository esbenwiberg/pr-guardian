"""Tests for the sticky_triggers / finding_reasons split in check_overrides()."""
from pathlib import Path

import pytest

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.decision.engine import check_overrides
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    SecuritySurface,
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
from pr_guardian.models.output import ReviewResult
from pr_guardian.models.pr import Diff, Platform, PlatformPR


def _ctx(**overrides) -> ReviewContext:
    base = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB, pr_id="1", repo="test/repo",
            repo_url="", source_branch="feat", target_branch="main",
            author="dev", title="PR", head_commit_sha="abc",
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
        # no overlap: sticky trigger kinds not in finding_reasons text
        sticky_kinds = {t.kind for t in sticky}
        assert sticky_kinds.isdisjoint({"detected", "suspected", "flagged"})

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
                saw_full_context=True, pattern_match=True,
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
        assert not hasattr(ReviewResult, "override_reasons") or \
               "override_reasons" not in ReviewResult.__dataclass_fields__

    def test_review_result_has_no_trust_tier_reasons(self):
        assert not hasattr(ReviewResult, "trust_tier_reasons") or \
               "trust_tier_reasons" not in ReviewResult.__dataclass_fields__

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
