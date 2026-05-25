from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app)


def _review() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "agent_results": [
            {
                "agent_name": "architecture",
                "verdict": "pass",
                "status": "skipped",
                "status_reason": "no architecture context found",
                "findings": [],
            },
            {
                "agent_name": "intent",
                "verdict": "flag_human",
                "status": "ran",
                "status_reason": None,
                "findings": [
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "medium",
                        "certainty": "suspected",
                        "file": "",
                        "line": None,
                        "category": "scope-opacity",
                        "description": "PR intent is too broad.",
                        "quote": "PR description: fixes auth stuff",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "certainty": "detected",
                        "file": "src/auth.py",
                        "line": 12,
                        "category": "auth-bypass",
                        "description": "Bypass can allow access.",
                        "quote": "return user.is_admin or allow_all",
                    },
                ],
            },
        ],
    }


def _patch_dashboard(monkeypatch, review):
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


def test_quote_status_payload_preserves_triage_enrichment(client, monkeypatch):
    review = _review()
    _patch_dashboard(monkeypatch, review)

    resp = client.get(f"/api/dashboard/reviews/{review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    architecture = body["agent_results"][0]
    assert architecture["status"] == "skipped"
    assert architecture["status_reason"] == "no architecture context found"

    findings = body["agent_results"][1]["findings"]
    assert findings[0]["line"] is None
    assert findings[0]["quote"] == "PR description: fixes auth stuff"
    assert findings[0]["triage"] == "fyi"
    assert findings[1]["quote"] == "return user.is_admin or allow_all"
    assert findings[1]["triage"] == "decision"
    assert body["triage_counts"] == {"noise": 0, "fyi": 1, "decision": 1}


def test_human_review_quote_rendering_contract_is_present(client):
    resp = client.get(f"/reviews/{uuid.uuid4()}/human-review")
    assert resp.status_code == 200
    html = resp.text
    assert "function renderQuoteStrip(quote)" in html
    assert "data-quote-strip" in html
    assert "Architecture skipped - no architecture context found" in html
