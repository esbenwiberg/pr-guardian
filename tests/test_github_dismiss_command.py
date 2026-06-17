"""Fix #1: dismiss a finding by replying to a Guardian inline comment."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from pr_guardian.core import github_chatops


# ---------------------------------------------------------------------------
# Parsing + scope gate (pure)
# ---------------------------------------------------------------------------


def test_parse_dismiss_command_variants():
    assert github_chatops.parse_dismiss_command("@guardian dismiss false_positive: parameterized") == (
        "false_positive",
        "parameterized",
    )
    assert github_chatops.parse_dismiss_command("@pr-guardian dismiss by_design") == (
        "by_design",
        "",
    )
    # No status -> defaults to acknowledged
    assert github_chatops.parse_dismiss_command("@guardian dismiss: not a real issue") == (
        "acknowledged",
        "not a real issue",
    )


def test_parse_dismiss_command_rejects_non_dismiss():
    assert github_chatops.parse_dismiss_command("@guardian re-review") is None
    assert github_chatops.parse_dismiss_command("just a normal comment") is None


def test_is_comment_dismissable_scope_gate():
    low = {"severity": "low", "agent_name": "test_quality"}
    med = {"severity": "medium", "agent_name": "performance"}
    high = {"severity": "high", "agent_name": "test_quality"}
    crit = {"severity": "critical", "agent_name": "performance"}
    sec = {"severity": "medium", "agent_name": "security_privacy"}
    assert github_chatops._is_comment_dismissable(low)
    assert github_chatops._is_comment_dismissable(med)
    assert not github_chatops._is_comment_dismissable(high)
    assert not github_chatops._is_comment_dismissable(crit)
    assert not github_chatops._is_comment_dismissable(sec)  # security never self-dismissable


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def __init__(self):
        self.replies: list[tuple] = []
        self.closed = False

    async def reply_to_review_comment(self, repo, pr_id, comment_id, body):
        self.replies.append((repo, pr_id, comment_id, body))

    async def close(self):
        self.closed = True


def _patch_common(monkeypatch, *, parent, adapter):
    monkeypatch.setattr(
        github_chatops.storage,
        "find_inline_comment_by_platform_id",
        AsyncMock(return_value=parent),
    )
    monkeypatch.setattr(
        github_chatops.storage,
        "claim_chatops_command",
        AsyncMock(return_value=uuid.uuid4()),
    )
    monkeypatch.setattr(github_chatops.storage, "update_chatops_command", AsyncMock())
    monkeypatch.setattr(
        github_chatops.storage,
        "get_review",
        AsyncMock(return_value={"id": "rev", "connection_id": "c"}),
    )
    monkeypatch.setattr(
        github_chatops,
        "_fresh_adapter_for_review",
        AsyncMock(return_value=adapter),
    )


async def test_dismiss_records_eligible_finding(monkeypatch):
    parent = {
        "review_id": str(uuid.uuid4()),
        "platform": "github",
        "repo": "org/repo",
        "pr_id": "287",
        "findings": [
            {"file": "a.py", "category": "style", "agent_name": "code_quality_observability",
             "severity": "low"},
        ],
    }
    adapter = _FakeAdapter()
    upsert = AsyncMock(return_value=uuid.uuid4())
    _patch_common(monkeypatch, parent=parent, adapter=adapter)
    monkeypatch.setattr(github_chatops.storage, "upsert_dismissal", upsert)

    result = await github_chatops.handle_github_review_comment_reply(
        repo="org/repo",
        pr_id="287",
        comment_id="reply-1",
        in_reply_to_id="parent-1",
        body="@guardian dismiss false_positive: handled upstream",
        commenter="alice",
        author_association="OWNER",
        pr_author="alice",
        source="github:pull_request_review_comment",
    )

    assert result["status"] == "dismissed"
    assert result["count"] == 1
    assert upsert.await_count == 1
    assert upsert.await_args.kwargs["status"] == "false_positive"
    assert adapter.replies and "recorded 1" in adapter.replies[0][3]
    assert adapter.closed


async def test_dismiss_blocked_for_high_or_security(monkeypatch):
    parent = {
        "review_id": str(uuid.uuid4()),
        "platform": "github",
        "repo": "org/repo",
        "pr_id": "287",
        "findings": [
            {"file": "auth.py", "category": "sql-injection", "agent_name": "security_privacy",
             "severity": "high"},
        ],
    }
    adapter = _FakeAdapter()
    upsert = AsyncMock()
    _patch_common(monkeypatch, parent=parent, adapter=adapter)
    monkeypatch.setattr(github_chatops.storage, "upsert_dismissal", upsert)

    result = await github_chatops.handle_github_review_comment_reply(
        repo="org/repo",
        pr_id="287",
        comment_id="reply-2",
        in_reply_to_id="parent-2",
        body="@guardian dismiss false_positive: trust me",
        commenter="alice",
        author_association="OWNER",
        pr_author="alice",
        source="github:pull_request_review_comment",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "not_dismissable"
    assert upsert.await_count == 0
    assert adapter.replies and "can't be dismissed" in adapter.replies[0][3]


async def test_reply_to_non_guardian_comment_ignored(monkeypatch):
    monkeypatch.setattr(
        github_chatops.storage,
        "find_inline_comment_by_platform_id",
        AsyncMock(return_value=None),
    )
    claim = AsyncMock()
    monkeypatch.setattr(github_chatops.storage, "claim_chatops_command", claim)

    result = await github_chatops.handle_github_review_comment_reply(
        repo="org/repo",
        pr_id="287",
        comment_id="reply-3",
        in_reply_to_id="some-other-comment",
        body="@guardian dismiss false_positive: x",
        commenter="alice",
        author_association="OWNER",
        pr_author="alice",
        source="github:pull_request_review_comment",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "not_a_guardian_finding_comment"
    # Never even claimed a command for a non-Guardian comment.
    assert claim.await_count == 0


async def test_unauthorized_commenter_cannot_dismiss(monkeypatch):
    parent = {
        "review_id": str(uuid.uuid4()),
        "platform": "github",
        "repo": "org/repo",
        "pr_id": "287",
        "findings": [
            {"file": "a.py", "category": "style", "agent_name": "code_quality_observability",
             "severity": "low"},
        ],
    }
    adapter = _FakeAdapter()
    upsert = AsyncMock()
    _patch_common(monkeypatch, parent=parent, adapter=adapter)
    monkeypatch.setattr(github_chatops.storage, "upsert_dismissal", upsert)

    result = await github_chatops.handle_github_review_comment_reply(
        repo="org/repo",
        pr_id="287",
        comment_id="reply-4",
        in_reply_to_id="parent-4",
        body="@guardian dismiss false_positive: x",
        commenter="random-drive-by",
        author_association="NONE",
        pr_author="alice",
        source="github:pull_request_review_comment",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unauthorized"
    assert upsert.await_count == 0


async def test_non_dismiss_reply_is_ignored_without_lookup(monkeypatch):
    find = AsyncMock()
    monkeypatch.setattr(github_chatops.storage, "find_inline_comment_by_platform_id", find)
    result = await github_chatops.handle_github_review_comment_reply(
        repo="org/repo",
        pr_id="287",
        comment_id="reply-5",
        in_reply_to_id="parent-5",
        body="thanks, will fix",
        commenter="alice",
        author_association="OWNER",
        pr_author="alice",
        source="github:pull_request_review_comment",
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "no_dismiss_command"
    assert find.await_count == 0
