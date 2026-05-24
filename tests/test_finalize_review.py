"""Regression tests for the wrap-up modal's POST /api/reviews/{id}/finalize.

Exercises the same ADO org/project recovery path that submit-verdict has, plus
the "always post a comment on approve" behavior. Stays in-process via stubbed
storage and a mocked platform adapter.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app)


def _ado_review() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "13965",
        "repo": "IntegrationHub",
        "platform": "ado",
        "head_commit_sha": "abc123",
        "pr_url": "https://dev.azure.com/365projectum/MyProject/_git/IntegrationHub/pullrequest/13965",
        "source_branch": "feature",
        "target_branch": "main",
        "author": "dev",
        "title": "My PR",
        "agent_results": [],
    }


def _make_adapter():
    a = AsyncMock()
    a.approve_pr = AsyncMock(return_value=None)
    a.request_changes = AsyncMock(return_value=None)
    a.post_comment = AsyncMock(return_value=None)
    a.post_inline_comments = AsyncMock(return_value=None)
    return a


def _patch(monkeypatch, review, adapter):
    from pr_guardian.api import reviews_queue as rq
    from pr_guardian.platform import factory as factory_mod

    async def _get_review(_id):
        return review

    appended = []

    async def _append(_id, entry):
        appended.append(entry)
        return True

    async def _list(**_kw):
        return []

    monkeypatch.setattr(rq.storage, "get_review", _get_review)
    monkeypatch.setattr(rq.storage, "append_review_log_entry", _append)
    monkeypatch.setattr(rq.storage, "list_reviews", _list)
    monkeypatch.setattr(factory_mod, "create_adapter", lambda _p: adapter)
    monkeypatch.setattr(factory_mod, "create_github_adapter", AsyncMock(return_value=adapter))
    return appended


def test_ado_finalize_recovers_project_from_pr_url(client, monkeypatch):
    """Reviews row has no project column → recover from pr_url so ADO's
    reviewer endpoint receives a populated project segment."""
    review = _ado_review()
    adapter = _make_adapter()
    _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "approve",
            "comment_mode": "summary",
            "comment_to_author": "",
            "decisions": {},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["posted"] is True
    called_pr = adapter.approve_pr.await_args.args[0]
    assert called_pr.project == "MyProject"
    assert called_pr.org == "365projectum"


def test_ado_finalize_without_pr_url_returns_422(client, monkeypatch):
    review = _ado_review()
    review["pr_url"] = ""
    adapter = _make_adapter()
    _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "approve",
            "comment_mode": "summary",
            "comment_to_author": "",
            "decisions": {},
        },
    )
    assert resp.status_code == 422
    adapter.approve_pr.assert_not_awaited()


def test_finalize_approve_with_blank_comment_still_posts_summary(client, monkeypatch):
    """Even with no personal note and no fix decisions, a comment must be
    posted on approve — the PR's audit trail depends on it. comment_mode
    'none' is the only way to opt out."""
    review = _ado_review()
    adapter = _make_adapter()
    _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "approve",
            "comment_mode": "summary",
            "comment_to_author": "",
            "decisions": {},
        },
    )
    assert resp.status_code == 200, resp.text
    adapter.approve_pr.assert_awaited_once()
    adapter.post_comment.assert_awaited_once()
    body = adapter.post_comment.await_args.args[1]
    assert "Reviewed and approved" in body


def test_finalize_approve_with_comment_mode_none_skips_post_comment(client, monkeypatch):
    review = _ado_review()
    adapter = _make_adapter()
    _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "approve",
            "comment_mode": "none",
            "comment_to_author": "",
            "decisions": {},
        },
    )
    assert resp.status_code == 200, resp.text
    adapter.approve_pr.assert_awaited_once()
    adapter.post_comment.assert_not_awaited()
