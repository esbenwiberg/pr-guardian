"""Tests for the POST /api/review endpoint."""

from __future__ import annotations

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


class TestPRReviewValidation:
    """Input validation returns errors before any async work."""

    def test_bad_url_returns_400(self, client):
        resp = client.post("/api/review", json={"pr_url": "https://not-a-pr-url.com"})
        assert resp.status_code == 400

    def test_post_comment_field_rejected_422(self, client):
        """Old post_comment bool must be rejected — extra='forbid' is in effect."""
        resp = client.post(
            "/api/review",
            json={"pr_url": "https://github.com/owner/repo/pull/1", "post_comment": True},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert any(e.get("type") == "extra_forbidden" for e in detail)

    def test_named_pat_not_found_returns_404(self, client):
        with patch(
            "pr_guardian.api.review.create_github_adapter",
            new_callable=AsyncMock,
            side_effect=LookupError("PAT 'missing' not found"),
        ):
            resp = client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/1", "pat_name": "missing"},
            )
        assert resp.status_code == 404


class TestPRReviewQueueing:
    """Valid requests are accepted (202) and queued without hitting the platform API."""

    def test_comment_mode_inline_accepted(self, client):
        """comment_mode=inline must return 202 without fetching the PR from GitHub."""
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("pr_guardian.api.review.asyncio.create_task") as mock_task,
        ):
            resp = client.post(
                "/api/review",
                json={
                    "pr_url": "https://github.com/esbenwiberg/pr-guardian/pull/101",
                    "comment_mode": "inline",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pr_id"] == "101"
        assert data["repo"] == "esbenwiberg/pr-guardian"
        mock_task.assert_called_once()

    def test_response_includes_review_uuid_when_db_available(self, client):
        """The response must carry the review UUID so the UI can subscribe to live events.

        Without it, the /live page can't filter SSE events (they're keyed by UUID)
        and the fallback link to /reviews/{id} returns 422.
        """
        import uuid as _uuid

        mock_adapter = _mock_adapter()
        fake_id = _uuid.uuid4()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch("pr_guardian.api.review.asyncio.create_task"),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=fake_id,
            ),
        ):
            resp = client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/9"},
            )

        assert resp.status_code == 200
        assert resp.json()["review_id"] == str(fake_id)

    def test_response_review_id_null_when_db_unavailable(self, client):
        """If the DB record can't be created, the trigger still succeeds — but the UI
        knows it can't track the run live."""
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch("pr_guardian.api.review.asyncio.create_task"),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                side_effect=RuntimeError("no db"),
            ),
        ):
            resp = client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/9"},
            )

        assert resp.status_code == 200
        assert resp.json()["review_id"] is None

    def test_comment_mode_summary_accepted(self, client):
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("pr_guardian.api.review.asyncio.create_task"),
        ):
            resp = client.post(
                "/api/review",
                json={
                    "pr_url": "https://github.com/owner/repo/pull/5",
                    "comment_mode": "summary",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_comment_mode_none_accepted(self, client):
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("pr_guardian.api.review.asyncio.create_task"),
        ):
            resp = client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/5"},
            )
        assert resp.status_code == 200

    def test_background_task_passes_stub_not_hydrated_pr(self, client):
        """_run_review_background must be called with the stub (no live network call)."""
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("pr_guardian.api.review.asyncio.create_task") as mock_task,
        ):
            client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/7"},
            )

        coro = mock_task.call_args[0][0]
        assert coro.__qualname__ == "_run_review_background"

    def test_dry_run_returns_without_queuing(self, client):
        mock_adapter = _mock_adapter()
        with (
            patch(
                "pr_guardian.api.review.create_github_adapter",
                new_callable=AsyncMock,
                return_value=mock_adapter,
            ),
            patch(
                "pr_guardian.persistence.storage.create_review_record",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("pr_guardian.api.review.asyncio.create_task") as mock_task,
        ):
            resp = client.post(
                "/api/review",
                json={"pr_url": "https://github.com/owner/repo/pull/3", "dry_run": True},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "dry_run_accepted"
        mock_task.assert_not_called()


class TestPRReviewBackground:
    """Background task hydrates PR and handles errors gracefully."""

    @pytest.mark.asyncio
    async def test_hydrate_failure_logs_and_returns(self):
        from pr_guardian.api.review import _run_review_background
        from pr_guardian.models.pr import Platform, PlatformPR

        stub = PlatformPR(
            platform=Platform.GITHUB,
            pr_id="1",
            repo="owner/repo",
            repo_url="",
            source_branch="",
            target_branch="",
            author="",
            title="",
            head_commit_sha="",
            org="owner",
        )
        adapter = _mock_adapter()

        with patch(
            "pr_guardian.api.review._hydrate_pr",
            new_callable=AsyncMock,
            side_effect=RuntimeError("creds expired"),
        ):
            await _run_review_background(
                stub, adapter, "none", "http://localhost", platform_name="github"
            )
        # Should not raise; error is logged and swallowed

    @pytest.mark.asyncio
    async def test_hydrate_success_calls_run_review(self):
        from pr_guardian.api.review import _run_review_background
        from pr_guardian.models.pr import Platform, PlatformPR

        stub = PlatformPR(
            platform=Platform.GITHUB,
            pr_id="1",
            repo="owner/repo",
            repo_url="",
            source_branch="",
            target_branch="",
            author="",
            title="",
            head_commit_sha="",
            org="owner",
        )
        import dataclasses

        hydrated = dataclasses.replace(stub, title="My PR", author="alice")
        adapter = _mock_adapter()

        with (
            patch(
                "pr_guardian.api.review._hydrate_pr", new_callable=AsyncMock, return_value=hydrated
            ),
            patch("pr_guardian.api.review.run_review", new_callable=AsyncMock) as mock_run,
        ):
            await _run_review_background(
                stub, adapter, "inline", "http://localhost", platform_name="github"
            )

        mock_run.assert_awaited_once()
        assert mock_run.call_args.kwargs.get("comment_mode") == "inline"
