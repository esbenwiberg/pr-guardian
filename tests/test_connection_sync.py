from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence import models
from pr_guardian.persistence.storage import (
    add_excluded_repo,
    create_connection,
    create_profile,
    create_readiness_candidate,
    create_repo_link,
    list_synced_prs,
    purge_prs_from_inactive_connections,
    upsert_synced_pr,
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _test_rsa_pem() -> str:
    """Generate a fresh RSA-2048 private key for test App connections."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class _FakeGitHubAdapter:
    """Fake GitHubAdapter that tracks whether it was created with App auth."""

    called_with_app_auth: list[bool] = []

    def __init__(self, token: str = "", *, app_auth=None):
        _FakeGitHubAdapter.called_with_app_auth.append(app_auth is not None)

    async def list_accessible_repos(self):
        return [
            {
                "full_name": "octo/service",
                "owner": {"login": "octo"},
                "clone_url": "https://github.com/octo/service.git",
                "default_branch": "main",
            }
        ]

    async def list_repo_open_prs(self, repo: str):
        assert repo == "octo/service"
        return [
            {
                "number": 42,
                "title": "Connection-backed browse sync",
                "user": {"login": "alice"},
                "html_url": "https://github.com/octo/service/pull/42",
                "head": {"ref": "feat/browse-sync"},
                "base": {"ref": "main"},
                "draft": False,
                "mergeable": True,
                "requested_reviewers": [],
                "assignees": [],
                "comments": 0,
                "review_comments": 0,
                "created_at": "2026-05-31T12:00:00Z",
                "updated_at": "2026-05-31T12:05:00Z",
            }
        ]

    async def fetch_merged_prs(self, repo: str, *, since: str, base: str):
        return []

    async def close(self):
        return None


class _FailingADOAdapter:
    def __init__(self, *args, **kwargs):
        raise AssertionError("sync-disabled ADO Connection must not be used for broad sync")


@pytest.mark.asyncio
async def test_pr_sync_uses_only_healthy_sync_enabled_connections():
    from pr_guardian.core import pr_sync

    engine, factory = await _make_session_factory()
    _FakeGitHubAdapter.called_with_app_auth = []
    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            profile = await create_profile("Standard", settings={"severity_floor": "medium"})
            # GitHub App connection — the only kind that triggers sync now
            pem = _test_rsa_pem()
            github_sync = await create_connection(
                "Healthy GitHub App Sync",
                platform="github",
                auth_kind="github_app",
                app_id="12345",
                installation_id="98765",
                private_key=pem,
                sync_enabled=True,
                health_status="healthy",
            )
            ado_manual = await create_connection(
                "Healthy ADO Manual",
                platform="ado",
                token="ado-token-manual",
                org_url="https://dev.azure.com/example",
                sync_enabled=False,
                health_status="healthy",
            )
            # Unhealthy GitHub App connection — must be skipped (health_status filter)
            await create_connection(
                "Unhealthy GitHub App Sync",
                platform="github",
                auth_kind="github_app",
                app_id="99999",
                installation_id="11111",
                private_key=pem,
                sync_enabled=True,
                health_status="unhealthy",
            )

            link = await create_repo_link(
                platform="ado",
                org_url="https://dev.azure.com/example",
                project="Platform",
                repo_name="manual-repo",
                repo_url="https://dev.azure.com/example/Platform/_git/manual-repo",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(ado_manual["id"]),
                auto_review_enabled=True,
            )

            with (
                patch("pr_guardian.platform.github.GitHubAdapter", _FakeGitHubAdapter),
                patch("pr_guardian.platform.ado.ADOAdapter", _FailingADOAdapter),
            ):
                await pr_sync.run_pr_sync()

            # Exactly one adapter was created, and it used App auth (not a PAT token)
            assert _FakeGitHubAdapter.called_with_app_auth == [True], (
                "Expected exactly one GitHubAdapter created via App auth"
            )
            assert link["connection_id"] == ado_manual["id"]

            async with factory() as session:
                synced_pr = (
                    await session.scalars(
                        select(models.SyncedPRRow).where(models.SyncedPRRow.pr_id == "42")
                    )
                ).one()
                sync_source = (
                    await session.scalars(
                        select(models.SyncSourceRow).where(
                            models.SyncSourceRow.repo == "octo/service"
                        )
                    )
                ).one()

            assert str(synced_pr.connection_id) == github_sync["id"]
            assert synced_pr.connection_snapshot["name"] == "Healthy GitHub App Sync"
            assert str(sync_source.connection_id) == github_sync["id"]
            assert sync_source.connection_snapshot["platform"] == "github"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exclusions_hide_browse_rows_but_not_linked_candidates():
    engine, factory = await _make_session_factory()
    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            profile = await create_profile("Readiness Profile")
            connection = await create_connection(
                "GitHub Link",
                platform="github",
                token="gh-token-linked",
                health_status="healthy",
                sync_enabled=False,
            )
            link = await create_repo_link(
                platform="github",
                repo_owner="octo",
                repo_name="hidden",
                repo_url="https://github.com/octo/hidden",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
                auto_review_enabled=True,
            )
            await upsert_synced_pr(
                {
                    "platform": "github",
                    "pr_id": "7",
                    "org": "octo",
                    "project": "",
                    "repo": "octo/hidden",
                    "title": "Hidden from browse only",
                    "author": "alice",
                    "pr_url": "https://github.com/octo/hidden/pull/7",
                    "source_branch": "feat/hidden",
                    "target_branch": "main",
                    "connection_id": uuid.UUID(connection["id"]),
                    "connection_snapshot": connection,
                    "pr_created_at": datetime.now(timezone.utc),
                    "pr_updated_at": datetime.now(timezone.utc),
                }
            )
            await add_excluded_repo("github", "octo", "", "octo/hidden", "admin@example.com")

            browse_items, total = await list_synced_prs()
            candidate = await create_readiness_candidate(
                repo_link_id=uuid.UUID(link["id"]),
                pr_id="7",
                pr_url="https://github.com/octo/hidden/pull/7",
                head_sha="abc123",
                state="waiting",
            )

            assert total == 0
            assert browse_items == []
            assert candidate["repo_link_id"] == link["id"]
            assert candidate["repo"] == "octo/hidden"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_purge_prs_from_inactive_connections():
    """PRs from archived or sync-disabled connections are removed; active ones stay."""
    engine, factory = await _make_session_factory()
    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            active_conn = await create_connection(
                "Active Sync",
                platform="github",
                token="tok-active",
                sync_enabled=True,
                health_status="healthy",
            )
            inactive_conn = await create_connection(
                "Disabled Sync",
                platform="github",
                token="tok-inactive",
                sync_enabled=False,
                health_status="healthy",
            )

            def _pr(pr_id: str, connection: dict, status: str = "pending") -> dict:
                return {
                    "platform": "github",
                    "pr_id": pr_id,
                    "org": "org",
                    "project": "",
                    "repo": "org/repo",
                    "title": f"PR {pr_id}",
                    "author": "alice",
                    "pr_url": f"https://github.com/org/repo/pull/{pr_id}",
                    "source_branch": "feat",
                    "target_branch": "main",
                    "approval_status": status,
                    "connection_id": uuid.UUID(connection["id"]),
                    "connection_snapshot": connection,
                    "pr_created_at": datetime.now(timezone.utc),
                    "pr_updated_at": datetime.now(timezone.utc),
                }

            await upsert_synced_pr(_pr("1", active_conn))
            await upsert_synced_pr(_pr("2", inactive_conn))
            await upsert_synced_pr(_pr("3", inactive_conn, status="merged"))

            purged = await purge_prs_from_inactive_connections()
            items, total = await list_synced_prs()

            assert purged == 1, "only the inactive-connection pending PR should be removed"
            assert total == 1
            assert items[0]["pr_id"] == "1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_opted_prs_stay_in_pull_requests_api_not_reviews(monkeypatch):
    engine, factory = await _make_session_factory()
    try:
        monkeypatch.setenv("GUARDIAN_DEV_ADMIN", "1")
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            connection = await create_connection(
                "Browse Only GitHub",
                platform="github",
                token="gh-token-browse",
                health_status="healthy",
                sync_enabled=True,
            )
            await upsert_synced_pr(
                {
                    "platform": "github",
                    "pr_id": "88",
                    "org": "browse",
                    "project": "",
                    "repo": "browse/only",
                    "title": "Unlinked browse-only pull request",
                    "author": "alice",
                    "pr_url": "https://github.com/browse/only/pull/88",
                    "source_branch": "feat/only",
                    "target_branch": "main",
                    "connection_id": uuid.UUID(connection["id"]),
                    "connection_snapshot": connection,
                    "pr_created_at": datetime.now(timezone.utc),
                    "pr_updated_at": datetime.now(timezone.utc),
                }
            )
            async with factory() as session:
                session.add(
                    models.ReviewRow(
                        pr_id="1",
                        repo="reviewed/repo",
                        platform="github",
                        title="Existing review row",
                        pr_url="https://github.com/reviewed/repo/pull/1",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()

            with TestClient(app) as client:
                page_response = client.get("/pull-requests")
                redirect_response = client.get("/pr-dashboard", follow_redirects=False)
                pull_response = client.get("/api/prs")
                reviews_response = client.get("/api/reviews/queue")

            assert page_response.status_code == 200
            assert "Pull Requests" in page_response.text
            assert redirect_response.status_code == 302
            assert redirect_response.headers["location"] == "/pull-requests"
            assert pull_response.status_code == 200
            pull_titles = [item["title"] for item in pull_response.json()["items"]]
            assert "Unlinked browse-only pull request" in pull_titles

            assert reviews_response.status_code == 200
            review_titles = [item["title"] for item in reviews_response.json()["items"]]
            assert "Unlinked browse-only pull request" not in review_titles
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_github_guarded_isolates_per_connection_errors():
    """_sync_github_guarded must not propagate exceptions — logs and returns normally."""
    from pr_guardian.core import pr_sync

    connection = {"id": "conn-abc", "name": "Failing GitHub App"}

    with patch.object(
        pr_sync,
        "_sync_github",
        new=AsyncMock(side_effect=RuntimeError("network failure")),
    ):
        # Must not raise — exception isolation is the point of the wrapper
        await pr_sync._sync_github_guarded(connection)


@pytest.mark.asyncio
async def test_github_sync_ignores_github_token_without_app_connection(monkeypatch):
    """Even with GITHUB_TOKEN set in the environment, the sync path must not use it.

    A healthy, sync-enabled GitHub connection that lacks ``auth_kind='github_app'``
    (i.e. a legacy PAT connection) must be skipped — not silently promoted with the
    env token.  GitHubAdapter must never be instantiated from the env fallback.
    """
    from pr_guardian.core import pr_sync

    engine, factory = await _make_session_factory()
    adapter_instantiated: list[dict] = []

    class _CapturingAdapter:
        def __init__(self, *args, **kwargs):
            adapter_instantiated.append({"args": args, "kwargs": kwargs})

    monkeypatch.setenv("GITHUB_TOKEN", "env-token-must-not-be-used")

    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            # Create a healthy sync-enabled GitHub connection WITHOUT auth_kind='github_app'
            # (legacy PAT shape — auth_kind is NULL)
            await create_connection(
                "Legacy PAT GitHub",
                platform="github",
                token="gh-token-legacy-pat",
                sync_enabled=True,
                health_status="healthy",
            )

            with patch("pr_guardian.platform.github.GitHubAdapter", _CapturingAdapter):
                await pr_sync.run_pr_sync()

            # The legacy PAT connection must have been skipped — GitHubAdapter never
            # instantiated and GITHUB_TOKEN never used.
            assert adapter_instantiated == [], (
                "GitHubAdapter was created from a non-App connection or env token. "
                f"Calls: {adapter_instantiated}"
            )
    finally:
        await engine.dispose()
