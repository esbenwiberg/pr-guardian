from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

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
    upsert_synced_pr,
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class _FakeGitHubAdapter:
    called_tokens: list[str] = []

    def __init__(self, token: str):
        self.called_tokens.append(token)

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
    _FakeGitHubAdapter.called_tokens = []
    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.persistence.exclusions.async_session", lambda: factory()),
        ):
            profile = await create_profile("Standard", settings={"severity_floor": "medium"})
            github_sync = await create_connection(
                "Healthy GitHub Sync",
                platform="github",
                token="gh-token-connection",
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
            await create_connection(
                "Unhealthy GitHub Sync",
                platform="github",
                token="gh-token-unhealthy",
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

            assert _FakeGitHubAdapter.called_tokens == ["gh-token-connection"]
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
            assert synced_pr.connection_snapshot["name"] == "Healthy GitHub Sync"
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
