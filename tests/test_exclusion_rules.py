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
        rules = [{"platform": "github", "org_pattern": "acme", "project_pattern": "", "repo_pattern": "acme/foo"}]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/foo") is True

    def test_org_wildcard_matches_all_repos(self):
        rules = [{"platform": "github", "org_pattern": "acme", "project_pattern": "", "repo_pattern": ""}]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/foo") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/bar") is True
        assert repo_matches_rules(rules, "github", "other", "", "other/foo") is False

    def test_repo_pattern_wildcard(self):
        rules = [{"platform": "github", "org_pattern": "acme", "project_pattern": "", "repo_pattern": "acme/test-*"}]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/test-foo") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/prod-foo") is False

    def test_platform_isolation(self):
        rules = [{"platform": "github", "org_pattern": "", "project_pattern": "", "repo_pattern": "*"}]
        assert repo_matches_rules(rules, "github", "x", "", "x/y") is True
        assert repo_matches_rules(rules, "ado", "x", "p", "y") is False

    def test_ado_project_pattern(self):
        rules = [{"platform": "ado", "org_pattern": "", "project_pattern": "Legacy*", "repo_pattern": ""}]
        assert repo_matches_rules(rules, "ado", "https://dev.azure.com/x", "LegacyApps", "foo") is True
        assert repo_matches_rules(rules, "ado", "https://dev.azure.com/x", "ModernApps", "foo") is False

    def test_question_mark_wildcard(self):
        rules = [{"platform": "github", "org_pattern": "", "project_pattern": "", "repo_pattern": "acme/v?"}]
        assert repo_matches_rules(rules, "github", "acme", "", "acme/v1") is True
        assert repo_matches_rules(rules, "github", "acme", "", "acme/v12") is False

    def test_any_rule_match_is_enough(self):
        rules = [
            {"platform": "github", "org_pattern": "miss", "project_pattern": "", "repo_pattern": ""},
            {"platform": "github", "org_pattern": "acme", "project_pattern": "", "repo_pattern": ""},
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

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        result = await list_exclusion_rules()

    assert len(result) == 1
    assert result[0]["org_pattern"] == "acme"
    assert result[0]["repo_pattern"] == "acme/test-*"


async def test_list_exclusion_rules_db_failure_returns_empty():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
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

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
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

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
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

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
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
    async def test_github_sync_skips_repos_matching_rule(self):
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

        rules = [{
            "platform": "github",
            "org_pattern": "acme",
            "project_pattern": "",
            "repo_pattern": "acme/test-*",
        }]

        with (
            patch("pr_guardian.platform.github.GitHubAdapter", return_value=adapter),
            patch("pr_guardian.core.pr_sync.storage.list_exclusion_rules", AsyncMock(return_value=rules)),
        ):
            await pr_sync._sync_github("token-xyz")

        # Only the non-skipped repo should have its PRs queried.
        called_repos = [c.args[0] for c in adapter.list_repo_open_prs.call_args_list]
        assert "acme/keep" in called_repos
        assert "acme/test-skip" not in called_repos


# ---------------------------------------------------------------------------
# Multi-PAT iteration in run_pr_sync
# ---------------------------------------------------------------------------


class TestMultiPatSync:
    async def test_run_pr_sync_iterates_all_pats(self, monkeypatch):
        from pr_guardian.core import pr_sync

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("ADO_PAT", raising=False)
        monkeypatch.delenv("ADO_ORG_URL", raising=False)

        pats = [
            {"name": "org-a"},
            {"name": "org-b"},
            {"name": "org-c"},
        ]
        token_map = {"org-a": "tok-a", "org-b": "tok-b", "org-c": "tok-c"}

        async def _resolve(pat_name=None):
            return token_map[pat_name]

        called_tokens: list[str] = []

        async def _fake_sync_github(token, pat_label="env"):
            called_tokens.append(token)

        with (
            patch("pr_guardian.core.pr_sync.storage.list_github_pats", AsyncMock(return_value=pats)),
            patch("pr_guardian.core.pr_sync.storage.resolve_github_token", side_effect=_resolve),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
        ):
            await pr_sync.run_pr_sync()

        assert sorted(called_tokens) == ["tok-a", "tok-b", "tok-c"]

    async def test_run_pr_sync_falls_back_to_env_when_no_db_pats(self, monkeypatch):
        from pr_guardian.core import pr_sync

        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        monkeypatch.delenv("ADO_PAT", raising=False)
        monkeypatch.delenv("ADO_ORG_URL", raising=False)

        called_tokens: list[str] = []

        async def _fake_sync_github(token, pat_label="env"):
            called_tokens.append(token)

        with (
            patch("pr_guardian.core.pr_sync.storage.list_github_pats", AsyncMock(return_value=[])),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
        ):
            await pr_sync.run_pr_sync()

        assert called_tokens == ["env-token"]

    async def test_run_pr_sync_skips_pat_with_resolve_failure(self, monkeypatch):
        from pr_guardian.core import pr_sync

        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("ADO_PAT", raising=False)
        monkeypatch.delenv("ADO_ORG_URL", raising=False)

        pats = [{"name": "good"}, {"name": "broken"}]

        async def _resolve(pat_name=None):
            if pat_name == "broken":
                raise LookupError("corrupt")
            return "tok-good"

        called_tokens: list[str] = []

        async def _fake_sync_github(token, pat_label="env"):
            called_tokens.append(token)

        with (
            patch("pr_guardian.core.pr_sync.storage.list_github_pats", AsyncMock(return_value=pats)),
            patch("pr_guardian.core.pr_sync.storage.resolve_github_token", side_effect=_resolve),
            patch("pr_guardian.core.pr_sync._sync_github", side_effect=_fake_sync_github),
        ):
            await pr_sync.run_pr_sync()

        assert called_tokens == ["tok-good"]
