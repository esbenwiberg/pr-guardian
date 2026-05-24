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

    def test_high_no_agents_human_review(self):
        # Vacuous all() on empty list must NOT auto-approve — guard prevents silent pass-through
        ctx = _make_context()
        result = decide(ctx, [], RiskTier.HIGH, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_high_all_pass_low_score_auto_approve(self):
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.HIGH, GuardianConfig())
        assert result.decision == Decision.AUTO_APPROVE

    def test_high_flag_human_review(self):
        agent = AgentResult(agent_name="security_privacy", verdict=Verdict.FLAG_HUMAN)
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.HIGH, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_high_with_findings_not_auto_approved(self):
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(severity=Severity.HIGH)],
        )
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.HIGH, GuardianConfig())
        assert result.decision in (Decision.HUMAN_REVIEW, Decision.REJECT)

    def test_verdict_explanation_preserved_on_flag_human(self):
        explanation = "SQL injection risk in user input handling. Focus on parameterized query usage."
        agent = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.FLAG_HUMAN,
            findings=[_make_finding(severity=Severity.HIGH)],
            verdict_explanation=explanation,
        )
        ctx = _make_context()
        result = decide(ctx, [agent], RiskTier.HIGH, GuardianConfig())
        assert result.decision in (Decision.HUMAN_REVIEW, Decision.REJECT)
        assert result.agent_results[0].verdict_explanation == explanation

    def test_verdict_explanation_defaults_to_none(self):
        agent = AgentResult(agent_name="test", verdict=Verdict.PASS)
        assert agent.verdict_explanation is None

    def test_blocked_branch_overrides_auto_approve(self):
        ctx = _make_context(
            pr=PlatformPR(
                platform=Platform.GITHUB, pr_id="1", repo="test",
                repo_url="", source_branch="feature", target_branch="release/1.0",
                author="test", title="Test", head_commit_sha="abc",
            ),
        )
        result = decide(ctx, [], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW


class TestSkippedAgent:
    def test_skipped_agent_contributes_no_score(self):
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        score = combined_score([skipped], GuardianConfig())
        assert score == 0.0

    def test_skipped_agent_with_ran_agent_score_unaffected(self):
        ran = AgentResult(
            agent_name="security_privacy",
            verdict=Verdict.WARN,
            findings=[_make_finding(severity=Severity.HIGH)],
        )
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        score_with_skipped = combined_score([ran, skipped], GuardianConfig())
        score_without_skipped = combined_score([ran], GuardianConfig())
        assert score_with_skipped == score_without_skipped

    def test_skipped_agent_not_counted_as_pass_in_matrix(self):
        # If only a skipped agent exists at HIGH tier, it should NOT auto-approve
        # (skipped is not a clean pass; ran_results is empty so guard fires)
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context()
        result = decide(ctx, [skipped], RiskTier.HIGH, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_skipped_agent_remains_in_review_result(self):
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ran = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        ctx = _make_context()
        result = decide(ctx, [ran, skipped], RiskTier.LOW, GuardianConfig())
        names = [r.agent_name for r in result.agent_results]
        assert "architecture" in names
        assert result.agent_results[names.index("architecture")].status == "skipped"

    def test_skipped_agent_does_not_add_finding_reasons(self):
        # A skipped agent with FLAG_HUMAN verdict should not generate finding reasons
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.FLAG_HUMAN,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context()
        result = decide(ctx, [skipped], RiskTier.TRIVIAL, GuardianConfig())
        assert not any("architecture" in r for r in result.finding_reasons)

    def test_skipped_agent_trivial_tier_still_auto_approves(self):
        # TRIVIAL tier returns before the all-skipped guard fires; this is intentional
        # because agents are never scheduled for trivial PRs regardless.
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context()
        result = decide(ctx, [skipped], RiskTier.TRIVIAL, GuardianConfig())
        assert result.decision == Decision.AUTO_APPROVE

    def test_skipped_agent_only_at_low_tier_does_not_auto_approve(self):
        # Vacuous all_pass when ran_results is empty must not auto-approve at LOW tier
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context()
        result = decide(ctx, [skipped], RiskTier.LOW, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_skipped_agent_only_at_medium_tier_does_not_auto_approve(self):
        # Vacuous has_flags=False/has_warns=False when ran_results is empty must not auto-approve
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context()
        result = decide(ctx, [skipped], RiskTier.MEDIUM, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW

    def test_skipped_agent_only_elevated_low_tier_does_not_auto_approve(self):
        # all_pass vacuously True with ran_results empty must not auto-approve at ELEVATED+LOW
        skipped = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        ctx = _make_context(repo_risk_class=RepoRiskClass.ELEVATED)
        result = decide(ctx, [skipped], RiskTier.LOW, GuardianConfig())
        assert result.decision == Decision.HUMAN_REVIEW
