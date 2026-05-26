"""Regression tests for the wrap-up modal's POST /api/reviews/{id}/finalize.

Exercises the same ADO org/project recovery path that submit-verdict has, plus
the "always post a comment on approve" behavior. Stays in-process via stubbed
storage and a mocked platform adapter.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
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


def _github_review() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "head_commit_sha": "abc123",
        "pr_url": "https://github.com/org/repo/pull/42",
        "source_branch": "feature",
        "target_branch": "main",
        "author": "dev",
        "title": "My PR",
        "agent_results": [],
        "pat_name": "work-pat",
    }


def _github_review_with_finding() -> dict:
    review = _github_review()
    review["agent_results"] = [
        {
            "agent_name": "security_privacy",
            "verdict": "warn",
            "languages_reviewed": ["python"],
            "error": None,
            "verdict_explanation": None,
            "findings": [
                {
                    "id": str(uuid.uuid4()),
                    "severity": "high",
                    "certainty": "detected",
                    "category": "SQL Injection",
                    "language": "python",
                    "file": "src/foo.py",
                    "line": 10,
                    "description": "Unsanitised input passed to query.",
                    "suggestion": "Use parameterised queries.",
                    "cwe": None,
                }
            ],
        }
    ]
    return review


def _make_adapter():
    a = AsyncMock()
    a.approve_pr = AsyncMock(return_value=None)
    a.request_changes = AsyncMock(return_value=None)
    a.post_comment = AsyncMock(return_value=None)
    a.post_inline_comments = AsyncMock(return_value=None)
    return a


def _patch(monkeypatch, review, adapter, github_factory=None, active_dismissals=None):
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

    async def _get_active_dismissals(_pr_id, _repo, _platform):
        return active_dismissals or []

    monkeypatch.setattr(rq.storage, "get_review", _get_review)
    monkeypatch.setattr(rq.storage, "append_review_log_entry", _append)
    monkeypatch.setattr(rq.storage, "list_reviews", _list)
    monkeypatch.setattr(rq.storage, "get_active_dismissals", _get_active_dismissals)
    monkeypatch.setattr(factory_mod, "create_adapter", lambda _p: adapter)
    monkeypatch.setattr(
        factory_mod,
        "create_github_adapter",
        github_factory or AsyncMock(return_value=adapter),
    )
    return appended


def _github_reviews_422() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.github.com/repos/org/repo/pulls/42/reviews")
    response = httpx.Response(
        status_code=422,
        request=request,
        text='{"message":"Validation Failed","errors":["Can not review your own pull request"]}',
    )
    return httpx.HTTPStatusError("422 Unprocessable Entity", request=request, response=response)


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


def test_github_finalize_uses_review_pat_name(client, monkeypatch):
    review = _github_review()
    adapter = _make_adapter()
    github_factory = AsyncMock(return_value=adapter)
    _patch(monkeypatch, review, adapter, github_factory=github_factory)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "request_changes",
            "comment_mode": "summary",
            "comment_to_author": "Needs work.",
            "decisions": {},
        },
    )

    assert resp.status_code == 200, resp.text
    github_factory.assert_awaited_once_with("work-pat")
    adapter.request_changes.assert_awaited_once()


def test_github_request_changes_422_falls_back_to_comment(client, monkeypatch):
    review = _github_review()
    adapter = _make_adapter()
    adapter.request_changes = AsyncMock(side_effect=_github_reviews_422())
    appended = _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "request_changes",
            "comment_mode": "summary",
            "comment_to_author": "Please address this before merge.",
            "decisions": {},
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["posted"] is True
    assert body["actions"] == ["request_changes_rejected", "post_comment_fallback"]
    adapter.request_changes.assert_awaited_once()
    adapter.post_comment.assert_awaited_once()
    assert "Please address this before merge" in adapter.post_comment.await_args.args[1]
    assert appended[0]["posted"] is True
    assert "Can not review your own pull request" in appended[0]["error"]


def test_finalize_inline_recovers_persisted_fix_decisions(client, monkeypatch):
    """The finish-review modal may send an empty decision map if the viewer
    state was stale; persisted will_fix choices must still produce inline
    comments and a useful summary."""
    from pr_guardian.persistence.storage import finding_signature

    review = _github_review_with_finding()
    finding = review["agent_results"][0]["findings"][0]
    signature = finding_signature(finding["file"], finding["category"], "security_privacy")
    adapter = _make_adapter()
    appended = _patch(
        monkeypatch,
        review,
        adapter,
        active_dismissals=[
            {
                "signature": signature,
                "status": "will_fix",
                "source_finding": {
                    "file": finding["file"],
                    "line": finding["line"],
                    "category": finding["category"],
                    "agent_name": "security_privacy",
                },
            }
        ],
    )

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "request_changes",
            "comment_mode": "inline",
            "comment_to_author": "Please fix the flagged issue.",
            "decisions": {},
        },
    )

    assert resp.status_code == 200, resp.text
    adapter.post_inline_comments.assert_awaited_once()
    inline_findings = adapter.post_inline_comments.await_args.args[1]
    assert len(inline_findings) == 1
    assert inline_findings[0].file == "src/foo.py"
    assert inline_findings[0].line == 10
    adapter.request_changes.assert_awaited_once()
    summary = adapter.request_changes.await_args.args[1]
    assert "Fix-requested findings" in summary
    assert "Unsanitised input" in summary
    assert resp.json()["decisions"] == {finding["id"]: "fix"}
    assert appended[0]["decisions"] == {finding["id"]: "fix"}


def test_finalize_inline_request_changes_defaults_actionable_findings(client, monkeypatch):
    """Request-changes inline mode should not require every finding to be
    pre-marked as fix; unresolved actionable findings are the inline set."""
    review = _github_review_with_finding()
    finding = review["agent_results"][0]["findings"][0]
    adapter = _make_adapter()
    appended = _patch(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/reviews/{review['id']}/finalize",
        json={
            "verdict": "request_changes",
            "comment_mode": "inline",
            "comment_to_author": "Changes requested before merge.",
            "decisions": {},
        },
    )

    assert resp.status_code == 200, resp.text
    adapter.post_inline_comments.assert_awaited_once()
    inline_findings = adapter.post_inline_comments.await_args.args[1]
    assert len(inline_findings) == 1
    assert inline_findings[0].file == "src/foo.py"
    summary = adapter.request_changes.await_args.args[1]
    assert "1 fix requested" in summary
    assert "Fix-requested findings" in summary
    assert resp.json()["decisions"] == {finding["id"]: "fix"}
    assert appended[0]["decisions"] == {finding["id"]: "fix"}
