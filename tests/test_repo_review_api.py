"""Tests for the /api/review/repo endpoint error-handling branches."""
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


class TestManualRepoReviewErrors:
    """build_repo_diff errors must surface as HTTP errors, not silent background failures."""

    def test_value_error_returns_400(self, client):
        """Repo-too-large ValueError → 400 so the UI can display the message."""
        with (
            patch(
                "pr_guardian.api.review.create_adapter",
                return_value=_mock_adapter(),
            ),
            patch(
                "pr_guardian.api.review.build_repo_diff",
                new_callable=AsyncMock,
                side_effect=ValueError("Repo has 500 reviewable files (limit: 300)."),
            ),
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github"},
            )

        assert resp.status_code == 400
        assert "500 reviewable files" in resp.json()["detail"]

    def test_generic_exception_returns_502(self, client):
        """Network or auth failures during diff build → 502 so the modal shows an error."""
        with (
            patch(
                "pr_guardian.api.review.create_adapter",
                return_value=_mock_adapter(),
            ),
            patch(
                "pr_guardian.api.review.build_repo_diff",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection timeout"),
            ),
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github"},
            )

        assert resp.status_code == 502
        assert "connection timeout" in resp.json()["detail"]

    def test_success_returns_queued(self, client):
        """Happy path: diff builds successfully → 200 with status=queued."""
        from pr_guardian.models.pr import Diff

        with (
            patch(
                "pr_guardian.api.review.create_adapter",
                return_value=_mock_adapter(),
            ),
            patch(
                "pr_guardian.api.review.build_repo_diff",
                new_callable=AsyncMock,
                return_value=(
                    Diff(files=[]),
                    {
                        "files_listed": 0,
                        "files_skipped_binary": 0,
                        "files_included": 0,
                        "files_truncated": 0,
                        "files_read_errors": 0,
                        "total_bytes": 0,
                    },
                ),
            ),
            patch("pr_guardian.api.review.asyncio.create_task"),
        ):
            resp = client.post(
                "/api/review/repo",
                json={"repo": "owner/repo", "platform": "github"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert resp.json()["repo"] == "owner/repo"

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
