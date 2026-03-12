from pr_guardian.config.schema import GuardianConfig
from pr_guardian.decision.dedup import deduplicate_findings
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)


def _finding(
    file: str = "test.py",
    line: int | None = 10,
    description: str = "Some issue with error handling in this code",
    severity: Severity = Severity.MEDIUM,
    certainty: Certainty = Certainty.DETECTED,
    category: str = "error_handling",
) -> Finding:
    return Finding(
        severity=severity,
        certainty=certainty,
        category=category,
        language="python",
        file=file,
        line=line,
        description=description,
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            suggestion_is_concrete=True,
        ),
    )


def _agent(name: str, findings: list[Finding], verdict: Verdict = Verdict.WARN) -> AgentResult:
    return AgentResult(agent_name=name, verdict=verdict, findings=findings)


class TestDedupSameFileAndLine:
    """Two agents flag same file + same line + similar description."""

    def test_keeps_higher_weight_agent(self):
        """security_privacy (weight 3.0) beats code_quality_observability (weight 1.0)."""
        results = [
            _agent("security_privacy", [
                _finding(description="Missing error handling in authentication code path"),
            ]),
            _agent("code_quality_observability", [
                _finding(description="Missing error handling in authentication code"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        # security_privacy finding kept, code_quality_observability removed
        assert len(deduped[0].findings) == 1  # security_privacy
        assert len(deduped[1].findings) == 0  # code_quality_observability

    def test_tie_broken_by_severity(self):
        """Same agent weight → higher severity wins."""
        results = [
            _agent("performance", [
                _finding(severity=Severity.HIGH, description="Resource leak in connection handling code"),
            ]),
            _agent("hotspot", [
                _finding(severity=Severity.LOW, description="Resource leak in connection handling code"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        assert len(deduped[0].findings) == 1  # performance (HIGH)
        assert len(deduped[1].findings) == 0  # hotspot (LOW) removed

    def test_tie_broken_by_certainty(self):
        """Same weight + same severity → higher certainty wins."""
        results = [
            _agent("performance", [
                _finding(certainty=Certainty.DETECTED, description="N+1 query in loop over items"),
            ]),
            _agent("hotspot", [
                _finding(certainty=Certainty.SUSPECTED, description="N+1 query in loop over items"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        assert len(deduped[0].findings) == 1  # performance (DETECTED)
        assert len(deduped[1].findings) == 0  # hotspot (SUSPECTED) removed


class TestDedupNearbyLines:
    """Duplicate detection for nearby but not identical line numbers."""

    def test_lines_within_proximity(self):
        """Lines within ±3 are treated as same location."""
        results = [
            _agent("security_privacy", [
                _finding(line=10, description="SQL injection risk in query construction"),
            ]),
            _agent("code_quality_observability", [
                _finding(line=12, description="SQL injection risk in query construction code"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1

    def test_lines_outside_proximity(self):
        """Lines more than 3 apart are kept even with similar descriptions."""
        results = [
            _agent("security_privacy", [
                _finding(line=10, description="SQL injection risk in query construction"),
            ]),
            _agent("code_quality_observability", [
                _finding(line=20, description="SQL injection risk in query construction"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
        assert len(deduped[0].findings) == 1
        assert len(deduped[1].findings) == 1


class TestDedupDifferentDescriptions:
    """Same file + same line but different descriptions are kept."""

    def test_different_descriptions_kept(self):
        results = [
            _agent("security_privacy", [
                _finding(description="SQL injection via string interpolation with user input"),
            ]),
            _agent("code_quality_observability", [
                _finding(description="Missing structured logging for API endpoint response"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
        assert len(deduped[0].findings) == 1
        assert len(deduped[1].findings) == 1


class TestDedupSameAgent:
    """Findings from the same agent are never deduplicated."""

    def test_same_agent_not_deduped(self):
        results = [
            _agent("security_privacy", [
                _finding(line=10, description="Missing error handling in auth code path"),
                _finding(line=11, description="Missing error handling in auth code path"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
        assert len(deduped[0].findings) == 2


class TestDedupSameCategoryLowerThreshold:
    """Same category uses a lower similarity threshold (0.3 vs 0.5)."""

    def test_same_category_deduped_at_lower_threshold(self):
        """Findings with same category match at lower Jaccard threshold."""
        results = [
            _agent("security_privacy", [
                _finding(
                    category="sql_injection",
                    description="SQL injection risk found in database query construction",
                ),
            ]),
            _agent("code_quality_observability", [
                _finding(
                    category="sql_injection",
                    description="Database query has potential SQL injection vulnerability",
                ),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1


class TestDedupDifferentFiles:
    """Findings in different files are never duplicates."""

    def test_different_files_kept(self):
        results = [
            _agent("security_privacy", [
                _finding(file="auth.py", description="Missing input validation"),
            ]),
            _agent("code_quality_observability", [
                _finding(file="handler.py", description="Missing input validation"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0


class TestDedupEdgeCases:
    """Edge cases: empty findings, single finding, verdict handling."""

    def test_empty_findings(self):
        results = [_agent("security_privacy", [])]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
        assert len(deduped[0].findings) == 0

    def test_single_finding(self):
        results = [_agent("security_privacy", [_finding()])]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
        assert len(deduped[0].findings) == 1

    def test_originals_not_mutated(self):
        original_finding = _finding(description="Missing error handling in code path")
        results = [
            _agent("security_privacy", [original_finding]),
            _agent("code_quality_observability", [
                _finding(description="Missing error handling in code path"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        # Originals unchanged
        assert len(results[0].findings) == 1
        assert len(results[1].findings) == 1

    def test_all_findings_removed_downgrades_verdict(self):
        """If dedup removes all findings from an agent, verdict → PASS."""
        results = [
            _agent("security_privacy", [
                _finding(description="Missing error handling in authentication code path"),
            ]),
            _agent("code_quality_observability", [
                _finding(description="Missing error handling in authentication code"),
            ], verdict=Verdict.WARN),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        # code_quality lost its only finding
        assert deduped[1].verdict == Verdict.PASS

    def test_flag_human_verdict_preserved(self):
        """FLAG_HUMAN verdict is not downgraded even if all findings removed."""
        results = [
            _agent("security_privacy", [
                _finding(description="Critical vulnerability in authentication flow"),
            ]),
            _agent("code_quality_observability", [
                _finding(description="Critical vulnerability in authentication flow"),
            ], verdict=Verdict.FLAG_HUMAN),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1
        assert deduped[1].verdict == Verdict.FLAG_HUMAN

    def test_none_lines_handled(self):
        """Findings with line=None: both None → near, one None one int → not near."""
        results = [
            _agent("security_privacy", [
                _finding(line=None, description="Missing error handling code"),
            ]),
            _agent("code_quality_observability", [
                _finding(line=None, description="Missing error handling code"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 1

    def test_none_vs_int_line_not_near(self):
        """line=None vs line=10 should not be considered near."""
        results = [
            _agent("security_privacy", [
                _finding(line=None, description="Missing error handling code"),
            ]),
            _agent("code_quality_observability", [
                _finding(line=10, description="Missing error handling code"),
            ]),
        ]
        deduped, removed = deduplicate_findings(results, GuardianConfig())
        assert removed == 0
