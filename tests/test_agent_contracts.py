"""Contract tests for shared agent/finding dataclasses (Brief 01)."""
from __future__ import annotations

import dataclasses

from pr_guardian.models.findings import AgentResult, Finding, Severity, Certainty, Verdict


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="test",
        language="python",
        file="src/foo.py",
        line=10,
        description="A test finding",
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestFindingQuoteField:
    def test_quote_is_a_field(self):
        field_names = {f.name for f in dataclasses.fields(Finding)}
        assert "quote" in field_names

    def test_quote_defaults_to_empty_string(self):
        finding = _make_finding()
        assert finding.quote == ""

    def test_quote_accepts_string_value(self):
        finding = _make_finding(quote="+ return user.is_admin or allow_all")
        assert finding.quote == "+ return user.is_admin or allow_all"

    def test_quote_is_string_type(self):
        fields = {f.name: f for f in dataclasses.fields(Finding)}
        assert fields["quote"].type is str or fields["quote"].default == ""


class TestAgentResultStatusField:
    def test_status_is_a_field(self):
        field_names = {f.name for f in dataclasses.fields(AgentResult)}
        assert "status" in field_names

    def test_status_defaults_to_ran(self):
        result = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        assert result.status == "ran"

    def test_status_reason_is_a_field(self):
        field_names = {f.name for f in dataclasses.fields(AgentResult)}
        assert "status_reason" in field_names

    def test_status_reason_defaults_to_none(self):
        result = AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)
        assert result.status_reason is None

    def test_skipped_status_accepted(self):
        result = AgentResult(
            agent_name="architecture",
            verdict=Verdict.PASS,
            status="skipped",
            status_reason="no architecture context found",
        )
        assert result.status == "skipped"
        assert result.status_reason == "no architecture context found"

    def test_ran_status_accepted(self):
        result = AgentResult(
            agent_name="intent",
            verdict=Verdict.WARN,
            status="ran",
        )
        assert result.status == "ran"
