"""Unit tests for the per-finding triage classifier (Phase 2)."""
from __future__ import annotations

import pytest

from pr_guardian.decision.finding_triage import (
    DECISION,
    FYI,
    NOISE,
    classify_finding,
    tag_findings_with_triage,
)


# ---------------------------------------------------------------------------
# classify_finding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("severity,certainty,expected", [
    ("critical", "detected",   DECISION),
    ("critical", "uncertain",  DECISION),
    ("high",     "detected",   DECISION),
    ("high",     "suspected",  DECISION),
    ("medium",   "detected",   DECISION),
    ("medium",   "suspected",  FYI),
    ("medium",   "uncertain",  FYI),
    ("low",      "detected",   FYI),
    ("low",      "suspected",  NOISE),
    ("low",      "uncertain",  NOISE),
    ("low",      "",           NOISE),
])
def test_classify_finding_matrix(severity, certainty, expected):
    finding = {"severity": severity, "certainty": certainty}
    assert classify_finding(finding) == expected


def test_dismissed_is_noise_regardless_of_severity():
    f = {"severity": "critical", "certainty": "detected", "dismissal": {"id": "x"}}
    assert classify_finding(f) == NOISE


def test_unknown_severity_fails_safe_to_decision():
    """An unrecognised severity surfaces for human review rather than getting silently filtered."""
    assert classify_finding({"severity": "unknown"}) == DECISION
    assert classify_finding({}) == DECISION


def test_severity_is_case_insensitive():
    assert classify_finding({"severity": "HIGH"}) == DECISION
    assert classify_finding({"severity": "Low", "certainty": "Detected"}) == FYI


# ---------------------------------------------------------------------------
# tag_findings_with_triage
# ---------------------------------------------------------------------------

def test_tag_findings_in_place_and_returns_counts():
    agent_results = [
        {"agent_name": "security", "findings": [
            {"severity": "high", "certainty": "detected"},
            {"severity": "medium", "certainty": "suspected"},
            {"severity": "low", "certainty": "uncertain"},
            {"severity": "low", "certainty": "detected", "dismissal": {"id": "abc"}},
        ]},
        {"agent_name": "perf", "findings": [
            {"severity": "critical", "certainty": "detected"},
            {"severity": "low", "certainty": "uncertain"},
        ]},
    ]

    counts = tag_findings_with_triage(agent_results)
    assert counts == {NOISE: 3, FYI: 1, DECISION: 2}

    # In-place mutation: every finding gets a triage field.
    classes = [f["triage"] for ar in agent_results for f in ar["findings"]]
    assert classes == [DECISION, FYI, NOISE, NOISE, DECISION, NOISE]


def test_tag_handles_missing_or_empty_inputs():
    assert tag_findings_with_triage([]) == {NOISE: 0, FYI: 0, DECISION: 0}
    assert tag_findings_with_triage(None) == {NOISE: 0, FYI: 0, DECISION: 0}
    # Empty findings list on an agent.
    assert tag_findings_with_triage([{"agent_name": "x"}]) == {NOISE: 0, FYI: 0, DECISION: 0}


def test_tag_does_not_overwrite_pre_existing_triage_field():
    """If a finding already has a triage label, classify still recomputes — by design.

    Phase 2 is the source of truth for triage; we don't want stale labels from
    older runs sneaking through. The test pins the contract.
    """
    agent_results = [{"findings": [{"severity": "high", "triage": "noise"}]}]
    tag_findings_with_triage(agent_results)
    assert agent_results[0]["findings"][0]["triage"] == DECISION
