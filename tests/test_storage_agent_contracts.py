"""Storage contract tests for persisted finding quote and agent status."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.persistence import storage
from pr_guardian.persistence.models import AgentResultRow, FindingRow, ReviewRow


class _Session:
    def __init__(self, review: ReviewRow):
        self.review = review
        self._current_agent: AgentResultRow | None = None

    async def get(self, model, key):
        if model is ReviewRow and key == self.review.id:
            return self.review
        return None

    def add(self, row):
        if isinstance(row, AgentResultRow):
            row.id = uuid.uuid4()
            row.findings = []
            self.review.agent_results.append(row)
            self._current_agent = row
        elif isinstance(row, FindingRow):
            row.id = uuid.uuid4()
            if self._current_agent is not None:
                self._current_agent.findings.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _session_factory(session: _Session):
    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


async def test_quote_status_roundtrip(monkeypatch):
    review_id = uuid.uuid4()
    review = ReviewRow(
        id=review_id,
        pr_id="42",
        repo="org/repo",
        platform="github",
        author="ada",
        title="Persist quote",
        source_branch="feature",
        target_branch="main",
        head_commit_sha="abc123",
    )
    review.mechanical_results = []
    review.agent_results = []

    result = ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.MEDIUM,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.HUMAN_REVIEW,
        agent_results=[
            AgentResult(
                agent_name="security_privacy",
                verdict=Verdict.WARN,
                findings=[
                    Finding(
                        severity=Severity.HIGH,
                        certainty=Certainty.DETECTED,
                        category="sql-injection",
                        language="python",
                        file="app.py",
                        line=7,
                        description="Untrusted input reaches SQL.",
                        quote="cursor.execute(f\"select * from users where id={user_id}\")",
                    )
                ],
            ),
            AgentResult(
                agent_name="architecture_intent",
                verdict=Verdict.PASS,
                status="skipped",
                status_reason="no architecture context found",
            ),
        ],
    )

    session = _Session(review)
    monkeypatch.setattr(storage, "async_session", _session_factory(session))

    await storage.save_review_result(review_id, result)
    body = await storage.get_review(review_id)

    finding = body["agent_results"][0]["findings"][0]
    architecture = body["agent_results"][1]
    assert finding["quote"] == "cursor.execute(f\"select * from users where id={user_id}\")"
    assert architecture["status"] == "skipped"
    assert architecture["status_reason"] == "no architecture context found"
