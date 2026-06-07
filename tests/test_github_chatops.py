from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from pr_guardian.core import github_chatops
from pr_guardian.models.pr import Platform, PlatformPR


class _FakeGitHubAdapter:
    def __init__(self, author: str = "alice"):
        self.author = author
        self.comments: list[str] = []
        self.issue_comments: list[dict] = []
        self.closed = False

    async def fetch_pr(self, repo: str, pr_id: str | int) -> PlatformPR:
        return PlatformPR(
            platform=Platform.GITHUB,
            pr_id=str(pr_id),
            repo=repo,
            repo_url=f"https://github.com/{repo}.git",
            source_branch="fix",
            target_branch="main",
            author=self.author,
            title="Fix",
            head_commit_sha="sha2",
            org=repo.split("/", 1)[0],
        )

    async def post_comment(self, pr: PlatformPR, body: str) -> None:
        self.comments.append(body)

    async def list_issue_comments(self, repo: str, pr_id: str | int) -> list[dict]:
        return self.issue_comments

    async def close(self) -> None:
        self.closed = True


def test_is_github_re_review_command_accepts_mention_forms():
    assert github_chatops.is_github_re_review_command("@pr-guardian re-review")
    assert github_chatops.is_github_re_review_command("@pr-guardian: re-review please")
    assert not github_chatops.is_github_re_review_command("@pr-guardian review")


@pytest.mark.asyncio
async def test_handle_github_comment_queues_re_review(monkeypatch):
    command_id = uuid.uuid4()
    review_id = uuid.uuid4()
    command_adapter = _FakeGitHubAdapter()
    created: list[object] = []

    monkeypatch.setattr(
        github_chatops.storage,
        "claim_chatops_command",
        AsyncMock(return_value=command_id),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "find_latest_review_for_pr",
        AsyncMock(return_value={"id": str(review_id), "agent_results": []}),
    )
    update = AsyncMock()
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", update)
    monkeypatch.setattr(
        github_chatops,
        "_fresh_adapter_for_review",
        AsyncMock(return_value=command_adapter),
    )

    def _capture_task(coro):
        created.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(github_chatops.asyncio, "create_task", _capture_task)

    result = await github_chatops.handle_github_comment(
        repo="octo/service",
        pr_id="42",
        comment_id="9001",
        body="@pr-guardian re-review",
        commenter="bob",
        author_association="MEMBER",
        source="github:issue_comment",
    )

    assert result == {"status": "queued", "review_id": str(review_id)}
    assert command_adapter.comments == ["PR Guardian: re-review queued."]
    assert command_adapter.closed is True
    assert created
    update.assert_any_await(
        command_id, status="queued", status_detail="", review_id=str(review_id)
    )


@pytest.mark.asyncio
async def test_handle_github_comment_dedupes_seen_comment(monkeypatch):
    monkeypatch.setattr(
        github_chatops.storage,
        "claim_chatops_command",
        AsyncMock(return_value=None),
    )

    result = await github_chatops.handle_github_comment(
        repo="octo/service",
        pr_id="42",
        comment_id="9001",
        body="@pr-guardian re-review",
        commenter="bob",
        author_association="MEMBER",
        source="poll:github",
    )

    assert result == {"status": "ignored", "reason": "duplicate"}


@pytest.mark.asyncio
async def test_fresh_adapter_raises_when_no_connection_id_on_review(monkeypatch):
    """_fresh_adapter_for_review raises ValueError when review has no connection_id."""
    with pytest.raises(ValueError, match="No GitHub App Connection found"):
        await github_chatops._fresh_adapter_for_review({"id": "review-abc"})


@pytest.mark.asyncio
async def test_fresh_adapter_raises_when_connection_is_deleted(monkeypatch):
    """_fresh_adapter_for_review raises ValueError when connection_id is set but deleted."""
    import uuid

    conn_id = uuid.uuid4()
    monkeypatch.setattr(
        github_chatops.storage,
        "get_connection",
        AsyncMock(return_value=None),
    )

    with pytest.raises(ValueError, match="No GitHub App Connection found"):
        await github_chatops._fresh_adapter_for_review(
            {"id": "review-abc", "connection_id": str(conn_id)}
        )


@pytest.mark.asyncio
async def test_fresh_adapter_raises_when_connection_is_not_app_typed(monkeypatch):
    """_fresh_adapter_for_review raises ValueError when connection is not a GitHub App."""
    import uuid

    conn_id = uuid.uuid4()
    monkeypatch.setattr(
        github_chatops.storage,
        "get_connection",
        AsyncMock(return_value={"id": str(conn_id), "auth_kind": None, "platform": "github"}),
    )

    with pytest.raises(ValueError, match="not a GitHub App connection"):
        await github_chatops._fresh_adapter_for_review(
            {"id": "review-abc", "connection_id": str(conn_id)}
        )


@pytest.mark.asyncio
async def test_fresh_adapter_resolves_app_connection_successfully(monkeypatch):
    """_fresh_adapter_for_review returns a GitHubAdapter when connection is App-typed."""
    import uuid

    conn_id = uuid.uuid4()
    fake_adapter = _FakeGitHubAdapter()
    monkeypatch.setattr(
        github_chatops.storage,
        "get_connection",
        AsyncMock(
            return_value={
                "id": str(conn_id),
                "auth_kind": "github_app",
                "platform": "github",
                "app_id": "12345",
                "installation_id": "98765",
            }
        ),
    )
    monkeypatch.setattr(
        "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
        AsyncMock(return_value=fake_adapter),
    )

    adapter = await github_chatops._fresh_adapter_for_review(
        {"id": "review-abc", "connection_id": str(conn_id)}
    )
    assert adapter is fake_adapter


@pytest.mark.asyncio
async def test_poll_github_pr_comments_dispatches_poll_source(monkeypatch):
    adapter = _FakeGitHubAdapter()
    adapter.issue_comments = [
        {
            "id": 1,
            "body": "@pr-guardian re-review",
            "user": {"login": "alice"},
            "author_association": "CONTRIBUTOR",
        }
    ]
    handle = AsyncMock(return_value={"status": "queued", "review_id": "review-1"})
    monkeypatch.setattr(github_chatops, "handle_github_comment", handle)

    count = await github_chatops.poll_github_pr_comments(
        adapter,
        repo="octo/service",
        pr={"number": 42, "comments": 1, "user": {"login": "alice"}},
    )

    assert count == 1
    handle.assert_awaited_once()
    assert handle.call_args.kwargs["source"] == "poll:github"
    assert handle.call_args.kwargs["pr_author"] == "alice"
