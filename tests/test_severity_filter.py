from pr_guardian.config.schema import GuardianConfig, SeverityFloorConfig, SeverityFloorRule
from pr_guardian.decision.severity_filter import filter_findings
from pr_guardian.models.context import RiskTier
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)


def _finding(severity: Severity, certainty: Certainty = Certainty.DETECTED) -> Finding:
    return Finding(
        severity=severity,
        certainty=certainty,
        category="test",
        language="python",
        file="test.py",
        line=1,
        description="desc",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-89",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )


def _agent(name: str, findings: list[Finding], verdict: Verdict = Verdict.WARN) -> AgentResult:
    return AgentResult(agent_name=name, verdict=verdict, findings=findings)


class TestSeverityFloorLowTier:
    """LOW risk tier: suppress all LOW-severity findings by default."""

    def test_low_severity_suppressed(self):
        results = [_agent("code_quality_observability", [
            _finding(Severity.LOW),
            _finding(Severity.MEDIUM),
        ])]
        filtered, count = filter_findings(results, RiskTier.LOW, GuardianConfig())
        assert count == 1
        assert len(filtered[0].findings) == 1
        assert filtered[0].findings[0].severity == Severity.MEDIUM

    def test_medium_severity_kept(self):
        results = [_agent("code_quality_observability", [
            _finding(Severity.MEDIUM),
            _finding(Severity.HIGH),
        ])]
        filtered, count = filter_findings(results, RiskTier.LOW, GuardianConfig())
        assert count == 0
        assert len(filtered[0].findings) == 2

    def test_all_low_suppressed_downgrades_verdict(self):
        results = [_agent("code_quality_observability", [
            _finding(Severity.LOW),
            _finding(Severity.LOW),
        ], verdict=Verdict.WARN)]
        filtered, count = filter_findings(results, RiskTier.LOW, GuardianConfig())
        assert count == 2
        assert filtered[0].verdict == Verdict.PASS
        assert len(filtered[0].findings) == 0

    def test_flag_human_verdict_preserved_even_if_all_suppressed(self):
        results = [_agent("security_privacy", [
            _finding(Severity.LOW),
        ], verdict=Verdict.FLAG_HUMAN)]
        filtered, _ = filter_findings(results, RiskTier.LOW, GuardianConfig())
        assert filtered[0].verdict == Verdict.FLAG_HUMAN

    def test_originals_not_mutated(self):
        original_finding = _finding(Severity.LOW)
        results = [_agent("test", [original_finding])]
        filter_findings(results, RiskTier.LOW, GuardianConfig())
        assert len(results[0].findings) == 1  # original unchanged


class TestSeverityFloorMediumTier:
    """MEDIUM risk tier: suppress LOW+UNCERTAIN findings by default."""

    def test_low_uncertain_suppressed(self):
        results = [_agent("code_quality_observability", [
            _finding(Severity.LOW, Certainty.UNCERTAIN),
            _finding(Severity.LOW, Certainty.DETECTED),
            _finding(Severity.MEDIUM, Certainty.UNCERTAIN),
        ])]
        filtered, count = filter_findings(results, RiskTier.MEDIUM, GuardianConfig())
        assert count == 1  # only LOW+UNCERTAIN suppressed
        assert len(filtered[0].findings) == 2

    def test_low_detected_kept(self):
        results = [_agent("test", [_finding(Severity.LOW, Certainty.DETECTED)])]
        filtered, count = filter_findings(results, RiskTier.MEDIUM, GuardianConfig())
        assert count == 0
        assert len(filtered[0].findings) == 1


class TestSeverityFloorHighTier:
    """HIGH risk tier: no suppression by default."""

    def test_nothing_suppressed(self):
        results = [_agent("test", [
            _finding(Severity.LOW, Certainty.UNCERTAIN),
            _finding(Severity.LOW, Certainty.DETECTED),
        ])]
        filtered, count = filter_findings(results, RiskTier.HIGH, GuardianConfig())
        assert count == 0
        assert len(filtered[0].findings) == 2


class TestSeverityFloorTrivialTier:
    """TRIVIAL tier: no agents run, filter is a no-op."""

    def test_trivial_passthrough(self):
        results = [_agent("test", [_finding(Severity.LOW)])]
        filtered, count = filter_findings(results, RiskTier.TRIVIAL, GuardianConfig())
        assert count == 0
        assert len(filtered[0].findings) == 1


class TestSeverityFloorDisabled:
    def test_disabled_passes_through(self):
        config = GuardianConfig(severity_floor=SeverityFloorConfig(enabled=False))
        results = [_agent("test", [_finding(Severity.LOW)])]
        filtered, count = filter_findings(results, RiskTier.LOW, config)
        assert count == 0
        assert len(filtered[0].findings) == 1


class TestSeverityFloorCustomRules:
    def test_custom_rules(self):
        """Custom rule: suppress MEDIUM+SUSPECTED on LOW tier."""
        config = GuardianConfig(
            severity_floor=SeverityFloorConfig(
                low_tier_suppress=[
                    SeverityFloorRule(severity="low"),
                    SeverityFloorRule(severity="medium", certainty="suspected"),
                ],
            ),
        )
        results = [_agent("test", [
            _finding(Severity.LOW, Certainty.DETECTED),
            _finding(Severity.MEDIUM, Certainty.SUSPECTED),
            _finding(Severity.MEDIUM, Certainty.DETECTED),
        ])]
        filtered, count = filter_findings(results, RiskTier.LOW, config)
        assert count == 2
        assert len(filtered[0].findings) == 1
        assert filtered[0].findings[0].severity == Severity.MEDIUM
        assert filtered[0].findings[0].certainty == Certainty.DETECTED
