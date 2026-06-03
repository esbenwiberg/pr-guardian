"""Unit tests for post_inline_comments / delete_inline_comments on GitHub and ADO adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pr_guardian.models.findings import Certainty, Finding, Severity
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.protocol import InlinePostResult


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

    review_resp = _mock_response(201, {"id": 999})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=review_resp)

    with patch.object(adapter, "_get_client", return_value=mock_client):
        result = await adapter.post_inline_comments(pr, findings)

    assert result.posted_ids == ["999"]
    assert result.skipped == []
    mock_client.post.assert_called_once()
    call_args, call_kwargs = mock_client.post.call_args
    assert call_args[0] == "/repos/org/repo/pulls/42/comments"
    assert call_kwargs["json"]["commit_id"] == "abc123"
    assert call_kwargs["json"]["line"] == 10
    assert call_kwargs["json"]["side"] == "RIGHT"


@pytest.mark.asyncio
async def test_github_post_inline_skips_422():
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()
    # Two findings at different lines: first is outside diff (422), second is valid
    f1 = _finding("src/foo.py", 5)
    f2 = _finding("src/bar.py", 20)

    resp_422 = _mock_response(422, {})
    resp_ok = _mock_response(201, {"id": 777})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_422, resp_ok])

    with patch.object(adapter, "_get_client", return_value=mock_client):
        result = await adapter.post_inline_comments(pr, [f1, f2])

    assert result.posted_ids == ["777"]
    assert len(result.skipped) == 1
    assert result.skipped[0].file == "src/foo.py"
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
        result = await adapter.post_inline_comments(pr, [f])

    assert result.posted_ids == []
    assert len(result.skipped) == 1
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
        result = await adapter.post_inline_comments(pr, [f1, f2])

    assert result.posted_ids == ["100"]
    assert result.skipped == []
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
        result = await adapter.post_inline_comments(pr, findings)

    assert result.posted_ids == ["42"]
    assert result.skipped == []
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
        result = await adapter.post_inline_comments(pr, [f1, f2])

    assert result.posted_ids == ["55"]
    assert len(result.skipped) == 1
    assert result.skipped[0].file == "src/outside.py"
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


# ---------------------------------------------------------------------------
# GitHub — delete negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_delete_ignores_404():
    """404 on a stale comment ID should be silently skipped; remaining IDs still processed."""
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()

    mock_client = MagicMock()
    mock_client.delete = AsyncMock(
        side_effect=[
            _mock_response(404, {}),
            _mock_response(204, {}),
        ]
    )

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.delete_inline_comments(pr, ["stale", "valid"])

    assert mock_client.delete.call_count == 2


@pytest.mark.asyncio
async def test_github_delete_raises_on_non_404_error():
    """Non-404 HTTP errors (e.g. 500) should propagate."""
    adapter = GitHubAdapter(token="tok")
    pr = _make_github_pr()

    mock_client = MagicMock()
    mock_client.delete = AsyncMock(return_value=_mock_response(500, {}))

    with patch.object(adapter, "_get_client", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.delete_inline_comments(pr, ["111"])


# ---------------------------------------------------------------------------
# ADO — delete negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ado_delete_ignores_404():
    """404 on a stale thread ID should be silently skipped; remaining IDs still processed."""
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(
        side_effect=[
            _mock_response(404, {}),
            _mock_response(200, {}),
        ]
    )
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))

    with patch.object(adapter, "_get_client", return_value=mock_client):
        await adapter.delete_inline_comments(pr, ["stale", "valid"])

    assert mock_client.patch.call_count == 2
    # reply comment only posted for the successful patch, not the 404
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_ado_delete_raises_on_non_404_error():
    """Non-404 HTTP errors (e.g. 500) should propagate."""
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()

    mock_client = MagicMock()
    mock_client.patch = AsyncMock(return_value=_mock_response(500, {}))

    with patch.object(adapter, "_get_client", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.delete_inline_comments(pr, ["99"])


@pytest.mark.asyncio
async def test_ado_delete_reply_failure_does_not_abort_batch():
    """If posting the superseded reply fails, the batch continues for remaining thread IDs."""
    adapter = ADOAdapter(pat="pat", org_url="https://dev.azure.com/myorg")
    pr = _make_ado_pr()

    mock_client = MagicMock()
    # Both PATCHes succeed
    mock_client.patch = AsyncMock(return_value=_mock_response(200, {}))
    # First reply POST fails with 403, second succeeds
    mock_client.post = AsyncMock(
        side_effect=[
            _mock_response(403, {}),
            _mock_response(200, {}),
        ]
    )

    with patch.object(adapter, "_get_client", return_value=mock_client):
        # Should not raise even though first reply POST returns 403
        await adapter.delete_inline_comments(pr, ["thread1", "thread2"])

    assert mock_client.patch.call_count == 2
    assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# Orchestrator _post_results — inline comment mode
# ---------------------------------------------------------------------------

import uuid as _uuid

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.orchestrator import _post_results
from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.findings import AgentResult, Verdict
from pr_guardian.models.output import Decision, ReviewResult


def _make_result(agent_findings: list[Finding] | None = None) -> ReviewResult:
    """Build a minimal ReviewResult with optional agent findings."""
    findings = agent_findings or []
    verdict = Verdict.WARN if findings else Verdict.PASS
    ar = AgentResult(agent_name="security_privacy", verdict=verdict, findings=findings)
    return ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.MEDIUM,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.HUMAN_REVIEW,
        agent_results=[ar] if findings else [],
    )


def _inline_finding(severity: Severity, line: int | None = 10) -> Finding:
    return Finding(
        severity=severity,
        certainty=Certainty.DETECTED,
        category="Test",
        language="python",
        file="src/app.py",
        line=line,
        description="A finding.",
        suggestion="Fix it.",
    )


def _mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.post_comment = AsyncMock()
    adapter.post_inline_comments = AsyncMock(
        return_value=InlinePostResult(posted_ids=["id-1", "id-2"], skipped=[])
    )
    adapter.delete_inline_comments = AsyncMock()
    adapter.add_label = AsyncMock()
    adapter.set_status = AsyncMock()
    adapter.request_reviewers = AsyncMock()
    adapter.approve_pr = AsyncMock()
    adapter.request_changes = AsyncMock()
    return adapter


def _mock_storage(existing_ids: list[str] | None = None) -> MagicMock:
    storage = MagicMock()
    storage.save_inline_comment_ids = AsyncMock()
    storage.load_inline_comment_ids = AsyncMock(return_value=existing_ids or [])
    return storage


@pytest.mark.asyncio
async def test_inline_mode_filters_below_threshold():
    """Only MEDIUM+ findings (by default threshold) reach post_inline_comments."""
    pr = _make_github_pr()
    low_finding = _inline_finding(Severity.LOW, line=5)
    medium_finding = _inline_finding(Severity.MEDIUM, line=10)
    high_finding = _inline_finding(Severity.HIGH, line=20)
    result = _make_result([low_finding, medium_finding, high_finding])

    adapter = _mock_adapter()
    storage = _mock_storage()
    config = GuardianConfig()  # default threshold = MEDIUM

    await _post_results(
        adapter,
        pr,
        result,
        config,
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_called_once()
    passed_findings = adapter.post_inline_comments.call_args[0][1]
    severities = {f.severity for f in passed_findings}
    assert Severity.LOW not in severities
    assert Severity.MEDIUM in severities
    assert Severity.HIGH in severities


@pytest.mark.asyncio
async def test_inline_mode_posts_summary_after_inline():
    """In inline mode, post_comment (summary) is called after post_inline_comments."""
    pr = _make_github_pr()
    result = _make_result([_inline_finding(Severity.HIGH)])

    adapter = _mock_adapter()
    call_order: list[str] = []
    adapter.post_inline_comments = AsyncMock(
        side_effect=lambda *a, **kw: call_order.append("inline")
        or InlinePostResult(posted_ids=["id-1"], skipped=[])
    )
    adapter.post_comment = AsyncMock(side_effect=lambda *a, **kw: call_order.append("summary"))
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    assert "inline" in call_order
    assert "summary" in call_order
    assert call_order.index("inline") < call_order.index("summary")


@pytest.mark.asyncio
async def test_summary_mode_never_calls_post_inline_comments():
    """comment_mode='summary' must not touch post_inline_comments."""
    pr = _make_github_pr()
    result = _make_result([_inline_finding(Severity.HIGH)])

    adapter = _mock_adapter()
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="summary",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_not_called()
    adapter.post_comment.assert_called_once()


@pytest.mark.asyncio
async def test_inline_rereview_deletes_before_posting():
    """Re-review inline mode calls delete_inline_comments before post_inline_comments."""
    pr = _make_github_pr()
    result = _make_result([_inline_finding(Severity.HIGH)])
    old_ids = ["stale-1", "stale-2"]
    storage = _mock_storage(existing_ids=old_ids)

    adapter = _mock_adapter()
    call_order: list[str] = []
    adapter.delete_inline_comments = AsyncMock(
        side_effect=lambda *a, **kw: call_order.append("delete")
    )
    adapter.post_inline_comments = AsyncMock(
        side_effect=lambda *a, **kw: call_order.append("post")
        or InlinePostResult(posted_ids=["new-1"], skipped=[])
    )

    original_review_id = str(_uuid.uuid4())

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        original_review_id=original_review_id,
        manual_comment_override=True,
    )

    storage.load_inline_comment_ids.assert_called_once()
    adapter.delete_inline_comments.assert_called_once()
    deleted_ids = adapter.delete_inline_comments.call_args[0][1]
    assert deleted_ids == old_ids

    assert "delete" in call_order
    assert "post" in call_order
    assert call_order.index("delete") < call_order.index("post")


# ---------------------------------------------------------------------------
# build_inline_comment_body — format contract
# ---------------------------------------------------------------------------

from pr_guardian.decision.actions import build_inline_comment_body


def test_build_inline_comment_body_single_with_suggestion():
    f = Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="SQL Injection",
        language="python",
        file="src/foo.py",
        line=10,
        description="Unsanitised input.",
        suggestion="Use parameterised queries.",
    )
    body = build_inline_comment_body([f])
    assert body == "**[HIGH] SQL Injection**\nUnsanitised input.\n\n> Use parameterised queries."


def test_build_inline_comment_body_single_without_suggestion():
    f = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="Code Quality",
        language="python",
        file="src/foo.py",
        line=5,
        description="Long function.",
    )
    body = build_inline_comment_body([f])
    assert body == "**[MEDIUM] Code Quality**\nLong function."
    assert ">" not in body


def test_build_inline_comment_body_multiple_findings_separator():
    f1 = Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="Bug",
        language="python",
        file="src/x.py",
        line=1,
        description="Bug here.",
        suggestion="Fix it.",
    )
    f2 = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="Style",
        language="python",
        file="src/x.py",
        line=1,
        description="Style issue.",
    )
    body = build_inline_comment_body([f1, f2])
    assert "---" in body
    assert "**[HIGH] Bug**" in body
    assert "**[MEDIUM] Style**" in body


def test_build_inline_comment_body_severity_uppercased():
    f = Finding(
        severity=Severity.CRITICAL,
        certainty=Certainty.DETECTED,
        category="XSS",
        language="javascript",
        file="src/ui.js",
        line=42,
        description="XSS vulnerability.",
    )
    body = build_inline_comment_body([f])
    assert "[CRITICAL]" in body
    assert "[critical]" not in body


def test_build_inline_comment_body_low_severity_uppercased():
    f = Finding(
        severity=Severity.LOW,
        certainty=Certainty.DETECTED,
        category="Lint",
        language="python",
        file="src/foo.py",
        line=3,
        description="Minor style nit.",
    )
    body = build_inline_comment_body([f])
    assert body.startswith("**[LOW] Lint**")


# ---------------------------------------------------------------------------
# Orchestrator _post_inline_and_summary — mechanical findings path
# ---------------------------------------------------------------------------

from pr_guardian.models.output import MechanicalResult


def _make_result_with_mech(
    mech_results: list[MechanicalResult],
    agent_findings: list[Finding] | None = None,
) -> ReviewResult:
    findings = agent_findings or []
    verdict = Verdict.WARN if findings else Verdict.PASS
    ar = AgentResult(agent_name="security_privacy", verdict=verdict, findings=findings)
    return ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.MEDIUM,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.HUMAN_REVIEW,
        agent_results=[ar] if findings else [],
        mechanical_results=mech_results,
    )


@pytest.mark.asyncio
async def test_inline_mode_mech_findings_skips_none_line():
    """Mechanical findings with line=None are silently skipped."""
    pr = _make_github_pr()
    mech = MechanicalResult(
        tool="ruff",
        passed=False,
        severity="error",
        findings=[
            {"file": "src/foo.py", "line": None, "rule": "E501", "message": "Line too long"},
            {"file": "src/bar.py", "line": None, "rule": "E302", "message": "2 blank lines"},
        ],
    )
    result = _make_result_with_mech([mech])

    adapter = _mock_adapter()
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_not_called()


@pytest.mark.asyncio
async def test_inline_mode_mech_findings_error_severity_passes_threshold():
    """Mechanical findings with severity='error' (mapped to HIGH) pass MEDIUM threshold."""
    pr = _make_github_pr()
    mech = MechanicalResult(
        tool="bandit",
        passed=False,
        severity="error",
        findings=[
            {"file": "src/app.py", "line": 42, "rule": "B101", "message": "Assert used"},
        ],
    )
    result = _make_result_with_mech([mech])

    adapter = _mock_adapter()
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_called_once()
    passed_findings = adapter.post_inline_comments.call_args[0][1]
    assert len(passed_findings) == 1
    assert passed_findings[0].file == "src/app.py"
    assert passed_findings[0].line == 42


@pytest.mark.asyncio
async def test_inline_mode_mech_findings_info_severity_filtered_out():
    """Mechanical findings with severity='info' (mapped to LOW) are filtered by MEDIUM threshold."""
    pr = _make_github_pr()
    mech = MechanicalResult(
        tool="pylint",
        passed=False,
        severity="info",
        findings=[
            {"file": "src/app.py", "line": 10, "rule": "C0103", "message": "Invalid name"},
        ],
    )
    result = _make_result_with_mech([mech])

    adapter = _mock_adapter()
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_not_called()


@pytest.mark.asyncio
async def test_inline_mode_mech_findings_warning_passes_medium_threshold():
    """Mechanical findings with severity='warning' (mapped to MEDIUM) pass MEDIUM threshold."""
    pr = _make_github_pr()
    mech = MechanicalResult(
        tool="mypy",
        passed=False,
        severity="warning",
        findings=[
            {
                "file": "src/types.py",
                "line": 7,
                "rule": "arg-type",
                "message": "Incompatible type",
            },
        ],
    )
    result = _make_result_with_mech([mech])

    adapter = _mock_adapter()
    storage = _mock_storage()

    await _post_results(
        adapter,
        pr,
        result,
        GuardianConfig(),
        comment_mode="inline",
        review_id=_uuid.uuid4(),
        storage=storage,
        manual_comment_override=True,
    )

    adapter.post_inline_comments.assert_called_once()
    passed_findings = adapter.post_inline_comments.call_args[0][1]
    assert any(f.line == 7 for f in passed_findings)
