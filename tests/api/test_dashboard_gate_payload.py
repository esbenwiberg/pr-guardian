"""Verify the dashboard review-detail endpoint exposes gate_read + escalation_mode.

Contract fact: fact-dashboard-payload-gate-read
Scenario: payload-carries-gate-read
  Given: a structural_only review
  When: the dashboard review payload is fetched
  Then: it includes the gate level, reason, and escalation mode
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app)


def _patch(monkeypatch, review, dismissals=None, archived=None):
    from pr_guardian.api import dashboard as dash

    async def _get(_id):
        return review

    async def _active(*_a, **_k):
        return dismissals or []

    async def _archived(*_a, **_k):
        return archived or []

    async def _synced_pr_lookup(*_a, **_k):
        return {}

    monkeypatch.setattr(dash.storage, "get_review", _get)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _active)
    monkeypatch.setattr(dash.storage, "get_archived_dismissals", _archived)
    monkeypatch.setattr(dash.storage, "get_synced_pr_lookup", _synced_pr_lookup)


def _structural_review(gate_read=None, sticky_triggers=None, reject_threshold="confident_only"):
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "7",
        "repo": "org/repo",
        "platform": "github",
        "agent_results": [],
        "sticky_triggers": sticky_triggers or [],
        "finding_reasons": [],
        "gate_read": gate_read,
        "profile_snapshot": {
            "escalation_policy": {
                "mode": "structural_only",
                "gate_threshold": "medium_plus",
                "reject_threshold": reject_threshold,
            }
        },
    }


def _standard_review():
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "8",
        "repo": "org/repo",
        "platform": "github",
        "agent_results": [],
        "sticky_triggers": [],
        "finding_reasons": [],
        "gate_read": None,
        "profile_snapshot": {
            "escalation_policy": {
                "mode": "standard",
            }
        },
    }


# ---------------------------------------------------------------------------
# Scenario: payload-carries-gate-read
# ---------------------------------------------------------------------------


def test_structural_only_payload_includes_gate_level_and_reason(client, monkeypatch):
    """structural_only auto-approved review → payload has gate level, reason, escalation_mode."""
    review = _structural_review(
        gate_read={"level": "low", "reason": "CI-only change. No prod paths.", "gated": False}
    )
    _patch(monkeypatch, review)

    resp = client.get(f"/api/dashboard/reviews/{review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["escalation_mode"] == "structural_only"
    assert body["gate_read"] is not None
    assert body["gate_read"]["level"] == "low"
    assert body["gate_read"]["reason"] == "CI-only change. No prod paths."


def test_structural_only_payload_gate_read_from_sticky_when_absent(client, monkeypatch):
    """Legacy structural_only row with no gate_read but gate_agent sticky → derived gate_read."""
    stickies = [
        {
            "kind": "gate_agent",
            "label": "Gate agent: HIGH danger",
            "reason": "Destructive migration detected",
            "source": "gate_agent",
        }
    ]
    review = _structural_review(gate_read=None, sticky_triggers=stickies)
    _patch(monkeypatch, review)

    resp = client.get(f"/api/dashboard/reviews/{review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["escalation_mode"] == "structural_only"
    assert body["gate_read"] is not None
    assert body["gate_read"]["level"] == "high"
    assert body["gate_read"]["reason"] == "Destructive migration detected"
    assert body["gate_read"]["gated"] is True


def test_standard_mode_payload_has_standard_escalation_mode(client, monkeypatch):
    """Standard review → escalation_mode is 'standard', gate_read absent."""
    review = _standard_review()
    _patch(monkeypatch, review)

    resp = client.get(f"/api/dashboard/reviews/{review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["escalation_mode"] == "standard"
    # gate_read key may be absent or None in standard mode
    assert body.get("gate_read") is None


def test_structural_only_payload_includes_escalation_mode_no_profile_snapshot(client, monkeypatch):
    """structural_only row without profile_snapshot → escalation_mode defaults to 'standard'."""
    review = {
        "id": str(uuid.uuid4()),
        "pr_id": "9",
        "repo": "org/repo",
        "platform": "github",
        "agent_results": [],
        "sticky_triggers": [],
        "finding_reasons": [],
        "gate_read": {"level": "none", "reason": "safe", "gated": False},
        "profile_snapshot": None,
    }
    _patch(monkeypatch, review)

    resp = client.get(f"/api/dashboard/reviews/{review['id']}")
    assert resp.status_code == 200
    body = resp.json()

    # Without profile_snapshot we can't know structural_only, so mode falls back to standard.
    # The gate_read from the row is not surfaced in standard mode.
    assert body["escalation_mode"] == "standard"
