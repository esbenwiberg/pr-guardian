"""Tests for POST /api/dashboard/reviews/{id}/verify endpoint."""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app
    return TestClient(app)


@pytest.fixture
def review_id():
    return uuid.uuid4()


@pytest.fixture
def fake_review(review_id):
    return {
        "id": str(review_id),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
    }


def _patch_storage(monkeypatch, fake_review):
    from pr_guardian.api import dashboard as dash
    monkeypatch.setattr(dash.storage, "get_review", AsyncMock(return_value=fake_review))
    monkeypatch.setattr(dash.storage, "verify_sticky_trigger", AsyncMock(return_value=None))
    return dash.storage


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_valid_payload_returns_200_and_record(client, review_id, fake_review, monkeypatch):
    storage = _patch_storage(monkeypatch, fake_review)
    resp = client.post(
        f"/api/dashboard/reviews/{review_id}/verify",
        json={"trigger_kind": "new_dep", "trigger_source": "requests==2.32.3", "user": "alice@example.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verified"] is True
    assert "signature" in body
    # Signature formula: sha256(pr_id::kind::source)[:16]
    expected_sig = hashlib.sha256("42::new_dep::requests==2.32.3".encode()).hexdigest()[:16]
    assert body["signature"] == expected_sig
    storage.verify_sticky_trigger.assert_awaited_once_with(
        "42", "new_dep", "requests==2.32.3", "alice@example.com"
    )


def test_unknown_trigger_kind_returns_400(client, review_id, fake_review, monkeypatch):
    _patch_storage(monkeypatch, fake_review)
    resp = client.post(
        f"/api/dashboard/reviews/{review_id}/verify",
        json={"trigger_kind": "not_a_real_kind", "trigger_source": "something", "user": "bob"},
    )
    assert resp.status_code == 400
    assert "trigger_kind" in resp.text or "Unknown" in resp.text


def test_unknown_review_id_returns_404(client, monkeypatch):
    from pr_guardian.api import dashboard as dash
    monkeypatch.setattr(dash.storage, "get_review", AsyncMock(return_value=None))
    resp = client.post(
        f"/api/dashboard/reviews/{uuid.uuid4()}/verify",
        json={"trigger_kind": "hotspot", "trigger_source": "src/auth/", "user": "carol"},
    )
    assert resp.status_code == 404


def test_idempotent_second_post_returns_200(client, review_id, fake_review, monkeypatch):
    """Posting the same verify twice is a no-op success (idempotent)."""
    storage = _patch_storage(monkeypatch, fake_review)
    payload = {"trigger_kind": "path_risk", "trigger_source": "src/auth/", "user": "dave"}
    resp1 = client.post(f"/api/dashboard/reviews/{review_id}/verify", json=payload)
    assert resp1.status_code == 200, resp1.text
    resp2 = client.post(f"/api/dashboard/reviews/{review_id}/verify", json=payload)
    assert resp2.status_code == 200, resp2.text
    # Both calls should have same signature
    assert resp1.json()["signature"] == resp2.json()["signature"]
    # verify_sticky_trigger was called twice (idempotency is in storage, not the endpoint)
    assert storage.verify_sticky_trigger.await_count == 2
