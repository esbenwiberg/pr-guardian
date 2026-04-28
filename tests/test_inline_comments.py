"""Unit tests for post_inline_comments / delete_inline_comments on GitHub and ADO adapters."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pr_guardian.models.findings import Certainty, Finding, Severity
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter


def _make_github_pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="https://github.com/org/repo",
        source_branch="feature",
        target_branch="main",
        author="dev",
        title="My PR",
        head_commit_sha="abc123",
        org="org",
    )


def _make_ado_pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.ADO,
        pr_id="7",
        repo="myrepo",
        repo_url="https://dev.azure.com/myorg/myproject/_git/myrepo",
        source_branch="feature",
        target_branch="main",
        author="dev@example.com",
        title="My ADO PR",
        head_commit_sha="def456",
        org="https://dev.azure.com/myorg",
        project="myproject",
    )


def _finding(file: str = "src/foo.py", line: int = 10) -> Finding:
    return Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="SQL Injection",
        language="python",
        file=file,
        line=line,
        description="Unsanitised input passed to query.",
        suggestion="Use parameterised queries.",
    )


def _mock_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# GitHub — post_inline_comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_post_inline_returns_ids():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()
    findings = [_finding("src/foo.py", 10)]

    review_resp = _mock_response(200, {"id": 1, "comments": [{"id": 999}]})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=review_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, findings)

    assert ids == ["999"]
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["json"]["event"] == "COMMENT"
    assert call_kwargs[1]["json"]["comments"][0]["line"] == 10


@pytest.mark.asyncio
async def test_github_post_inline_skips_422():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()
    # Two findings at different lines: first is outside diff (422), second is valid
    f1 = _finding("src/foo.py", 5)
    f2 = _finding("src/bar.py", 20)

    resp_422 = _mock_response(422, {})
    resp_ok = _mock_response(200, {"id": 2, "comments": [{"id": 777}]})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_422, resp_ok])

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, [f1, f2])

    assert ids == ["777"]
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_github_post_inline_skips_none_line():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()
    f = Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="Bug",
        language="python",
        file="src/x.py",
        line=None,
        description="desc",
    )
    mock_client = MagicMock()
    mock_client.post = AsyncMock()

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, [f])

    assert ids == []
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_github_post_inline_groups_same_file_line():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()
    # Two findings at same file+line — should produce ONE review POST
    f1 = _finding("src/foo.py", 10)
    f2 = _finding("src/foo.py", 10)

    review_resp = _mock_response(200, {"id": 1, "comments": [{"id": 100}]})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=review_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, [f1, f2])

    assert ids == ["100"]
    assert mock_client.post.call_count == 1


# ---------------------------------------------------------------------------
# GitHub — delete_inline_comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_delete_calls_correct_endpoint():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()

    mock_client = MagicMock()
    mock_client.delete = AsyncMock(return_value=_mock_response(204, {}))

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.delete_inline_comments(pr, ["111", "222"])

    assert mock_client.delete.call_count == 2
    calls = [c[0][0] for c in mock_client.delete.call_args_list]
    assert "/repos/org/repo/pulls/comments/111" in calls[0]
    assert "/repos/org/repo/pulls/comments/222" in calls[1]


# ---------------------------------------------------------------------------
# ADO — post_inline_comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ado_post_inline_returns_ids():
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()
    findings = [_finding("src/foo.py", 15)]

    thread_resp = _mock_response(200, {"id": 42, "comments": []})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=thread_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, findings)

    assert ids == ["42"]
    call_kwargs = mock_client.post.call_args
    body = call_kwargs[1]["json"]
    assert body["threadContext"]["filePath"] == "/src/foo.py"
    assert body["threadContext"]["rightFileStart"]["line"] == 15


@pytest.mark.asyncio
async def test_ado_post_inline_skips_422():
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()
    f1 = _finding("src/outside.py", 999)
    f2 = _finding("src/inside.py", 5)

    resp_422 = _mock_response(422, {})
    resp_ok = _mock_response(200, {"id": 55})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_422, resp_ok])

    with patch.object(adapter, "_get_client", return_value=mock_client):
        ids = await adapter.post_inline_comments(pr, [f1, f2])

    assert ids == ["55"]
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_ado_post_inline_prepends_slash_to_path():
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()
    findings = [_finding("no_leading_slash.py", 1)]

    resp_ok = _mock_response(200, {"id": 10})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=resp_ok)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.post_inline_comments(pr, findings)

    body = mock_client.post.call_args[1]["json"]
    assert body["threadContext"]["filePath"].startswith("/")


# ---------------------------------------------------------------------------
# ADO — delete_inline_comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ado_delete_patches_status_and_replies():
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()

    mock_client = MagicMock()
    patch_resp = _mock_response(200, {"id": 77, "status": 4})
    reply_resp = _mock_response(200, {"id": 1})
    mock_client.patch = AsyncMock(return_value=patch_resp)
    mock_client.post = AsyncMock(return_value=reply_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.delete_inline_comments(pr, ["77"])

    mock_client.patch.assert_called_once()
    patch_body = mock_client.patch.call_args[1]["json"]
    assert patch_body["status"] == 4

    mock_client.post.assert_called_once()
    reply_body = mock_client.post.call_args[1]["json"]
    assert "superseded" in reply_body["content"]


@pytest.mark.asyncio
async def test_ado_delete_calls_correct_thread_url():
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=_mock_response(200, {}))
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.delete_inline_comments(pr, ["99", "100"])

    assert mock_client.patch.call_count == 2
    urls = [c[0][0] for c in mock_client.patch.call_args_list]
    assert "threads/99" in urls[0]
    assert "threads/100" in urls[1]
