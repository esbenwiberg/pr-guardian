"""Tests for the /api/review/repo endpoint."""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pr_guardian.main import app


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _mock_adapter():
    adapter = MagicMock()
    adapter.close = AsyncMock()
    return adapter


class TestManualRepoReviewValidation:
    """Input validation must return 400 before any async work starts."""

    def test_unsupported_platform_returns_400(self, client):
        resp = client.post(
            "/api/review/repo",
            json={"repo": "owner/repo", "platform": "bitbucket"},
        )
        assert resp.status_code == 400

    def test_missing_slash_in_repo_returns_400(self, client):
        resp = client.post(
            "/api/review/repo",
            json={"repo": "noslash", "platform": "github"},
        )
        assert resp.status_code == 400



class TestManualRepoReviewQueueing:
    """With credentials present the endpoint always returns 200 (fire-and-forget)."""

    def test_success_returns_queued(self, client):
        """Happy path: credentials set → 200 with status=queued and task is scheduled."""
        mock_adapter = _mock_adapter()
        with (
            patch("pr_guardian.api.review.create_github_adapter", new_callable=AsyncMock, return_value=mock_adapter),
            patch("pr_guardian.api.review.asyncio.create_task") as mock_task,
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["repo"] == "owner/repo"
        assert data["platform"] == "github"
        mock_task.assert_called_once()

    def test_create_task_called_with_background_fn(self, client):
        """Verify that create_task is invoked, not just fire-and-forget ignored."""
        mock_adapter = _mock_adapter()
        with (
            patch("pr_guardian.api.review.create_github_adapter", new_callable=AsyncMock, return_value=mock_adapter),
            patch("pr_guardian.api.review.asyncio.create_task") as mock_task,
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github"},
            )

        assert resp.status_code == 200
        mock_task.assert_called_once()
        # The coroutine passed to create_task must be from _run_repo_review_background
        coro = mock_task.call_args[0][0]
        assert coro.__qualname__ == "_run_repo_review_background"

    def test_queued_response_includes_ref(self, client):
        with (
            patch("pr_guardian.api.review.create_github_adapter", new_callable=AsyncMock, return_value=_mock_adapter()),
            patch("pr_guardian.api.review.asyncio.create_task"),
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github", "ref": "main"},
            )

        assert resp.status_code == 200
        assert resp.json()["ref"] == "main"


class TestRepoReviewBackground:
    """Background task must surface diff-build errors via DB, not silently discard them."""

    @pytest.mark.asyncio
    async def test_diff_failure_marks_review_as_failed(self):
        """If build_repo_diff raises, the DB record is marked as error."""
        from pr_guardian.api.review import _run_repo_review_background
        from pr_guardian.models.pr import Diff

        test_id = uuid.uuid4()
        mock_storage = MagicMock()
        mock_storage.create_review_record = AsyncMock(return_value=test_id)
        mock_storage.update_review_stage = AsyncMock()
        mock_storage.mark_review_failed = AsyncMock()
        adapter = _mock_adapter()

        with (
            patch("pr_guardian.core.orchestrator.get_storage",
                  return_value=mock_storage),
            patch("pr_guardian.api.review.build_repo_diff",
                  new_callable=AsyncMock,
                  side_effect=ValueError("Repo too large")),
        ):
            await _run_repo_review_background(
                "owner/repo", "github", adapter, "HEAD", 300,
            )

        mock_storage.mark_review_failed.assert_awaited_once()
        args = mock_storage.mark_review_failed.call_args
        assert "Repo too large" in str(args)

    @pytest.mark.asyncio
    async def test_success_calls_run_review_with_existing_id(self):
        """On success, run_review receives the pre-created review_db_id."""
        from pr_guardian.api.review import _run_repo_review_background
        from pr_guardian.models.pr import Diff

        test_id = uuid.uuid4()
        mock_storage = MagicMock()
        mock_storage.create_review_record = AsyncMock(return_value=test_id)
        mock_storage.update_review_stage = AsyncMock()
        adapter = _mock_adapter()
        empty_meta = {
            "files_listed": 0, "files_skipped_binary": 0,
            "files_included": 0, "files_truncated": 0,
            "files_read_errors": 0, "total_bytes": 0,
        }

        with (
            patch("pr_guardian.core.orchestrator.get_storage",
                  return_value=mock_storage),
            patch("pr_guardian.api.review.build_repo_diff",
                  new_callable=AsyncMock,
                  return_value=(Diff(files=[]), empty_meta)),
            patch("pr_guardian.api.review.run_review",
                  new_callable=AsyncMock) as mock_run,
        ):
            await _run_repo_review_background(
                "owner/repo", "github", adapter, "HEAD", 300,
            )

        mock_run.assert_awaited_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("existing_review_db_id") == test_id
        assert kwargs.get("post_comment") is False
        assert kwargs.get("skip_platform_side_effects") is True
