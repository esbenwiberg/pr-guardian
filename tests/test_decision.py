from pr_guardian.config.schema import GuardianConfig
from pr_guardian.decision.engine import (
    agent_score,
    combined_score,
    decide,
    finding_score,
    validated_certainty,
)
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
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
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pathlib import Path


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="test",
        language="python",
        file="test.py",
        line=1,
        description="Test finding",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-89",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _make_context(**overrides) -> ReviewContext:
    defaults = dict(
        pr=PlatformPR(
            platform=Platform.GITHUB, pr_id="1", repo="test/repo",
            repo_url="", source_branch="feature", target_branch="develop",
            author="test", title="Test", head_commit_sha="abc",
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


class TestCertaintyValidation:
    def test_detected_with_evidence_stays(self):
        finding = _make_finding(certainty=Certainty.DETECTED)
        assert validated_certainty(finding, GuardianConfig()) == Certainty.DETECTED

    def test_detected_without_evidence_downgraded(self):
        finding = _make_finding(
            certainty=Certainty.DETECTED,
            evidence_basis=EvidenceBasis(),  # no evidence
        )
        assert validated_certainty(finding, GuardianConfig()) == Certainty.SUSPECTED

    def test_suspected_with_evidence_stays(self):
        finding = _make_finding(
            certainty=Certainty.SUSPECTED,
            evidence_basis=EvidenceBasis(pattern_match=True),
        )
        assert validated_certainty(finding, GuardianConfig()) == Certainty.SUSPECTED

    def test_suspected_without_evidence_downgraded(self):
        finding = _make_finding(
            certainty=Certainty.SUSPECTED,
            evidence_basis=EvidenceBasis(),
        )
        assert validated_certainty(finding, GuardianConfig()) == Certainty.UNCERTAIN


class TestScoring:
    def test_finding_score_detected_medium(self):
        finding = _make_finding(severity=Severity.MEDIUM, certainty=Certainty.DETECTED)
        # SEVERITY_SCORE[medium]=3, CERTAINTY_WEIGHT[detected]=1.0
        assert finding_score(finding, GuardianConfig()) == 3.0

    def test_finding_score_low_uncertain(self):
        finding = _make_finding(
            severity=Severity.LOW,
            certainty=Certainty.UNCERTAIN,
            evidence_basis=EvidenceBasis(),
        )
        # SEVERITY_SCORE[low]=1, CERTAINTY_WEIGHT[uncertain]=0.2
        assert finding_score(finding, GuardianConfig()) == 0.2

    def test_agent_score_no_findings(self):
        result = AgentResult(agent_name="test", verdict=Verdict.PASS)
        assert agent_score(result, GuardianConfig()) == 0.0

    def test_combined_score_single_agent(self):
        result = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(severity=Severity.MEDIUM)],
        )
        score = combined_score([result], GuardianConfig())
        assert score > 0


class TestDecisionMatrix:
    def test_trivial_auto_approve(self):
        ctx = _make_context()
        result = decide(ctx, [], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.AUTO_APPROVE

    def test_trivial_critical_repo_human_review(self):
        ctx = _make_context(repo_risk_class=RepoRiskClass.CRITICAL)
        result = decide(ctx, [], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_low_all_pass_auto_approve(self):
        agent = AgentResult(agent_name="code_quality_observability", verdict=Verdict.PASS)
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig())
        assert result.decision == Decision.AUTO_APPROVE

    def test_low_flag_human_review(self):
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.FLAG_HUMAN)
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.LOW, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_high_always_human_review(self):
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.HIGH, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_blocked_branch_overrides_auto_approve(self):
        ctx = _make_context(
            pr=PlatformPR(
                platform=Platform.GITHUB, pr_id="1", repo="test",
                repo_url="", source_branch="feature", target_branch="main",
                author="test", title="Test", head_commit_sha="abc",
            ),
        )
        result = decide(ctx, [], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW
