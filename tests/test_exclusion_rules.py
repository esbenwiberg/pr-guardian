"""Tests for wildcard exclusion rules: matcher, storage, admin API, sync filter."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pr_guardian.main import app
from pr_guardian.persistence.storage import (
    add_exclusion_rule,
    list_exclusion_rules,
    remove_exclusion_rule,
    repo_matches_rules,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_cm(session):
    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


class _FailingCM:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_):
        return False


def _failing_session():
    exc = RuntimeError("DB down")
    return lambda: _FailingCM(exc)


def _make_rule_row(
    *,
    platform: str = "github",
    org_pattern: str = "",
    project_pattern: str = "",
    repo_pattern: str = "",
    email: str = "admin@example.com",
):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.platform = platform
    row.org_pattern = org_pattern
    row.project_pattern = project_pattern
    row.repo_pattern = repo_pattern
    row.created_by_email = email
    row.created_at = datetime.now(timezone.utc)
    return row


# ---------------------------------------------------------------------------
# repo_matches_rules
# ---------------------------------------------------------------------------


class TestRepoMatchesRules:
    def test_empty_rules_never_match(self):
        assert repo_matches_rules([], "github", "acme", "", "acme/foo") is False

    def test_exact_match_via_patterns(self):
        rules = [
            {
                "platform": "github",
                "org_pattern": "acme",
                "project_pattern": "",
                "repo_pattern": "acme/foo",
            }
        ]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/foo") is True

    def test_org_wildcard_matches_all_repos(self):
        rules = [
            {
                "platform": "github",
                "org_pattern": "acme",
                "project_pattern": "",
                "repo_pattern": "",
            }
        ]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/foo") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/bar") is True
        assert repo_matches_rules(rules, "github", "other", "", "other/foo") is False

    def test_repo_pattern_wildcard(self):
        rules = [
            {
                "platform": "github",
                "org_pattern": "acme",
                "project_pattern": "",
                "repo_pattern": "acme/test-*",
            }
        ]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/test-foo") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/prod-foo") is False

    def test_platform_isolation(self):
        rules = [
            {"platform": "github", "org_pattern": "", "project_pattern": "", "repo_pattern": "*"}
        ]
        assert repo_matches_rules(rules, "github", "x", "", "x/y") is True
        assert repo_matches_rules(rules, "ado", "x", "p", "y") is False

    def test_ado_project_pattern(self):
        rules = [
            {
                "platform": "ado",
                "org_pattern": "",
                "project_pattern": "Legacy*",
                "repo_pattern": "",
            }
        ]
        assert (
            repo_matches_rules(rules, "ado", "https://dev.azure.com/x", "LegacyApps", "foo")
            is True
        )
        assert (
            repo_matches_rules(rules, "ado", "https://dev.azure.com/x", "ModernApps", "foo")
            is False
        )

    def test_question_mark_wildcard(self):
        rules = [
            {
                "platform": "github",
                "org_pattern": "",
                "project_pattern": "",
                "repo_pattern": "acme/v?",
            }
        ]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/v1") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/v12") is False

    def test_any_rule_match_is_enough(self):
        rules = [
            {
                "platform": "github",
                "org_pattern": "miss",
                "project_pattern": "",
                "repo_pattern": "",
            },
            {
                "platform": "github",
                "org_pattern": "acme",
                "project_pattern": "",
                "repo_pattern": "",
            },
        ]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/foo") is True


# ---------------------------------------------------------------------------
# Storage: list / add / remove
# ---------------------------------------------------------------------------


async def test_list_exclusion_rules_returns_dicts():
    row = _make_rule_row(platform="github", org_pattern="acme", repo_pattern="acme/test-*")
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = [row]
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.exclusions.async_session", _session_cm(session)):
        result = await list_exclusion_rules()

    assert len(result) == 1
    assert result[0]["org_pattern"] == "acme"
    assert result[0]["repo_pattern"] == "acme/test-*"


async def test_list_exclusion_rules_db_failure_returns_empty():
    with patch("pr_guardian.persistence.exclusions.async_session", _failing_session()):
        result = await list_exclusion_rules()
    assert result == []


async def test_add_exclusion_rule_persists_fields():
    session = AsyncMock()
    captured = None

    def _cap(row):
        nonlocal captured
        captured = row

    session.add = MagicMock(side_effect=_cap)
    session.commit = AsyncMock()

    async def _refresh(row):
        row.created_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_refresh)

    with patch("pr_guardian.persistence.exclusions.async_session", _session_cm(session)):
        result = await add_exclusion_rule(
            platform="github",
            org_pattern="acme",
            repo_pattern="acme/test-*",
            email="admin@example.com",
        )

    assert captured.platform == "github"
    assert captured.org_pattern == "acme"
    assert captured.repo_pattern == "acme/test-*"
    assert captured.created_by_email == "admin@example.com"
    assert result["org_pattern"] == "acme"


async def test_remove_exclusion_rule_returns_true_on_success():
    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.rowcount = 1
    session.execute = AsyncMock(return_value=exec_result)
    session.commit = AsyncMock()

    with patch("pr_guardian.persistence.exclusions.async_session", _session_cm(session)):
        ok = await remove_exclusion_rule(str(uuid.uuid4()))

    assert ok is True


async def test_remove_exclusion_rule_returns_false_for_invalid_uuid():
    # No DB call should occur — the UUID parse guard must short-circuit.
    ok = await remove_exclusion_rule("not-a-uuid")
    assert ok is False


async def test_remove_exclusion_rule_returns_false_when_missing():
    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.rowcount = 0
    session.execute = AsyncMock(return_value=exec_result)
    session.commit = AsyncMock()

    with patch("pr_guardian.persistence.exclusions.async_session", _session_cm(session)):
        ok = await remove_exclusion_rule(str(uuid.uuid4()))

    assert ok is False


# ---------------------------------------------------------------------------
# Admin HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestExclusionRulesAdminApi:
    def test_list_returns_200(self, client):
        rule = {
            "id": str(uuid.uuid4()),
            "platform": "github",
            "org_pattern": "acme",
            "project_pattern": "",
            "repo_pattern": "",
            "created_by_email": "admin@x",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with patch(
            "pr_guardian.persistence.storage.list_exclusion_rules",
            AsyncMock(return_value=[rule]),
        ):
            resp = client.get("/api/admin/exclusion-rules")
        assert resp.status_code == 200
        assert resp.json()[0]["org_pattern"] == "acme"

    def test_create_requires_at_least_one_pattern(self, client):
        resp = client.post(
            "/api/admin/exclusion-rules",
            json={"platform": "github", "org_pattern": "", "repo_pattern": ""},
        )
        assert resp.status_code == 400

    def test_create_rejects_invalid_platform(self, client):
        resp = client.post(
            "/api/admin/exclusion-rules",
            json={"platform": "gitlab", "repo_pattern": "*"},
        )
        assert resp.status_code == 400

    def test_create_rejects_project_for_github(self, client):
        resp = client.post(
            "/api/admin/exclusion-rules",
            json={"platform": "github", "project_pattern": "X"},
        )
        assert resp.status_code == 400

    def test_create_succeeds(self, client):
        created = {
            "id": str(uuid.uuid4()),
            "platform": "github",
            "org_pattern": "acme",
            "project_pattern": "",
            "repo_pattern": "acme/test-*",
            "created_by_email": "admin@x",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with patch(
            "pr_guardian.persistence.storage.add_exclusion_rule",
            AsyncMock(return_value=created),
        ):
            resp = client.post(
                "/api/admin/exclusion-rules",
                json={
                    "platform": "github",
                    "org_pattern": "acme",
                    "repo_pattern": "acme/test-*",
                },
            )
        assert resp.status_code == 201
        assert resp.json()["repo_pattern"] == "acme/test-*"

    def test_delete_404_when_missing(self, client):
        with patch(
            "pr_guardian.persistence.storage.remove_exclusion_rule",
            AsyncMock(return_value=False),
        ):
            resp = client.delete(f"/api/admin/exclusion-rules/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_delete_200_when_removed(self, client):
        with patch(
            "pr_guardian.persistence.storage.remove_exclusion_rule",
            AsyncMock(return_value=True),
        ):
            resp = client.delete(f"/api/admin/exclusion-rules/{uuid.uuid4()}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Sync-time filter behavior
# ---------------------------------------------------------------------------


class TestSyncTimeFilter:
    def test_normalize_ado_merged_pr_prefers_created_at(self):
        from pr_guardian.core import pr_sync

        pr = {
            "number": 42,
            "title": "Merged PR",
            "user": {"login": "dev@example.com"},
            "created_at": "2026-05-01T10:00:00Z",
            "merged_at": "2026-05-03T12:00:00Z",
            "base": {"ref": "main"},
        }

        result = pr_sync._normalize_ado_merged_pr(
            pr,
            "https://dev.azure.com/acme",
            "Project",
            "Repo",
        )

        assert result["pr_created_at"] == "2026-05-01T10:00:00Z"
        assert result["pr_updated_at"] == "2026-05-03T12:00:00Z"

    async def test_github_sync_does_not_apply_browse_exclusion_rules(self):
        from pr_guardian.core import pr_sync

        adapter = AsyncMock()
        adapter.list_accessible_repos = AsyncMock(
            return_value=[
                {"full_name": "acme/keep", "owner": {"login": "acme"}, "clone_url": "u1"},
                {"full_name": "acme/test-skip", "owner": {"login": "acme"}, "clone_url": "u2"},
            ]
        )
        adapter.list_repo_open_prs = AsyncMock(return_value=[])
        adapter.close = AsyncMock()

        rules = [
            {
                "platform": "github",
                "org_pattern": "acme",
                "project_pattern": "",
                "repo_pattern": "acme/test-*",
            }
        ]

        connection = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "GitHub Browse",
            "platform": "github",
            "auth_kind": "github_app",
            "app_id": "12345",
            "installation_id": "98765",
            "org_url": None,
            "sync_enabled": True,
            "health_status": "healthy",
        }

        with (
            patch(
                "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
                AsyncMock(return_value=adapter),
            ),
            patch(
                "pr_guardian.core.pr_sync.storage.list_exclusion_rules",
                AsyncMock(return_value=rules),
            ),
        ):
            await pr_sync._sync_github(connection)

        # Exclusions are browse-only; sync still records what the Connection can see.
        called_repos = [c.args[0] for c in adapter.list_repo_open_prs.call_args_list]
        assert "acme/keep" in called_repos
        assert "acme/test-skip" in called_repos


# ---------------------------------------------------------------------------
# Connection iteration in run_pr_sync
# ---------------------------------------------------------------------------


class TestMultiPatSync:
    async def test_run_pr_sync_iterates_healthy_sync_connections(self, monkeypatch):
        """run_pr_sync calls _sync_github for each healthy GitHub App connection
        and _sync_ado (with token) for each healthy ADO connection."""
        from pr_guardian.core import pr_sync

        monkeypatch.setenv("GITHUB_TOKEN", "env-token-must-not-sync")
        monkeypatch.setenv("ADO_PAT", "ado-env-must-not-sync")
        monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/env")

        connections = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "org-a",
                "platform": "github",
                "auth_kind": "github_app",
                "sync_enabled": True,
                "health_status": "healthy",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "name": "org-b",
                "platform": "github",
                "auth_kind": "github_app",
                "sync_enabled": True,
                "health_status": "healthy",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "name": "ado-org",
                "platform": "ado",
                "org_url": "https://dev.azure.com/acme",
                "sync_enabled": True,
                "health_status": "healthy",
            },
        ]
        token_map = {
            "33333333-3333-3333-3333-333333333333": "ado-token",
        }

        async def _token(connection_id):
            return token_map.get(str(connection_id), "")

        github_connection_ids: list[str] = []
        ado_tokens: list[str] = []

        async def _fake_sync_github(connection):
            github_connection_ids.append(connection["id"])

        async def _fake_sync_ado(token, connection):
            ado_tokens.append(token)

        with (
            patch(
                "pr_guardian.core.pr_sync.storage.list_broad_sync_connections",
                AsyncMock(return_value=connections),
            ),
            patch("pr_guardian.core.pr_sync.storage.get_connection_token", side_effect=_token),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
            patch("pr_guardian.core.pr_sync._sync_ado", side_effect=_fake_sync_ado),
        ):
            await pr_sync.run_pr_sync()

        assert sorted(github_connection_ids) == [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
        assert ado_tokens == ["ado-token"]

    async def test_run_pr_sync_does_not_fall_back_to_env(self, monkeypatch):
        from pr_guardian.core import pr_sync

        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        monkeypatch.setenv("ADO_PAT", "ado-env-token")
        monkeypatch.setenv("ADO_ORG_URL", "https://dev.azure.com/env")

        github_syncs: list[dict] = []

        async def _fake_sync_github(connection):
            github_syncs.append(connection)

        with (
            patch(
                "pr_guardian.core.pr_sync.storage.list_broad_sync_connections",
                AsyncMock(return_value=[]),
            ),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
        ):
            await pr_sync.run_pr_sync()

        assert github_syncs == []

    async def test_run_pr_sync_skips_github_connections_without_app_auth(self, monkeypatch):
        """GitHub connections without auth_kind='github_app' are skipped with a warning.
        ADO connections still use token resolution and skip on missing/broken tokens."""
        from pr_guardian.core import pr_sync

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("ADO_PAT", raising=False)
        monkeypatch.delenv("ADO_ORG_URL", raising=False)

        connections = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "github-app-good",
                "platform": "github",
                "auth_kind": "github_app",
                "sync_enabled": True,
                "health_status": "healthy",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "name": "github-legacy-pat",
                "platform": "github",
                "auth_kind": None,  # PAT shape — must be skipped
                "sync_enabled": True,
                "health_status": "healthy",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "name": "ado-good",
                "platform": "ado",
                "org_url": "https://dev.azure.com/acme",
                "sync_enabled": True,
                "health_status": "healthy",
            },
            {
                "id": "44444444-4444-4444-4444-444444444444",
                "name": "ado-missing-token",
                "platform": "ado",
                "org_url": "https://dev.azure.com/acme",
                "sync_enabled": True,
                "health_status": "healthy",
            },
        ]

        async def _token(connection_id):
            if str(connection_id) == "44444444-4444-4444-4444-444444444444":
                return ""
            if str(connection_id) == "33333333-3333-3333-3333-333333333333":
                return "ado-tok"
            return ""

        github_synced: list[str] = []
        ado_synced: list[str] = []

        async def _fake_sync_github(connection):
            github_synced.append(connection["id"])

        async def _fake_sync_ado(token, connection):
            ado_synced.append(token)

        with (
            patch(
                "pr_guardian.core.pr_sync.storage.list_broad_sync_connections",
                AsyncMock(return_value=connections),
            ),
            patch("pr_guardian.core.pr_sync.storage.get_connection_token", side_effect=_token),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
            patch("pr_guardian.core.pr_sync._sync_ado", side_effect=_fake_sync_ado),
        ):
            await pr_sync.run_pr_sync()

        # Only the GitHub App connection is synced; PAT connection is skipped
        assert github_synced == ["11111111-1111-1111-1111-111111111111"]
        # ADO with good token is synced; ADO with missing token is skipped
        assert ado_synced == ["ado-tok"]
