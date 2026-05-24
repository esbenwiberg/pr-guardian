import pytest

from pr_guardian.decision.dedup import (
    _jaccard,
    _lines_are_close,
    _tokenize_category,
    cluster_potential_duplicates,
    merge_findings,
)
from pr_guardian.decision.validator import _apply_validations, _flatten_findings
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)


def _finding(
    file: str = "handler.py",
    line: int | None = 10,
    category: str = "input-validation",
    severity: Severity = Severity.MEDIUM,
    certainty: Certainty = Certainty.SUSPECTED,
    desc: str = "Missing input validation",
    cwe: str | None = None,
    evidence: EvidenceBasis | None = None,
) -> Finding:
    return Finding(
        severity=severity,
        certainty=certainty,
        category=category,
        language="python",
        file=file,
        line=line,
        description=desc,
        suggestion="Add validation",
        cwe=cwe,
        evidence_basis=evidence or EvidenceBasis(),
    )


def _agent(name: str, findings: list[Finding]) -> AgentResult:
    return AgentResult(agent_name=name, verdict=Verdict.WARN, findings=findings)


# ---------------------------------------------------------------------------
# Tokenization & Jaccard
# ---------------------------------------------------------------------------


class TestTokenizeCategory:
    def test_basic(self):
        assert _tokenize_category("input-validation") == {"input", "validation"}

    def test_underscores_and_dots(self):
        assert _tokenize_category("input_validation.check") == {"input", "validation", "check"}

    def test_single_char_tokens_dropped(self):
        assert _tokenize_category("a-big-b") == {"big"}


class TestJaccard:
    def test_identical(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b"}) == pytest.approx(2 / 3)

    def test_empty(self):
        assert _jaccard(set(), {"a"}) == 0.0


# ---------------------------------------------------------------------------
# Line proximity
# ---------------------------------------------------------------------------


class TestLinesAreClose:
    def test_within_threshold(self):
        assert _lines_are_close(10, 13, 5) is True

    def test_at_threshold(self):
        assert _lines_are_close(10, 15, 5) is True

    def test_beyond_threshold(self):
        assert _lines_are_close(10, 20, 5) is False

    def test_both_none(self):
        assert _lines_are_close(None, None, 5) is True

    def test_one_none(self):
        assert _lines_are_close(10, None, 5) is False
        assert _lines_are_close(None, 10, 5) is False


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


class TestClusterPotentialDuplicates:
    def test_same_file_nearby_lines_similar_category(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
            ("cq", 0, _finding(file="a.py", line=12, category="input_validation")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert len(clusters) == 2
        assert clusters[0] == clusters[1]

    def test_different_files_not_clustered(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
            ("cq", 0, _finding(file="b.py", line=10, category="input-validation")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert clusters == {}

    def test_far_apart_lines_not_clustered(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
            ("cq", 0, _finding(file="a.py", line=100, category="input-validation")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert clusters == {}

    def test_different_categories_not_clustered(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="sql-injection")),
            ("perf", 0, _finding(file="a.py", line=10, category="n-plus-one-query")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert clusters == {}

    def test_singletons_not_included(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert clusters == {}

    def test_both_none_lines_clustered(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=None, category="input-validation")),
            ("cq", 0, _finding(file="a.py", line=None, category="input_validation")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert len(clusters) == 2

    def test_multiple_clusters(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
            ("cq", 0, _finding(file="a.py", line=12, category="input_validation")),
            ("sec", 1, _finding(file="b.py", line=50, category="error-handling")),
            ("cq", 1, _finding(file="b.py", line=52, category="error_handling")),
        ]
        clusters = cluster_potential_duplicates(flat)
        assert len(clusters) == 4
        # First pair should be in one cluster, second pair in another
        assert clusters[0] == clusters[1]
        assert clusters[2] == clusters[3]
        assert clusters[0] != clusters[2]

    def test_custom_line_threshold(self):
        flat = [
            ("sec", 0, _finding(file="a.py", line=10, category="input-validation")),
            ("cq", 0, _finding(file="a.py", line=18, category="input_validation")),
        ]
        # Default threshold of 5 — too far
        assert cluster_potential_duplicates(flat, line_threshold=5) == {}
        # Larger threshold — clustered
        clusters = cluster_potential_duplicates(flat, line_threshold=10)
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Merge findings
# ---------------------------------------------------------------------------


class TestMergeFindings:
    def test_severity_promoted_to_max(self):
        keeper = _finding(severity=Severity.MEDIUM)
        merged = [("cq", _finding(severity=Severity.HIGH))]
        result = merge_findings("sec", keeper, merged)
        assert result.severity == Severity.HIGH

    def test_certainty_promoted_to_max(self):
        keeper = _finding(certainty=Certainty.SUSPECTED)
        merged = [("cq", _finding(certainty=Certainty.DETECTED))]
        result = merge_findings("sec", keeper, merged)
        assert result.certainty == Certainty.DETECTED

    def test_evidence_basis_or_merged(self):
        keeper = _finding(evidence=EvidenceBasis(pattern_match=True))
        merged_f = _finding(evidence=EvidenceBasis(saw_full_context=True, cross_references=3))
        result = merge_findings("sec", keeper, [("cq", merged_f)])
        assert result.evidence_basis.pattern_match is True
        assert result.evidence_basis.saw_full_context is True
        assert result.evidence_basis.cross_references == 3

    def test_cwe_collected(self):
        keeper = _finding(cwe="CWE-79")
        merged = [("cq", _finding(cwe="CWE-89"))]
        result = merge_findings("sec", keeper, merged)
        assert "CWE-79" in result.cwe
        assert "CWE-89" in result.cwe

    def test_attribution_populated(self):
        keeper = _finding(desc="sec finding")
        merged = [("cq", _finding(desc="cq finding"))]
        result = merge_findings("sec", keeper, merged)
        assert result.primary_agent == "sec"
        assert result.merged_from_count == 2
        assert len(result.contributing_agents) == 2
        assert result.contributing_agents[0]["agent_name"] == "sec"
        assert result.contributing_agents[1]["agent_name"] == "cq"

    def test_keeper_preserved_when_already_strongest(self):
        keeper = _finding(severity=Severity.CRITICAL, certainty=Certainty.DETECTED)
        merged = [("cq", _finding(severity=Severity.LOW, certainty=Certainty.UNCERTAIN))]
        result = merge_findings("sec", keeper, merged)
        assert result.severity == Severity.CRITICAL
        assert result.certainty == Certainty.DETECTED
        assert result.description == keeper.description


# ---------------------------------------------------------------------------
# Merge action in _apply_validations
# ---------------------------------------------------------------------------


class TestApplyValidationsMerge:
    def test_merge_action_merges_findings(self):
        results = [
            _agent("sec", [_finding(desc="sec: missing validation")]),
            _agent("cq", [_finding(desc="cq: no validation")]),
        ]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "keep", "reason": "Best version"},
            {"index": 1, "action": "merge", "reason": "Same issue", "merge_into": 0},
        ]
        new_results, dismissed, downgraded, merged = _apply_validations(
            results,
            flat,
            validations,
        )
        assert merged == 1
        assert dismissed == 0
        # sec agent should have the merged finding
        assert len(new_results[0].findings) == 1
        merged_f = new_results[0].findings[0]
        assert merged_f.primary_agent == "sec"
        assert merged_f.merged_from_count == 2
        # cq agent should have its finding removed
        assert len(new_results[1].findings) == 0

    def test_merge_with_invalid_target_ignored(self):
        results = [
            _agent("sec", [_finding(desc="a")]),
            _agent("cq", [_finding(desc="b")]),
        ]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "keep", "reason": "Valid"},
            {"index": 1, "action": "merge", "reason": "Bad target", "merge_into": 99},
        ]
        new_results, _, _, merged = _apply_validations(results, flat, validations)
        # Invalid target — merge not applied, finding kept
        assert merged == 0
        assert len(new_results[0].findings) == 1
        assert len(new_results[1].findings) == 1

    def test_merge_coexists_with_dismiss_and_downgrade(self):
        results = [
            _agent("sec", [_finding(desc="a"), _finding(desc="b", severity=Severity.HIGH)]),
            _agent("cq", [_finding(desc="c")]),
            _agent("perf", [_finding(desc="d")]),
        ]
        flat = _flatten_findings(results)
        validations = [
            {"index": 0, "action": "keep", "reason": "Good"},
            {
                "index": 1,
                "action": "downgrade",
                "reason": "Overstated",
                "downgraded_severity": "medium",
            },
            {"index": 2, "action": "merge", "reason": "Same as 0", "merge_into": 0},
            {"index": 3, "action": "dismiss", "reason": "Noise"},
        ]
        new_results, dismissed, downgraded, merged = _apply_validations(
            results,
            flat,
            validations,
        )
        assert dismissed == 1
        assert downgraded == 1
        assert merged == 1
        # sec: merged finding (index 0) + downgraded finding (index 1)
        assert len(new_results[0].findings) == 2
        assert new_results[0].findings[0].merged_from_count == 2
        assert new_results[0].findings[1].severity == Severity.MEDIUM
        # cq: merged away
        assert len(new_results[1].findings) == 0
        # perf: dismissed
        assert len(new_results[2].findings) == 0

    def test_backward_compat_no_merge_actions(self):
        """Validations without any merge actions work exactly as before."""
        results = [_agent("sec", [_finding(desc="a")])]
        flat = _flatten_findings(results)
        validations = [{"index": 0, "action": "keep", "reason": "Fine"}]
        new_results, dismissed, downgraded, merged = _apply_validations(
            results,
            flat,
            validations,
        )
        assert dismissed == 0
        assert downgraded == 0
        assert merged == 0
        assert len(new_results[0].findings) == 1
