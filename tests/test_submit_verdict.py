"""Unit tests for the wizard's submit-verdict endpoint.

Exercises POST /api/dashboard/reviews/{id}/submit-verdict end-to-end via
FastAPI's TestClient. Uses monkeypatch to stub the storage helpers and the
platform adapter so the test stays in-process and does not require a DB,
network, or real GitHub/ADO credentials.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from pr_guardian.platform.protocol import InlinePostResult

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from pr_guardian.main import app

    return TestClient(app, headers={"X-MS-CLIENT-PRINCIPAL-NAME": "reviewer@example.test"})


@pytest.fixture
def fake_review():
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
        "agent_results": [
            {
                "agent_name": "security",
                "findings": [
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "medium",
                        "file": "a.py",
                        "line": 1,
                        "description": "x",
                        "dismissal": {"status": "acknowledged"},
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "file": "b.py",
                        "line": 2,
                        "description": "y",
                        "dismissal": {"status": "will_fix"},
                    },
                ],
            },
        ],
    }


def _patch_endpoint_deps(monkeypatch, fake_review, mock_adapter, *, dismissals=None):
    from pr_guardian.api import dashboard as dash

    async def _get_review(_id):
        return fake_review

    appended = []

    async def _append(_id, entry):
        appended.append(entry)
        return True

    async def _active_dismissals(_pr_id, _repo, _platform):
        # Tests drive dismissal state via the inline `dismissal` field on each
        # finding in `fake_review`. Returning [] keeps the enrichment helper
        # hermetic — it becomes a no-op and leaves those inline values intact.
        return dismissals or []

    monkeypatch.setattr(dash.storage, "get_review", _get_review)
    monkeypatch.setattr(dash.storage, "append_review_log_entry", _append)
    monkeypatch.setattr(dash.storage, "get_active_dismissals", _active_dismissals)
    monkeypatch.setattr(dash, "create_adapter", lambda _platform: mock_adapter)
    monkeypatch.setattr(dash, "create_github_adapter", AsyncMock(return_value=mock_adapter))
    return appended


def _make_mock_adapter():
    adapter = AsyncMock()
    adapter.approve_pr = AsyncMock(return_value=None)
    adapter.request_changes = AsyncMock(return_value=None)
    adapter.post_comment = AsyncMock(return_value=None)
    adapter.post_inline_comments = AsyncMock(
        return_value=InlinePostResult(posted_ids=["thread-1"], skipped=[])
    )
    return adapter


def test_approve_always_posts_comment_for_audit_trail(client, fake_review, monkeypatch):
    """Even on a clean approve with no personal note, post a comment so the
    PR carries a record that PR Guardian reviewed it."""
    adapter = _make_mock_adapter()
    appended = _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["posted"] is True
    assert body["verdict"] == "approve"
    # Fixture carries one `will_fix` finding, so we expect an inline post too.
    # Approve also flips guardian/review -> success so a required check unblocks.
    assert body["platform_actions"] == [
        "post_inline_comments",
        "approve_pr",
        "post_comment",
        "set_status:success",
    ]

    adapter.approve_pr.assert_awaited_once()
    adapter.post_comment.assert_awaited_once()
    adapter.post_inline_comments.assert_awaited_once()
    adapter.request_changes.assert_not_awaited()
    # Headline-only body still carries the standard "Reviewed and approved." line.
    posted_body = adapter.post_comment.await_args.args[1]
    assert "Reviewed and approved" in posted_body

    assert len(appended) == 1
    assert appended[0]["kind"] == "human_verdict"
    assert appended[0]["verdict"] == "approve"
    assert appended[0]["posted"] is True


def test_approve_with_comment_also_posts_comment(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": "Looks good — small nit on naming."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_actions"] == [
        "post_inline_comments",
        "approve_pr",
        "post_comment",
        "set_status:success",
    ]
    adapter.approve_pr.assert_awaited_once()
    adapter.post_comment.assert_awaited_once()
    body = adapter.post_comment.await_args.args[1]
    assert "small nit on naming" in body
    assert "1 accepted" in body and "1 fix" in body  # decision summary appended


def test_approve_with_fixes_always_posts_comment(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve_with_fixes", "comment": ""},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_actions"] == [
        "post_inline_comments",
        "approve_pr",
        "post_comment",
        "set_status:success",
    ]
    adapter.approve_pr.assert_awaited_once()
    adapter.post_comment.assert_awaited_once()


def test_decline_calls_request_changes_with_comment(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "decline", "comment": "Auth flow needs rework."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_actions"] == [
        "post_inline_comments",
        "request_changes",
        "set_status:failure",
    ]
    adapter.request_changes.assert_awaited_once()
    body = adapter.request_changes.await_args.args[1]
    assert "Auth flow needs rework" in body
    adapter.approve_pr.assert_not_awaited()


def test_decline_posts_inline_comments_for_each_will_fix_finding(client, monkeypatch):
    """The wizard's Decline + N fixes must reach the PR as N inline comments.

    Without this, only the summary headline lands on the PR and the reviewer's
    per-concern fix requests vanish (the bug that prompted this test).
    """
    review = {
        "id": str(uuid.uuid4()),
        "pr_id": "13965",
        "repo": "IntegrationHub",
        "platform": "ado",
        "head_commit_sha": "abc123",
        "pr_url": "https://dev.azure.com/365projectum/MyProject/_git/IntegrationHub/pullrequest/13965",
        "source_branch": "feature",
        "target_branch": "main",
        "author": "dev",
        "title": "Dataverse mapping",
        "agent_results": [
            {
                "agent_name": "security",
                "findings": [
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "file": "a.py",
                        "line": 10,
                        "description": "fix 1",
                        "dismissal": {"status": "will_fix"},
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "medium",
                        "file": "b.py",
                        "line": 22,
                        "description": "fix 2",
                        "dismissal": {"status": "will_fix"},
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "severity": "high",
                        "file": "c.py",
                        "line": 33,
                        "description": "fix 3",
                        "dismissal": {"status": "will_fix"},
                    },
                ],
            }
        ],
    }
    adapter = _make_mock_adapter()
    adapter.post_inline_comments = AsyncMock(
        return_value=InlinePostResult(posted_ids=["t1", "t2", "t3"], skipped=[])
    )
    _patch_endpoint_deps(monkeypatch, review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{review['id']}/submit-verdict",
        json={"verdict": "decline", "comment": "Needs rework."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["platform_actions"] == [
        "post_inline_comments",
        "request_changes",
        "set_status:failure",
    ]

    adapter.post_inline_comments.assert_awaited_once()
    inline_arg = adapter.post_inline_comments.await_args.args[1]
    assert len(inline_arg) == 3
    assert {f.file for f in inline_arg} == {"a.py", "b.py", "c.py"}


def test_approve_flips_guardian_status_to_success(client, fake_review, monkeypatch):
    """Human approval must flip guardian/review -> success so a required status
    check unblocks merge. A bot APPROVE review alone leaves the check red."""
    adapter = _make_mock_adapter()
    adapter.set_review_status = AsyncMock(return_value=None)
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 200, resp.text
    adapter.set_review_status.assert_awaited_once()
    args = adapter.set_review_status.await_args.args
    assert args[0].repo == "org/repo"  # pr
    assert args[1] == "success"  # state


def test_decline_flips_guardian_status_to_failure(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    adapter.set_review_status = AsyncMock(return_value=None)
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "decline", "comment": "nope"},
    )
    assert resp.status_code == 200, resp.text
    adapter.set_review_status.assert_awaited_once()
    assert adapter.set_review_status.await_args.args[1] == "failure"


def test_approve_succeeds_when_adapter_has_no_set_review_status(client, fake_review, monkeypatch):
    """Adapters without a status mechanism (e.g. some platforms) must not break
    the verdict — the status flip is best-effort by capability."""
    adapter = _make_mock_adapter()
    # Simulate an adapter that genuinely lacks the method.
    if hasattr(adapter, "set_review_status"):
        delattr(adapter, "set_review_status")
    # AsyncMock auto-creates attributes; force getattr(...) to return None.
    adapter.mock_add_spec(
        ["approve_pr", "request_changes", "post_comment", "post_inline_comments"]
    )
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 200, resp.text
    actions = resp.json()["platform_actions"]
    assert "set_status:success" not in actions
    assert "approve_pr" in actions


def test_invalid_verdict_returns_400(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, fake_review, adapter)
    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "merge_anyway", "comment": ""},
    )
    assert resp.status_code == 400


def test_missing_review_returns_404(client, monkeypatch):
    from pr_guardian.api import dashboard as dash

    async def _no_review(_id):
        return None

    monkeypatch.setattr(dash.storage, "get_review", _no_review)
    rid = uuid.uuid4()
    resp = client.post(
        f"/api/dashboard/reviews/{rid}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 404


def test_platform_failure_returns_502_and_records_attempt(client, fake_review, monkeypatch):
    adapter = _make_mock_adapter()
    adapter.approve_pr = AsyncMock(side_effect=RuntimeError("network down"))
    appended = _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 502
    # Even on failure, the attempt is recorded in pipeline_log for audit.
    assert len(appended) == 1
    assert appended[0]["posted"] is False
    assert "network down" in appended[0]["error"]


def test_ado_review_recovers_project_from_pr_url(client, monkeypatch):
    """The reviews table does not persist org/project. For ADO, the handler
    must recover them from the stored pr_url so the platform receives a URL
    with a populated project segment."""
    ado_review = {
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
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, ado_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{ado_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 200, resp.text

    # The adapter was called with a PlatformPR carrying the recovered project.
    called_pr = adapter.approve_pr.await_args.args[0]
    assert called_pr.project == "MyProject"
    assert called_pr.org == "365projectum"
    assert called_pr.repo == "IntegrationHub"


def test_ado_review_without_pr_url_returns_422(client, monkeypatch):
    """If the ADO review has no usable pr_url, surface a clear 422 rather
    than letting ADO 400 with a confusing 'project name required' error."""
    ado_review = {
        "id": str(uuid.uuid4()),
        "pr_id": "13965",
        "repo": "IntegrationHub",
        "platform": "ado",
        "head_commit_sha": "abc123",
        "pr_url": "",  # nothing to recover from
        "source_branch": "feature",
        "target_branch": "main",
        "author": "dev",
        "title": "My PR",
        "agent_results": [],
    }
    adapter = _make_mock_adapter()
    _patch_endpoint_deps(monkeypatch, ado_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{ado_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 422, resp.text
    adapter.approve_pr.assert_not_awaited()


def test_httpx_status_error_surfaces_platform_body_and_returns_502(
    client, fake_review, monkeypatch
):
    """When the platform raises httpx.HTTPStatusError, the handler must surface
    the response body — not swallow it with a 500 from a variable-shadow bug."""
    import httpx

    request = httpx.Request(
        "POST", "https://dev.azure.com/foo/_apis/git/pullrequests/42/reviewers/me"
    )
    response = httpx.Response(
        status_code=400,
        request=request,
        text='{"message":"VS403072: cannot vote on own PR"}',
    )
    err = httpx.HTTPStatusError("400 Bad Request", request=request, response=response)

    adapter = _make_mock_adapter()
    adapter.approve_pr = AsyncMock(side_effect=err)
    appended = _patch_endpoint_deps(monkeypatch, fake_review, adapter)

    resp = client.post(
        f"/api/dashboard/reviews/{fake_review['id']}/submit-verdict",
        json={"verdict": "approve", "comment": ""},
    )
    assert resp.status_code == 502, resp.text
    assert len(appended) == 1
    assert appended[0]["posted"] is False
    assert "VS403072" in appended[0]["error"]
    assert "HTTP 400" in appended[0]["error"]
