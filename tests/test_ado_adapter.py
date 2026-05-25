"""Unit tests for ADOAdapter.fetch_pr_body_and_commits."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.ado import ADOAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pr(pr_id: str = "99", repo: str = "my-repo", project: str = "my-project") -> PlatformPR:
    return PlatformPR(
        platform=Platform.ADO,
        pr_id=pr_id,
        repo=repo,
        repo_url="https://dev.azure.com/org/project/_git/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="ADO PR",
        head_commit_sha="abc123",
        org="my-org",
        project=project,
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


def _adapter(*responses: MagicMock) -> ADOAdapter:
    adapter = ADOAdapter(pat="test-pat", org_url="https://dev.azure.com/org")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(responses))
    adapter._client = mock_client
    return adapter


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_pr_body_and_commit_subjects_when_ado_endpoints_succeed():
    pr_json = {"description": "ADO PR description"}
    commits_json = {
        "value": [
            {"comment": "feat: add feature\n\nBody text"},
            {"comment": "fix: tweak"},
        ]
    }
    body, commits = await _adapter(_resp(pr_json), _resp(commits_json)).fetch_pr_body_and_commits(
        _pr()
    )
    assert body == "ADO PR description"
    assert commits == ["feat: add feature", "fix: tweak"]


@pytest.mark.asyncio
async def test_uses_description_not_body_field():
    """ADO uses 'description' key, not 'body'."""
    pr_json = {"body": "wrong field", "description": "correct field"}
    body, _ = await _adapter(_resp(pr_json), _resp({"value": []})).fetch_pr_body_and_commits(_pr())
    assert body == "correct field"


@pytest.mark.asyncio
async def test_uses_comment_field_for_commits():
    """ADO commit objects have a top-level 'comment' key, not nested 'commit.message'."""
    commits_json = {"value": [{"comment": "ado style commit message"}]}
    _, commits = await _adapter(
        _resp({"description": "x"}), _resp(commits_json)
    ).fetch_pr_body_and_commits(_pr())
    assert commits == ["ado style commit message"]


@pytest.mark.asyncio
async def test_multiline_commit_only_first_line():
    commits_json = {"value": [{"comment": "feat: headline\n\nSome detail"}]}
    _, commits = await _adapter(
        _resp({"description": ""}), _resp(commits_json)
    ).fetch_pr_body_and_commits(_pr())
    assert commits == ["feat: headline"]


@pytest.mark.asyncio
async def test_commit_without_comment_field_is_skipped():
    commits_json = {
        "value": [
            {"commitId": "abc"},  # no comment key
            {"comment": "valid commit"},
        ]
    }
    _, commits = await _adapter(
        _resp({"description": "x"}), _resp(commits_json)
    ).fetch_pr_body_and_commits(_pr())
    assert commits == ["valid commit"]


@pytest.mark.asyncio
async def test_null_description_returned_as_empty():
    pr_json = {"description": None}
    body, _ = await _adapter(_resp(pr_json), _resp({"value": []})).fetch_pr_body_and_commits(_pr())
    assert body == ""


# ---------------------------------------------------------------------------
# Partial-failure paths — each half degrades gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_body_non200_commits_still_returned():
    commits_json = {"value": [{"comment": "fix: something"}]}
    body, commits = await _adapter(_resp({}, 404), _resp(commits_json)).fetch_pr_body_and_commits(
        _pr()
    )
    assert body == ""
    assert commits == ["fix: something"]


@pytest.mark.asyncio
async def test_commits_non200_body_still_returned():
    body, commits = await _adapter(
        _resp({"description": "desc"}), _resp({}, 500)
    ).fetch_pr_body_and_commits(_pr())
    assert body == "desc"
    assert commits == []


@pytest.mark.asyncio
async def test_fetch_pr_body_and_commits_returns_empty_when_both_ado_endpoints_fail():
    body, commits = await _adapter(_resp({}, 503), _resp({}, 503)).fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == []


@pytest.mark.asyncio
async def test_network_error_is_handled():
    adapter = ADOAdapter(pat="test-pat", org_url="https://dev.azure.com/org")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    adapter._client = mock_client
    body, commits = await adapter.fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == []


# ---------------------------------------------------------------------------
# Body-already-cached path — pre-populated pr.body skips the PR GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_body_already_set_skips_pr_get():
    """When pr.body is pre-populated by _hydrate_pr, no PR GET is issued; only
    the commits endpoint is called (one mock response, not two)."""
    commits_json = {"value": [{"comment": "feat: cached"}]}
    pr_with_body = PlatformPR(
        platform=Platform.ADO,
        pr_id="99",
        repo="my-repo",
        repo_url="https://dev.azure.com/org/project/_git/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="ADO PR",
        head_commit_sha="abc123",
        org="my-org",
        project="my-project",
        body="already fetched description",
    )
    body, commits = await _adapter(_resp(commits_json)).fetch_pr_body_and_commits(pr_with_body)
    assert body == "already fetched description"
    assert commits == ["feat: cached"]


# ---------------------------------------------------------------------------
# request_changes — vote + comment contract
# ---------------------------------------------------------------------------


def _adapter_rw(put_resp: MagicMock, post_resp: MagicMock | None = None) -> ADOAdapter:
    """Adapter wired for request_changes: mock put (vote) and post (comment)."""
    adapter = ADOAdapter(pat="test-pat", org_url="https://dev.azure.com/org")
    mock_client = AsyncMock()
    # connectionData call for _get_current_user_id
    conn_resp = _resp({"authenticatedUser": {"id": "user-guid-1234"}})
    mock_client.get = AsyncMock(return_value=conn_resp)
    mock_client.put = AsyncMock(return_value=put_resp)
    mock_client.post = AsyncMock(return_value=post_resp or _resp({}))
    adapter._client = mock_client
    return adapter


@pytest.mark.asyncio
async def test_request_changes_puts_reject_vote():
    adapter = _adapter_rw(_resp({}))
    pr = _pr()
    await adapter.request_changes(pr, "Please fix the auth flow.")
    adapter._client.put.assert_awaited_once()
    call_json = adapter._client.put.await_args.kwargs["json"]
    assert call_json["vote"] == -5
    assert call_json["id"] == "user-guid-1234"


@pytest.mark.asyncio
async def test_request_changes_posts_comment_when_body_present():
    adapter = _adapter_rw(_resp({}))
    pr = _pr()
    await adapter.request_changes(pr, "Please fix the auth flow.")
    adapter._client.post.assert_awaited_once()
    posted_body = adapter._client.post.await_args.kwargs["json"]
    assert "Please fix the auth flow" in posted_body["comments"][0]["content"]


@pytest.mark.asyncio
async def test_request_changes_skips_comment_when_body_empty():
    adapter = _adapter_rw(_resp({}))
    await adapter.request_changes(_pr(), "")
    adapter._client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_changes_skips_comment_when_body_whitespace():
    adapter = _adapter_rw(_resp({}))
    await adapter.request_changes(_pr(), "   \n  ")
    adapter._client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_changes_vote_failure_propagates():
    adapter = _adapter_rw(_resp({}, status_code=403))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.request_changes(_pr(), "some comment")
    # comment must NOT be posted if vote failed
    adapter._client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_changes_comment_failure_raises_after_vote():
    """Vote succeeded but comment POST fails — exception must propagate so callers
    know the body was not delivered even though the vote was cast."""
    comment_resp = _resp({}, status_code=500)
    adapter = _adapter_rw(_resp({}), post_resp=comment_resp)
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.request_changes(_pr(), "needs rework")
    # vote was cast
    adapter._client.put.assert_awaited_once()
    # comment was attempted
    adapter._client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_body_already_hydrated_skips_pr_get():
    """body='' means _hydrate_pr ran and the PR genuinely has no description.
    The adapter must NOT fire a second GET — the empty string is a valid fetched
    value, not the 'not yet fetched' sentinel (which is None)."""
    commits_json = {"value": [{"comment": "feat: no-desc commit"}]}
    pr_empty_body = PlatformPR(
        platform=Platform.ADO,
        pr_id="99",
        repo="my-repo",
        repo_url="https://dev.azure.com/org/project/_git/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="ADO PR",
        head_commit_sha="abc123",
        org="my-org",
        project="my-project",
        body="",  # hydrated — PR has no description
    )
    # Only one mock response (commits); a PR GET would exhaust the iterator and fail.
    body, commits = await _adapter(_resp(commits_json)).fetch_pr_body_and_commits(pr_empty_body)
    assert body == ""
    assert commits == ["feat: no-desc commit"]
