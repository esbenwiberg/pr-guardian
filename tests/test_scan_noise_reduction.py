"""Tests for scan-level noise reduction: severity floor + validator."""
from __future__ import annotations

from pr_guardian.config.schema import GuardianConfig, SeverityFloorRule
from pr_guardian.decision.scan_severity_filter import filter_scan_findings
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.scan import ScanAgentResult, ScanFinding


def _finding(sev: str = "low", cert: str = "uncertain", desc: str = "test") -> ScanFinding:
    return ScanFinding(
        severity=Severity(sev),
        certainty=Certainty(cert),
        category="Test",
        file="test.py",
        line=1,
        description=desc,
        agent_name="trend",
        priority=0.3,
    )


def _result(findings: list[ScanFinding], verdict: str = "warn") -> ScanAgentResult:
    return ScanAgentResult(
        agent_name="trend",
        verdict=Verdict(verdict),
        findings=findings,
        summary="test",
    )


class TestScanSeverityFilter:
    def test_suppresses_low_uncertain(self):
        config = GuardianConfig()  # default scan_suppress: low+uncertain
        results = [_result([
            _finding("low", "uncertain"),
            _finding("medium", "detected"),
        ])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 1
        assert len(filtered[0].findings) == 1
        assert filtered[0].findings[0].severity == Severity.MEDIUM

    def test_keeps_low_detected(self):
        config = GuardianConfig()
        results = [_result([_finding("low", "detected")])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 0
        assert len(filtered[0].findings) == 1

    def test_keeps_medium_uncertain(self):
        config = GuardianConfig()
        results = [_result([_finding("medium", "uncertain")])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 0
        assert len(filtered[0].findings) == 1

    def test_downgrades_verdict_when_all_suppressed(self):
        config = GuardianConfig()
        results = [_result([_finding("low", "uncertain")], verdict="warn")]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 1
        assert filtered[0].verdict == Verdict.PASS
        assert len(filtered[0].findings) == 0

    def test_preserves_flag_human_verdict(self):
        config = GuardianConfig()
        results = [_result([_finding("low", "uncertain")], verdict="flag_human")]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 1
        assert filtered[0].verdict == Verdict.FLAG_HUMAN

    def test_disabled_returns_unmodified(self):
        config = GuardianConfig()
        config.severity_floor.enabled = False
        results = [_result([_finding("low", "uncertain")])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 0
        assert len(filtered[0].findings) == 1

    def test_custom_rules(self):
        config = GuardianConfig()
        config.severity_floor.scan_suppress = [
            SeverityFloorRule(severity="medium", certainty="suspected"),
        ]
        results = [_result([
            _finding("low", "uncertain"),
            _finding("medium", "suspected"),
            _finding("high", "detected"),
        ])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 1
        assert len(filtered[0].findings) == 2
        assert filtered[0].findings[0].severity == Severity.LOW
        assert filtered[0].findings[1].severity == Severity.HIGH

    def test_empty_rules_no_suppression(self):
        config = GuardianConfig()
        config.severity_floor.scan_suppress = []
        results = [_result([_finding("low", "uncertain")])]
        filtered, suppressed = filter_scan_findings(results, config)
        assert suppressed == 0
