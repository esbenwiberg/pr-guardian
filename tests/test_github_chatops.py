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
        self.reactions: list[tuple[str, str, str]] = []
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

    async def create_issue_comment_reaction(
        self, repo: str, comment_id: str, content: str
    ) -> None:
        self.reactions.append((repo, comment_id, content))

    async def list_issue_comments(self, repo: str, pr_id: str | int) -> list[dict]:
        return self.issue_comments

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Required fact: fact-chatops-guardian-aliases
# ---------------------------------------------------------------------------


def test_is_github_command_accepts_guardian_and_legacy_aliases():
    """@guardian and @pr-guardian (with or without re-review) are all valid commands."""
    assert github_chatops.is_github_command("@guardian")
    assert github_chatops.is_github_command("@guardian re-review")
    assert github_chatops.is_github_command("@pr-guardian")
    assert github_chatops.is_github_command("@pr-guardian re-review")
    # Mid-sentence mentions are also recognized
    assert github_chatops.is_github_command("please @guardian take a look")
    assert github_chatops.is_github_command("hey @pr-guardian re-review this")
    # Unrelated text must not trigger
    assert not github_chatops.is_github_command("mentioning guardian without the at-sign")
    assert not github_chatops.is_github_command("@guardianstuff do something")
    assert not github_chatops.is_github_command("@guardian-app")


# ---------------------------------------------------------------------------
# Required fact: fact-chatops-eyes-reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_github_comment_reacts_with_eyes_when_claimed(monkeypatch):
    """Guardian adds an eyes reaction to the triggering comment when it claims the command."""
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
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", AsyncMock())
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
        body="@guardian",
        commenter="bob",
        author_association="MEMBER",
        source="github:issue_comment",
    )

    assert result["status"] == "queued"
    # Eyes reaction must have been posted on the triggering comment
    assert ("octo/service", "9001", "eyes") in command_adapter.reactions
    assert command_adapter.closed is True


@pytest.mark.asyncio
async def test_handle_github_comment_eyes_reaction_error_does_not_fail_command(monkeypatch):
    """Eyes reaction errors (403, 429) are logged but do not abort the command."""
    command_id = uuid.uuid4()
    review_id = uuid.uuid4()
    created: list[object] = []

    failing_adapter = _FakeGitHubAdapter()

    async def _raise_reaction(repo: str, comment_id: str, content: str) -> None:
        raise RuntimeError("forbidden")

    failing_adapter.create_issue_comment_reaction = _raise_reaction  # type: ignore[method-assign]

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
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", AsyncMock())
    monkeypatch.setattr(
        github_chatops,
        "_fresh_adapter_for_review",
        AsyncMock(return_value=failing_adapter),
    )

    def _capture_task(coro):
        created.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(github_chatops.asyncio, "create_task", _capture_task)

    # Must not raise even though reaction creation fails
    result = await github_chatops.handle_github_comment(
        repo="octo/service",
        pr_id="42",
        comment_id="9001",
        body="@guardian",
        commenter="bob",
        author_association="MEMBER",
        source="github:issue_comment",
    )

    assert result["status"] == "queued"
    assert created  # background task was still scheduled


# ---------------------------------------------------------------------------
# Required fact: fact-chatops-first-review-or-rereview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guardian_mention_queues_first_review_or_rereview(monkeypatch):
    """@guardian on a linked PR with no prior Guardian review queues a first review."""
    command_id = uuid.uuid4()
    command_adapter = _FakeGitHubAdapter()
    created: list[object] = []

    repo_link = {"id": str(uuid.uuid4()), "connection_id": str(uuid.uuid4())}

    monkeypatch.setattr(
        github_chatops.storage,
        "claim_chatops_command",
        AsyncMock(return_value=command_id),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "find_latest_review_for_pr",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "get_active_repo_link_for_repo",
        AsyncMock(return_value=repo_link),
    )
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", AsyncMock())
    monkeypatch.setattr(
        github_chatops,
        "_adapter_for_repo_link",
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
        body="@guardian",
        commenter="alice",
        author_association="OWNER",
        source="github:issue_comment",
    )

    assert result == {"status": "queued", "first_review": True}
    assert created  # first-review background task was scheduled
    assert command_adapter.comments == ["Guardian: first review queued."]
    assert command_adapter.closed is True


@pytest.mark.asyncio
async def test_handle_github_comment_ignores_when_repo_not_linked(monkeypatch):
    """@guardian on an unlinked repo is marked ignored; no review is queued."""
    command_id = uuid.uuid4()

    monkeypatch.setattr(
        github_chatops.storage,
        "claim_chatops_command",
        AsyncMock(return_value=command_id),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "find_latest_review_for_pr",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "get_active_repo_link_for_repo",
        AsyncMock(return_value=None),
    )
    update = AsyncMock()
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", update)

    created: list[object] = []

    def _capture_task(coro):
        created.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(github_chatops.asyncio, "create_task", _capture_task)

    result = await github_chatops.handle_github_comment(
        repo="octo/service",
        pr_id="42",
        comment_id="9001",
        body="@guardian",
        commenter="alice",
        author_association="OWNER",
        source="github:issue_comment",
    )

    assert result == {"status": "ignored", "reason": "repo_not_linked"}
    assert not created  # no background task
    update.assert_any_await(
        command_id, status="ignored", status_detail="repo not linked", review_id=None
    )


# ---------------------------------------------------------------------------
# Existing tests (updated for ack copy change and @guardian recognition)
# ---------------------------------------------------------------------------


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
    assert command_adapter.comments == ["Guardian: re-review queued."]
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
