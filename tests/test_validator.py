import pytest

from pr_guardian.decision.validator import _apply_validations, _flatten_findings
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)


def _finding(severity: Severity = Severity.MEDIUM, desc: str = "desc") -> Finding:
    return Finding(
        severity=severity,
        certainty=Certainty.DETECTED,
        category="test",
        language="python",
        file="test.py",
        line=1,
        description=desc,
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            suggestion_is_concrete=True,
        ),
    )


def _agent(name: str, findings: list[Finding], verdict: Verdict = Verdict.WARN) -> AgentResult:
    return AgentResult(agent_name=name, verdict=verdict, findings=findings)


class TestFlattenFindings:
    def test_flatten_multi_agent(self):
        results = [
            _agent("sec", [_finding(desc="a"), _finding(desc="b")]),
            _agent("perf", [_finding(desc="c")]),
        ]
        flat = _flatten_findings(results)
        assert len(flat) == 3
        assert flat[0] == ("sec", 0, results[0].findings[0])
        assert flat[1] == ("sec", 1, results[0].findings[1])
        assert flat[2] == ("perf", 0, results[1].findings[0])

    def test_flatten_empty(self):
        results = [_agent("sec", [])]
        flat = _flatten_findings(results)
        assert flat == []


class TestApplyValidations:
    def test_dismiss_removes_finding(self):
        results = [_agent("sec", [_finding(desc="a"), _finding(desc="b")])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "dismiss", "reason": "False positive"},
            {"index": 1, "action": "keep", "reason": "Real issue"},
        ]
        new_results, dismissed, downgraded = _apply_validations(results, flat, validations)
        assert dismissed == 1
        assert downgraded == 0
        assert len(new_results[0].findings) == 1
        assert new_results[0].findings[0].description == "b"

    def test_downgrade_changes_severity(self):
        results = [_agent("sec", [_finding(severity=Severity.HIGH)])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "downgrade", "reason": "Overstated",
             "downgraded_severity": "medium"},
        ]
        new_results, dismissed, downgraded = _apply_validations(results, flat, validations)
        assert dismissed == 0
        assert downgraded == 1
        assert new_results[0].findings[0].severity == Severity.MEDIUM

    def test_all_dismissed_downgrades_verdict(self):
        results = [_agent("sec", [_finding()], verdict=Verdict.WARN)]
        flat = _flatten_findings(results)
        validations = [{"index": 0, "action": "dismiss", "reason": "Noise"}]
        new_results, _, _ = _apply_validations(results, flat, validations)
        assert new_results[0].verdict == Verdict.PASS
        assert len(new_results[0].findings) == 0

    def test_flag_human_verdict_preserved(self):
        results = [_agent("sec", [_finding()], verdict=Verdict.FLAG_HUMAN)]
        flat = _flatten_findings(results)
        validations = [{"index": 0, "action": "dismiss", "reason": "Noise"}]
        new_results, _, _ = _apply_validations(results, flat, validations)
        assert new_results[0].verdict == Verdict.FLAG_HUMAN

    def test_out_of_range_index_ignored(self):
        results = [_agent("sec", [_finding()])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 99, "action": "dismiss", "reason": "Bad index"},
            {"index": 0, "action": "keep", "reason": "Valid"},
        ]
        new_results, dismissed, _ = _apply_validations(results, flat, validations)
        assert dismissed == 0
        assert len(new_results[0].findings) == 1

    def test_missing_validation_keeps_finding(self):
        """If validator doesn't return an entry for a finding, keep it."""
        results = [_agent("sec", [_finding(desc="a"), _finding(desc="b")])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "dismiss", "reason": "Noise"},
            # index 1 missing
        ]
        new_results, dismissed, _ = _apply_validations(results, flat, validations)
        assert dismissed == 1
        assert len(new_results[0].findings) == 1
        assert new_results[0].findings[0].description == "b"

    def test_invalid_downgrade_severity_ignored(self):
        results = [_agent("sec", [_finding(severity=Severity.HIGH)])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "downgrade", "reason": "Bad",
             "downgraded_severity": "not_a_severity"},
        ]
        new_results, _, downgraded = _apply_validations(results, flat, validations)
        assert downgraded == 0
        assert new_results[0].findings[0].severity == Severity.HIGH

    def test_multi_agent_dismiss_cross_agent(self):
        results = [
            _agent("sec", [_finding(desc="sec-a")]),
            _agent("perf", [_finding(desc="perf-a"), _finding(desc="perf-b")]),
        ]
        flat = _flatten_findings(results)
        # Dismiss sec finding (idx 0) and second perf finding (idx 2)
        validations = [
            {"index": 0, "action": "dismiss", "reason": "FP"},
            {"index": 1, "action": "keep", "reason": "Real"},
            {"index": 2, "action": "dismiss", "reason": "Nitpick"},
        ]
        new_results, dismissed, _ = _apply_validations(results, flat, validations)
        assert dismissed == 2
        assert len(new_results[0].findings) == 0  # sec agent: all dismissed
        assert len(new_results[1].findings) == 1  # perf agent: kept perf-a
        assert new_results[1].findings[0].description == "perf-a"

    def test_originals_not_mutated(self):
        original = _finding(severity=Severity.HIGH)
        results = [_agent("sec", [original])]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "downgrade", "reason": "Lower",
             "downgraded_severity": "low"},
        ]
        _apply_validations(results, flat, validations)
        # Original should be untouched
        assert results[0].findings[0].severity == Severity.HIGH
