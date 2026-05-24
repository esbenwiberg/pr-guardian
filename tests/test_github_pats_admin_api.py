"""HTTP-layer tests for admin GitHub PAT endpoints.

Covers: GET/POST /api/admin/github-pats and PUT/DELETE /api/admin/github-pats/{id}.
In the test environment DATABASE_URL is not set, so the identity middleware grants
anonymous requests admin access — no auth headers are needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from pr_guardian.main import app


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _integrity_error(constraint: str) -> IntegrityError:
    orig = Exception(f'duplicate key value violates unique constraint "{constraint}"')
    return IntegrityError("statement", {}, orig)


def _pat_dict(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "name": "my-pat",
        "description": "",
        "token_prefix": "ghp_abcd...",
        "is_default": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# GET /api/admin/github-pats
# ---------------------------------------------------------------------------


class TestListGithubPats:
    def test_returns_200_with_list(self, client):
        with patch(
            "pr_guardian.persistence.storage.list_github_pats",
            AsyncMock(return_value=[_pat_dict()]),
        ):
            resp = client.get("/api/admin/github-pats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert resp.json()[0]["name"] == "my-pat"

    def test_empty_list_returns_200(self, client):
        with patch(
            "pr_guardian.persistence.storage.list_github_pats",
            AsyncMock(return_value=[]),
        ):
            resp = client.get("/api/admin/github-pats")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/admin/github-pats
# ---------------------------------------------------------------------------


class TestCreateGithubPat:
    def test_blank_name_returns_400(self, client):
        resp = client.post(
            "/api/admin/github-pats",
            json={"name": "   ", "token": "ghp_abc123"},
        )
        assert resp.status_code == 400

    def test_blank_token_returns_400(self, client):
        resp = client.post(
            "/api/admin/github-pats",
            json={"name": "my-pat", "token": "   "},
        )
        assert resp.status_code == 400

    def test_missing_token_returns_422(self, client):
        resp = client.post("/api/admin/github-pats", json={"name": "my-pat"})
        assert resp.status_code == 422

    def test_success_returns_201(self, client):
        with patch(
            "pr_guardian.persistence.storage.create_github_pat",
            AsyncMock(return_value=_pat_dict()),
        ):
            resp = client.post(
                "/api/admin/github-pats",
                json={"name": "my-pat", "token": "ghp_abc123"},
            )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-pat"

    def test_duplicate_name_returns_409_with_name_message(self, client):
        exc = _integrity_error("ix_github_pats_name")
        with patch(
            "pr_guardian.persistence.storage.create_github_pat",
            AsyncMock(side_effect=exc),
        ):
            resp = client.post(
                "/api/admin/github-pats",
                json={"name": "dup-pat", "token": "ghp_abc123"},
            )
        assert resp.status_code == 409
        assert "name" in resp.json()["detail"].lower()

    def test_concurrent_default_conflict_returns_409_with_default_message(self, client):
        exc = _integrity_error("uq_github_pats_single_default")
        with patch(
            "pr_guardian.persistence.storage.create_github_pat",
            AsyncMock(side_effect=exc),
        ):
            resp = client.post(
                "/api/admin/github-pats",
                json={"name": "new-default", "token": "ghp_abc123", "is_default": True},
            )
        assert resp.status_code == 409
        assert "default" in resp.json()["detail"].lower()

    def test_name_is_stripped(self, client):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return _pat_dict(name=kwargs["name"])

        with patch("pr_guardian.persistence.storage.create_github_pat", _capture):
            client.post(
                "/api/admin/github-pats",
                json={"name": "  padded  ", "token": "ghp_abc123"},
            )
        assert captured.get("name") == "padded"

    def test_token_is_stripped(self, client):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return _pat_dict()

        with patch("pr_guardian.persistence.storage.create_github_pat", _capture):
            client.post(
                "/api/admin/github-pats",
                json={"name": "my-pat", "token": "  ghp_abc123  "},
            )
        assert captured.get("token") == "ghp_abc123"


# ---------------------------------------------------------------------------
# PUT /api/admin/github-pats/{id}
# ---------------------------------------------------------------------------


class TestUpdateGithubPat:
    def test_blank_name_returns_400(self, client):
        resp = client.put(
            f"/api/admin/github-pats/{uuid.uuid4()}",
            json={"name": "   "},
        )
        assert resp.status_code == 400

    def test_blank_token_returns_400(self, client):
        resp = client.put(
            f"/api/admin/github-pats/{uuid.uuid4()}",
            json={"token": "   "},
        )
        assert resp.status_code == 400

    def test_not_found_returns_404(self, client):
        with patch(
            "pr_guardian.persistence.storage.update_github_pat",
            AsyncMock(return_value=None),
        ):
            resp = client.put(
                f"/api/admin/github-pats/{uuid.uuid4()}",
                json={"name": "new-name"},
            )
        assert resp.status_code == 404

    def test_success_returns_200(self, client):
        with patch(
            "pr_guardian.persistence.storage.update_github_pat",
            AsyncMock(return_value=_pat_dict(name="updated")),
        ):
            resp = client.put(
                f"/api/admin/github-pats/{uuid.uuid4()}",
                json={"name": "updated"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated"

    def test_duplicate_name_returns_409_with_name_message(self, client):
        exc = _integrity_error("ix_github_pats_name")
        with patch(
            "pr_guardian.persistence.storage.update_github_pat",
            AsyncMock(side_effect=exc),
        ):
            resp = client.put(
                f"/api/admin/github-pats/{uuid.uuid4()}",
                json={"name": "dup-name"},
            )
        assert resp.status_code == 409
        assert "name" in resp.json()["detail"].lower()

    def test_concurrent_default_conflict_returns_409_with_default_message(self, client):
        exc = _integrity_error("uq_github_pats_single_default")
        with patch(
            "pr_guardian.persistence.storage.update_github_pat",
            AsyncMock(side_effect=exc),
        ):
            resp = client.put(
                f"/api/admin/github-pats/{uuid.uuid4()}",
                json={"is_default": True},
            )
        assert resp.status_code == 409
        assert "default" in resp.json()["detail"].lower()

    def test_invalid_uuid_returns_422(self, client):
        resp = client.put("/api/admin/github-pats/not-a-uuid", json={"name": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/admin/github-pats/{id}
# ---------------------------------------------------------------------------


class TestDeleteGithubPat:
    def test_not_found_returns_404(self, client):
        with patch(
            "pr_guardian.persistence.storage.delete_github_pat",
            AsyncMock(return_value=False),
        ):
            resp = client.delete(f"/api/admin/github-pats/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_success_returns_200(self, client):
        with patch(
            "pr_guardian.persistence.storage.delete_github_pat",
            AsyncMock(return_value=True),
        ):
            resp = client.delete(f"/api/admin/github-pats/{uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_invalid_uuid_returns_422(self, client):
        resp = client.delete("/api/admin/github-pats/not-a-uuid")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# require_admin enforcement
# ---------------------------------------------------------------------------


class TestRequireAdmin:
    def test_non_admin_identity_returns_403(self, client):
        from pr_guardian.auth.identity import Identity

        non_admin = Identity(kind="user", email="user@example.com", is_admin=False)
        with patch("pr_guardian.auth.dependencies._get_identity", return_value=non_admin):
            resp = client.get("/api/admin/github-pats")
        assert resp.status_code == 403

    def test_admin_identity_passes(self, client):
        from pr_guardian.auth.identity import Identity

        admin = Identity(kind="user", email="admin@example.com", is_admin=True)
        with patch("pr_guardian.auth.dependencies._get_identity", return_value=admin):
            with patch(
                "pr_guardian.persistence.storage.list_github_pats",
                AsyncMock(return_value=[]),
            ):
                resp = client.get("/api/admin/github-pats")
        assert resp.status_code == 200
