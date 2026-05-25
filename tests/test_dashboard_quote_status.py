"""Dashboard API/UI contract tests for finding quote and skipped agent status."""
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
def quote_status_review():
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
                        "category": "sql-injection",
                        "language": "python",
                        "file": "app.py",
                        "line": 7,
                        "description": "Untrusted input reaches SQL.",
                        "quote": "cursor.execute(f\"select * from users where id={user_id}\")",
                    }
                ],
            },
            {
                "agent_name": "architecture_intent",
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

    async def _empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(dash.storage, "get_review", _get)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _empty)
    monkeypatch.setattr(dash.storage, "get_archived_dismissals", _empty)


def test_quote_status_payload(client, quote_status_review, monkeypatch):
    _patch_dashboard_storage(monkeypatch, quote_status_review)

    resp = client.get(f"/api/dashboard/reviews/{quote_status_review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    finding = body["agent_results"][0]["findings"][0]
    architecture = body["agent_results"][1]
    assert finding["quote"] == "cursor.execute(f\"select * from users where id={user_id}\")"
    assert finding["triage"] == "decision"
    assert architecture["status"] == "skipped"
    assert architecture["status_reason"] == "no architecture context found"


def test_human_review_quote_visible():
    root = Path(__file__).resolve().parents[1]
    human_review = (root / "src/pr_guardian/dashboard/human_review.html").read_text()
    human_wizard = (root / "src/pr_guardian/dashboard/human_wizard.html").read_text()

    assert "quote-strip" in human_review
    assert "renderQuoteStrip(f.quote)" in human_review
    assert "quote-strip" in human_wizard
    assert "d.quote" in human_wizard
