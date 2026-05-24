"""Confirms the dashboard review-detail endpoint stamps a triage class on
every finding and exposes a triage_counts roll-up. (Phase 2 enrichment.)
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app)


@pytest.fixture
def fake_review_with_mixed_findings():
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "agent_results": [
            {
                "agent_name": "security",
                "findings": [
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "certainty": "detected",
                        "file": "a.py",
                        "category": "x",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "medium",
                        "certainty": "uncertain",
                        "file": "b.py",
                        "category": "y",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "low",
                        "certainty": "detected",
                        "file": "c.py",
                        "category": "z",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "low",
                        "certainty": "uncertain",
                        "file": "d.py",
                        "category": "w",
                    },
                ],
            },
        ],
    }


def _patch(monkeypatch, review):
    from pr_guardian.api import dashboard as dash

    async def _get(_id):
        return review

    async def _active(*_a, **_k):
        return []

    async def _archived(*_a, **_k):
        return []

    monkeypatch.setattr(dash.storage, "get_review", _get)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _active)
    monkeypatch.setattr(dash.storage, "get_archived_dismissals", _archived)


def test_each_finding_gets_a_triage_class(client, fake_review_with_mixed_findings, monkeypatch):
    _patch(monkeypatch, fake_review_with_mixed_findings)

    resp = client.get(f"/api/dashboard/reviews/{fake_review_with_mixed_findings['id']}")
    assert resp.status_code == 200
    body = resp.json()

    findings = body["agent_results"][0]["findings"]
    triage_classes = [f["triage"] for f in findings]
    assert triage_classes == ["decision", "fyi", "fyi", "noise"]


def test_triage_counts_rollup_matches_per_finding_classes(
    client, fake_review_with_mixed_findings, monkeypatch
):
    _patch(monkeypatch, fake_review_with_mixed_findings)

    resp = client.get(f"/api/dashboard/reviews/{fake_review_with_mixed_findings['id']}")
    body = resp.json()

    assert body["triage_counts"] == {"noise": 1, "fyi": 2, "decision": 1}


def test_endpoint_still_works_when_dismissal_lookup_fails(
    client, fake_review_with_mixed_findings, monkeypatch
):
    """If dismissal enrichment throws (e.g. DB hiccup), the endpoint still
    returns the review with safe defaults — including a triage_counts shape
    consumers can rely on."""
    from pr_guardian.api import dashboard as dash

    async def _get(_id):
        return fake_review_with_mixed_findings

    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dash.storage, "get_review", _get)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _boom)

    resp = client.get(f"/api/dashboard/reviews/{fake_review_with_mixed_findings['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["triage_counts"] == {"noise": 0, "fyi": 0, "decision": 0}
