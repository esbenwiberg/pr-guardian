from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app)


@pytest.fixture
def review_with_quote_status():
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "decision": "human_review",
        "agent_results": [
            {
                "agent_name": "security_privacy",
                "verdict": "warn",
                "status": "ran",
                "status_reason": None,
                "findings": [
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "certainty": "detected",
                        "file": "src/auth.py",
                        "line": 17,
                        "category": "authorization",
                        "description": "The new guard can be bypassed.",
                        "quote": "return user.is_admin or allow_all",
                    }
                ],
            },
            {
                "agent_name": "architecture",
                "verdict": "pass",
                "status": "skipped",
                "status_reason": "no architecture context found",
                "findings": [],
            },
        ],
    }


def _patch_dashboard_storage(monkeypatch, review):
    from pr_guardian.api import dashboard as dash

    async def _get(_id):
        return review

    async def _active(*_args, **_kwargs):
        return []

    async def _archived(*_args, **_kwargs):
        return []

    monkeypatch.setattr(dash.storage, "get_review", _get)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _active)
    monkeypatch.setattr(dash.storage, "get_archived_dismissals", _archived)


def test_quote_status_payload_keeps_triage_enrichment(client, review_with_quote_status, monkeypatch):
    _patch_dashboard_storage(monkeypatch, review_with_quote_status)

    resp = client.get(f"/api/dashboard/reviews/{review_with_quote_status['id']}")

    assert resp.status_code == 200
    body = resp.json()
    agents = {a["agent_name"]: a for a in body["agent_results"]}
    finding = agents["security_privacy"]["findings"][0]
    assert finding["quote"] == "return user.is_admin or allow_all"
    assert finding["triage"] == "decision"
    assert body["triage_counts"] == {"noise": 0, "fyi": 0, "decision": 1}
    assert agents["architecture"]["status"] == "skipped"
    assert agents["architecture"]["status_reason"] == "no architecture context found"


def test_human_review_quote_strip_renderers_exist_and_inline_comments_stay_quote_free():
    dashboard_dir = Path("src/pr_guardian/dashboard")
    human_review = (dashboard_dir / "human_review.html").read_text()
    human_wizard = (dashboard_dir / "human_wizard.html").read_text()
    review_detail = (dashboard_dir / "review_detail.html").read_text()

    assert "data-quote-strip" in review_detail
    assert "data-quote-strip" in human_review
    assert "data-quote-strip" in human_wizard
    assert "Architecture skipped - " in review_detail
    assert "Architecture skipped - " in human_review
    assert "Architecture skipped - " in human_wizard

    from pr_guardian.decision.actions import build_inline_comment_body
    from pr_guardian.models.findings import Certainty, Finding, Severity

    quote = "return user.is_admin or allow_all"
    body = build_inline_comment_body([
        Finding(
            severity=Severity.HIGH,
            certainty=Certainty.DETECTED,
            category="authorization",
            language="python",
            file="src/auth.py",
            line=17,
            description="The new guard can be bypassed.",
            quote=quote,
        )
    ])
    assert quote not in body
