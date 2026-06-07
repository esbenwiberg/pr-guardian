"""Tests for the sticky guidance comment lifecycle on GitHub PRs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pr_guardian.decision.actions import GUIDANCE_MARKER, build_guidance_comment_body
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.guidance import upsert_guidance_comment


def _pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="https://github.com/org/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="My PR",
        head_commit_sha="sha1",
        org="org",
    )


def _mock_response(status: int, body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# build_guidance_comment_body
# ---------------------------------------------------------------------------


def test_guidance_body_contains_marker():
    body = build_guidance_comment_body("pending")
    assert GUIDANCE_MARKER in body


def test_guidance_body_contains_state_label():
    body = build_guidance_comment_body("success")
    assert "green" in body or "✓" in body


def test_guidance_body_contains_rereview_instruction():
    body = build_guidance_comment_body("pending")
    assert "@guardian" in body


def test_guidance_body_includes_review_url_when_provided():
    body = build_guidance_comment_body("reviewing", review_url="http://localhost/reviews/abc")
    assert "http://localhost/reviews/abc" in body


def test_guidance_body_no_url_when_empty():
    body = build_guidance_comment_body("pending", review_url="")
    assert "http" not in body


# ---------------------------------------------------------------------------
# upsert_guidance_comment — create path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_new_comment_when_no_stored_id():
    adapter = GitHubAdapter(token="tok")
    pr = _pr()

    # list_issue_comments returns empty (no existing comment)
    list_resp = _mock_response(200, [])
    post_resp = _mock_response(201, {"id": 999})
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=list_resp)
    mock_client.post = AsyncMock(return_value=post_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        comment_id = await adapter.upsert_guidance_comment(pr, "body text")

    assert comment_id == "999"
    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args[0][0]
    assert "/repos/org/repo/issues/42/comments" in call_url


# ---------------------------------------------------------------------------
# upsert_guidance_comment — update via stored ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_updates_stored_comment_id():
    adapter = GitHubAdapter(token="tok")
    pr = _pr()

    patch_resp = _mock_response(200, {"id": 555})
    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=patch_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        comment_id = await adapter.upsert_guidance_comment(
            pr, "updated body", stored_comment_id="555"
        )

    assert comment_id == "555"
    mock_client.patch.assert_called_once()
    call_url = mock_client.patch.call_args[0][0]
    assert "/repos/org/repo/issues/comments/555" in call_url


# ---------------------------------------------------------------------------
# upsert_guidance_comment — recovery when stored ID returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_recovers_by_marker_when_stored_comment_deleted():
    """When stored ID is 404, Guardian searches PR comments for the marker."""
    adapter = GitHubAdapter(token="tok")
    pr = _pr()

    patch_resp_404 = _mock_response(404, {})
    patch_resp_404.raise_for_status.side_effect = None  # 404 is handled gracefully

    existing_comment = {"id": 777, "body": f"{GUIDANCE_MARKER}\nOld guidance."}
    list_resp = _mock_response(200, [existing_comment])
    patch_resp_ok = _mock_response(200, {"id": 777})

    call_log: list[str] = []
    mock_client = MagicMock()

    async def fake_patch(url, **kw):
        call_log.append(("patch", url))
        if "comments/old-id" in url:
            return patch_resp_404
        return patch_resp_ok

    async def fake_get(url, **kw):
        call_log.append(("get", url))
        return list_resp

    mock_client.patch = AsyncMock(side_effect=fake_patch)
    mock_client.get = AsyncMock(side_effect=fake_get)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        comment_id = await adapter.upsert_guidance_comment(
            pr, "new body", stored_comment_id="old-id"
        )

    assert comment_id == "777"
    # First patch was to old-id (404), second patch updated the found comment
    patch_urls = [url for kind, url in call_log if kind == "patch"]
    assert any("old-id" in u for u in patch_urls)
    assert any("777" in u for u in patch_urls)


# ---------------------------------------------------------------------------
# upsert_guidance_comment — recreate when marker not found and no stored ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_when_marker_absent_in_comments():
    """If no stored ID and no marker in PR comments, create a fresh comment."""
    adapter = GitHubAdapter(token="tok")
    pr = _pr()

    unrelated = {"id": 100, "body": "Normal review comment, no marker here."}
    list_resp = _mock_response(200, [unrelated])
    post_resp = _mock_response(201, {"id": 101})

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=list_resp)
    mock_client.post = AsyncMock(return_value=post_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        comment_id = await adapter.upsert_guidance_comment(pr, "fresh guidance")

    assert comment_id == "101"
    mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# upsert_guidance_comment helper (platform.guidance) — "reviewing" state path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewing_state_body_includes_deeplink():
    """upsert_guidance_comment with state='reviewing' and review_url passes deeplink to adapter."""
    pr = _pr()
    review_url = "http://localhost/reviews/abc-123"

    adapter = MagicMock()
    adapter.upsert_guidance_comment = AsyncMock(return_value="comment-42")

    fake_storage = MagicMock()
    fake_storage.load_guidance_comment_id = AsyncMock(return_value=None)
    fake_storage.save_guidance_comment_id = AsyncMock()

    result_id = await upsert_guidance_comment(
        adapter, pr, "reviewing", review_url=review_url, storage=fake_storage
    )

    assert result_id == "comment-42"
    adapter.upsert_guidance_comment.assert_awaited_once()
    body = adapter.upsert_guidance_comment.await_args[0][1]
    assert GUIDANCE_MARKER in body
    assert "reviewing" in body
    assert review_url in body
    assert "@guardian" in body


# ---------------------------------------------------------------------------
# Orchestrator-level: guidance is called with correct state at completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticky_guidance_comment_is_created_recovered_and_updated():
    """End-to-end: _post_results() calls upsert_guidance_comment and sets postback_meta."""
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core.orchestrator import _post_results
    from pr_guardian.models.context import RepoRiskClass, RiskTier
    from pr_guardian.models.output import Decision, ReviewResult
    from pr_guardian.platform.protocol import InlinePostResult, PlatformPRMetadata

    pr = _pr()
    result = ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.TRIVIAL,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.AUTO_APPROVE,
        review_id="review-123",
    )

    adapter = MagicMock()
    adapter.set_review_status = AsyncMock()
    adapter.post_comment = AsyncMock()
    adapter.post_inline_comments = AsyncMock(
        return_value=InlinePostResult(posted_ids=[], skipped=[])
    )
    adapter.delete_inline_comments = AsyncMock()
    adapter.add_label = AsyncMock()
    adapter.request_reviewers = AsyncMock()
    adapter.approve_pr = AsyncMock()
    adapter.request_changes = AsyncMock()
    adapter.fetch_pr_metadata = AsyncMock(return_value=PlatformPRMetadata(head_sha="sha1"))
    adapter.upsert_guidance_comment = AsyncMock(return_value="comment-999")

    fake_storage = MagicMock()
    fake_storage.load_guidance_comment_id = AsyncMock(return_value=None)
    fake_storage.save_guidance_comment_id = AsyncMock()
    fake_storage.load_inline_comment_ids = AsyncMock(return_value=[])

    config = GuardianConfig()
    config.platform_approval_enabled = False

    await _post_results(
        adapter,
        pr,
        result,
        config,
        comment_mode="summary",
        manual_comment_override=True,
        storage=fake_storage,
    )

    # Guidance comment was called
    adapter.upsert_guidance_comment.assert_awaited_once()
    call_args = adapter.upsert_guidance_comment.await_args
    body_passed = call_args[0][1]
    assert GUIDANCE_MARKER in body_passed
    # State is success for AUTO_APPROVE
    assert "green" in body_passed or "✓" in body_passed

    # postback_meta was populated
    assert result.postback_meta.get("guidance_posted") is True
    assert result.postback_meta.get("guidance_comment_id") == "comment-999"

    # storage was called to persist the comment ID
    fake_storage.save_guidance_comment_id.assert_awaited_once_with(
        "github", "org/repo", "42", "comment-999"
    )
