from __future__ import annotations

import uuid

from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.persistence.models import AgentResultRow, FindingRow, ReviewRow


class _FakeSession:
    def __init__(self, review: ReviewRow):
        self.review = review
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _id):
        return self.review

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if isinstance(row, AgentResultRow) and row.id is None:
                row.id = uuid.uuid4()

    async def commit(self):
        return None


def _review_row(review_id: uuid.UUID) -> ReviewRow:
    row = ReviewRow(
        id=review_id,
        pr_id="42",
        repo="org/repo",
        platform="github",
        author="dev",
        title="Add auth fast path",
        source_branch="feature",
        target_branch="main",
        head_commit_sha="abc123",
    )
    row.mechanical_results = []
    row.agent_results = []
    return row


async def test_quote_status_roundtrip_through_storage(monkeypatch):
    from pr_guardian.persistence import storage

    review_id = uuid.uuid4()
    review = _review_row(review_id)
    fake_session = _FakeSession(review)
    quote = "return user.is_admin or allow_all"

    result = ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.MEDIUM,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.HUMAN_REVIEW,
        agent_results=[
            AgentResult(
                agent_name="architecture",
                verdict=Verdict.PASS,
                status="skipped",
                status_reason="no architecture context found",
            ),
            AgentResult(
                agent_name="intent",
                verdict=Verdict.FLAG_HUMAN,
                findings=[
                    Finding(
                        severity=Severity.MEDIUM,
                        certainty=Certainty.SUSPECTED,
                        category="scope-opacity",
                        language="python",
                        file="src/auth.py",
                        line=None,
                        description="PR intent is too broad for the touched auth path.",
                        quote=quote,
                    )
                ],
            ),
        ],
    )

    monkeypatch.setattr(storage, "async_session", lambda: fake_session)
    await storage.save_review_result(review_id, result)

    saved_agents = [r for r in fake_session.added if isinstance(r, AgentResultRow)]
    saved_findings = [r for r in fake_session.added if isinstance(r, FindingRow)]
    assert saved_agents[0].status == "skipped"
    assert saved_agents[0].status_reason == "no architecture context found"
    assert saved_findings[0].quote == quote

    review.agent_results = saved_agents
    saved_agents[0].findings = []
    saved_agents[1].findings = saved_findings
    payload = storage._review_to_dict(review)

    assert payload["agent_results"][0]["status"] == "skipped"
    assert payload["agent_results"][0]["status_reason"] == "no architecture context found"
    assert payload["agent_results"][1]["findings"][0]["line"] is None
    assert payload["agent_results"][1]["findings"][0]["quote"] == quote
