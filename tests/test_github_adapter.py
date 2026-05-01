"""Unit tests for GitHubAdapter.fetch_pr_body_and_commits."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.github import GitHubAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pr(pr_id: str = "42", repo: str = "owner/repo") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id=pr_id,
        repo=repo,
        repo_url="https://github.com/owner/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="My PR",
        head_commit_sha="abc123",
    )


def _resp(json_data: dict | list, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _adapter(*responses: MagicMock) -> GitHubAdapter:
    adapter = GitHubAdapter(token="test-token")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(responses))
    adapter._client = mock_client
    return adapter


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_succeed():
    pr_json = {"body": "My PR description"}
    commits_json = [
        {"commit": {"message": "feat: add feature\n\nDetails here"}},
        {"commit": {"message": "fix: small tweak"}},
    ]
    body, commits = await _adapter(_resp(pr_json), _resp(commits_json)).fetch_pr_body_and_commits(_pr())
    assert body == "My PR description"
    assert commits == ["feat: add feature", "fix: small tweak"]


@pytest.mark.asyncio
async def test_multiline_commit_only_first_line():
    commits_json = [{"commit": {"message": "feat: headline\n\nExpanded description here."}}]
    _, commits = await _adapter(_resp({"body": ""}), _resp(commits_json)).fetch_pr_body_and_commits(_pr())
    assert commits == ["feat: headline"]


@pytest.mark.asyncio
async def test_null_body_field_returned_as_empty():
    """GitHub returns body=null for PRs with no description."""
    pr_json = {"body": None}
    body, _ = await _adapter(_resp(pr_json), _resp([])).fetch_pr_body_and_commits(_pr())
    assert body == ""


@pytest.mark.asyncio
async def test_commit_without_message_is_skipped():
    commits_json = [
        {"commit": {}},
        {"commit": {"message": "valid commit"}},
    ]
    _, commits = await _adapter(_resp({"body": "x"}), _resp(commits_json)).fetch_pr_body_and_commits(_pr())
    assert commits == ["valid commit"]


# ---------------------------------------------------------------------------
# Partial-failure paths — each half degrades gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_body_non200_commits_still_returned():
    commits_json = [{"commit": {"message": "fix: something"}}]
    body, commits = await _adapter(_resp({}, 404), _resp(commits_json)).fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == ["fix: something"]


@pytest.mark.asyncio
async def test_commits_non200_body_still_returned():
    body, commits = await _adapter(_resp({"body": "Some description"}), _resp([], 500)).fetch_pr_body_and_commits(_pr())
    assert body == "Some description"
    assert commits == []


@pytest.mark.asyncio
async def test_both_non200_returns_empty():
    body, commits = await _adapter(_resp({}, 503), _resp({}, 503)).fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == []


@pytest.mark.asyncio
async def test_network_error_is_handled():
    """A transport-level error (not HTTP status) is also caught."""
    adapter = GitHubAdapter(token="test-token")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
    adapter._client = mock_client
    body, commits = await adapter.fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == []
