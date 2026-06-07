"""Unit tests for GitHubAdapter."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.github import GitHubAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pr(pr_id: str = "42", repo: str = "owner/repo") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id=pr_id,
        repo=repo,
        repo_url="https://github.com/owner/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="My PR",
        head_commit_sha="abc123",
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


def _adapter(*responses: MagicMock) -> GitHubAdapter:
    adapter = GitHubAdapter(token="test-token")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(responses))
    adapter._client = mock_client
    return adapter


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_review_status_links_to_guardian_review():
    adapter = _adapter()
    adapter._client.post.return_value = _resp({})

    await adapter.set_review_status(
        _pr(),
        "failure",
        "Guardian review requested changes",
        target_url="https://guardian.example/reviews/abc",
    )

    adapter._client.post.assert_awaited_once()
    payload = adapter._client.post.await_args.kwargs["json"]
    assert payload["context"] == "guardian/review"
    assert payload["target_url"] == "https://guardian.example/reviews/abc"


@pytest.mark.asyncio
async def test_fetch_pr_body_and_commit_subjects_when_github_endpoints_succeed():
    pr_json = {"body": "My PR description"}
    commits_json = [
        {"commit": {"message": "feat: add feature\n\nDetails here"}},
        {"commit": {"message": "fix: small tweak"}},
    ]
    body, commits = await _adapter(_resp(pr_json), _resp(commits_json)).fetch_pr_body_and_commits(
        _pr()
    )
    assert body == "My PR description"
    assert commits == ["feat: add feature", "fix: small tweak"]


@pytest.mark.asyncio
async def test_multiline_commit_only_first_line():
    commits_json = [{"commit": {"message": "feat: headline\n\nExpanded description here."}}]
    _, commits = await _adapter(
        _resp({"body": ""}), _resp(commits_json)
    ).fetch_pr_body_and_commits(_pr())
    assert commits == ["feat: headline"]


@pytest.mark.asyncio
async def test_null_body_field_returned_as_empty():
    """GitHub returns body=null for PRs with no description."""
    pr_json = {"body": None}
    body, _ = await _adapter(_resp(pr_json), _resp([])).fetch_pr_body_and_commits(_pr())
    assert body == ""


@pytest.mark.asyncio
async def test_commit_without_message_is_skipped():
    commits_json = [
        {"commit": {}},
        {"commit": {"message": "valid commit"}},
    ]
    _, commits = await _adapter(
        _resp({"body": "x"}), _resp(commits_json)
    ).fetch_pr_body_and_commits(_pr())
    assert commits == ["valid commit"]


# ---------------------------------------------------------------------------
# Partial-failure paths — each half degrades gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_body_non200_commits_still_returned():
    commits_json = [{"commit": {"message": "fix: something"}}]
    body, commits = await _adapter(_resp({}, 404), _resp(commits_json)).fetch_pr_body_and_commits(
        _pr()
    )
    assert body == ""
    assert commits == ["fix: something"]


@pytest.mark.asyncio
async def test_commits_non200_body_still_returned():
    body, commits = await _adapter(
        _resp({"body": "Some description"}), _resp([], 500)
    ).fetch_pr_body_and_commits(_pr())
    assert body == "Some description"
    assert commits == []


@pytest.mark.asyncio
async def test_fetch_pr_body_and_commits_returns_empty_when_both_github_endpoints_fail():
    body, commits = await _adapter(_resp({}, 503), _resp({}, 503)).fetch_pr_body_and_commits(_pr())
    assert body == ""
    assert commits == []


@pytest.mark.asyncio
async def test_network_error_is_handled():
    """A transport-level error (not HTTP status) is also caught."""
    adapter = GitHubAdapter(token="test-token")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
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
    commits_json = [{"commit": {"message": "feat: cached"}}]
    pr_with_body = PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="owner/repo",
        repo_url="https://github.com/owner/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="My PR",
        head_commit_sha="abc123",
        body="already fetched description",
    )
    body, commits = await _adapter(_resp(commits_json)).fetch_pr_body_and_commits(pr_with_body)
    assert body == "already fetched description"
    assert commits == ["feat: cached"]


@pytest.mark.asyncio
async def test_empty_body_already_hydrated_skips_pr_get():
    """body='' means _hydrate_pr ran and the PR genuinely has no description.
    The adapter must NOT fire a second GET — the empty string is a valid fetched
    value, not the 'not yet fetched' sentinel (which is None)."""
    commits_json = [{"commit": {"message": "feat: no-desc commit"}}]
    pr_empty_body = PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="owner/repo",
        repo_url="https://github.com/owner/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="My PR",
        head_commit_sha="abc123",
        body="",  # hydrated — PR has no description
    )
    # Only one mock response (commits); a PR GET would exhaust the iterator and fail.
    body, commits = await _adapter(_resp(commits_json)).fetch_pr_body_and_commits(pr_empty_body)
    assert body == ""
    assert commits == ["feat: no-desc commit"]


# ---------------------------------------------------------------------------
# fetch_pr_metadata — fork detection
# ---------------------------------------------------------------------------


def _pr_api_response(
    *,
    head_full_name: str = "owner/repo",
    base_full_name: str = "owner/repo",
    head_repo_fork: bool = False,
    draft: bool = False,
    state: str = "open",
    merged: bool = False,
    sha: str = "abc123",
) -> dict:
    return {
        "state": state,
        "draft": draft,
        "merged": merged,
        "head": {
            "sha": sha,
            "repo": {"full_name": head_full_name, "fork": head_repo_fork},
        },
        "base": {
            "repo": {"full_name": base_full_name},
        },
    }


@pytest.mark.asyncio
async def test_branch_pr_in_forked_repo_is_not_fork():
    """A branch PR within a repo that is itself a GitHub fork must not be
    treated as a cross-fork PR.  head_repo.fork=True but full_names match."""
    data = _pr_api_response(
        head_full_name="org/portfolio-simulation",
        base_full_name="org/portfolio-simulation",
        head_repo_fork=True,  # the repo is a fork, but this is a branch PR
    )
    metadata = await _adapter(_resp(data)).fetch_pr_metadata(_pr())
    assert metadata.fork is False


@pytest.mark.asyncio
async def test_cross_fork_pr_is_fork():
    """A PR from a contributor's personal fork to the upstream repo is a fork PR."""
    data = _pr_api_response(
        head_full_name="alice/portfolio-simulation",
        base_full_name="org/portfolio-simulation",
        head_repo_fork=True,
    )
    metadata = await _adapter(_resp(data)).fetch_pr_metadata(_pr())
    assert metadata.fork is True


@pytest.mark.asyncio
async def test_same_repo_non_fork_pr_is_not_fork():
    """Normal branch PR, repo is not a fork at all — fork must be False."""
    data = _pr_api_response(
        head_full_name="org/repo",
        base_full_name="org/repo",
        head_repo_fork=False,
    )
    metadata = await _adapter(_resp(data)).fetch_pr_metadata(_pr())
    assert metadata.fork is False


# ---------------------------------------------------------------------------
# Readiness signals: Checks API with Actions fallback for fine-grained PATs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_signals_use_check_runs_when_readable():
    """Classic PAT / GitHub App: the Checks API is readable, so check runs are used
    directly and the Actions API is never touched."""
    adapter = _adapter(
        _resp({"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}),
        _resp({"statuses": []}),
    )
    signals = await adapter.fetch_readiness_signals(_pr())
    assert [(s.name, s.state, s.source) for s in signals] == [("build", "success", "check_run")]


@pytest.mark.asyncio
async def test_readiness_signals_fall_back_to_actions_when_checks_forbidden():
    """Fine-grained PAT: check-runs 403s, so the adapter falls back to the Actions API
    and surfaces workflow runs as check_run signals, plus any commit statuses."""
    adapter = _adapter(
        _resp({}, status_code=403),  # check-runs denied
        _resp(  # actions/runs fallback
            {
                "workflow_runs": [
                    {"name": "Build & Smoke", "status": "completed", "conclusion": "success"},
                    {"name": "Lint", "status": "in_progress", "conclusion": None},
                ]
            }
        ),
        _resp({"statuses": [{"context": "legacy-ci", "state": "success"}]}),
    )
    signals = await adapter.fetch_readiness_signals(_pr())
    assert ("Build & Smoke", "success", "check_run") in [
        (s.name, s.state, s.source) for s in signals
    ]
    assert ("Lint", "in_progress", "check_run") in [(s.name, s.state, s.source) for s in signals]
    assert ("legacy-ci", "success", "status") in [(s.name, s.state, s.source) for s in signals]


@pytest.mark.asyncio
async def test_readiness_signals_degrade_to_statuses_when_actions_also_forbidden():
    """Neither Checks nor Actions scope: degrade to commit-statuses-only rather than
    wedge the whole gate."""
    adapter = _adapter(
        _resp({}, status_code=403),  # check-runs denied
        _resp({}, status_code=403),  # actions/runs also denied
        _resp({"statuses": [{"context": "legacy-ci", "state": "pending"}]}),
    )
    signals = await adapter.fetch_readiness_signals(_pr())
    assert [(s.name, s.state, s.source) for s in signals] == [("legacy-ci", "pending", "status")]


@pytest.mark.asyncio
async def test_readiness_signals_propagate_non_auth_errors():
    """A 500 on check-runs is a real platform error, not a permission gap — it must
    propagate (surfacing as platform_error), never be silently swallowed."""
    adapter = _adapter(_resp({}, status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.fetch_readiness_signals(_pr())


# ---------------------------------------------------------------------------
# GitHub App installation token authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_adapter_uses_installation_token_for_platform_actions():
    """_get_client() app_auth branch is exercised: _InstallationBearerAuth is
    instantiated by _get_client() itself and injects Bearer tokens on every request.
    No PAT or GITHUB_TOKEN value is read from the environment."""
    from pr_guardian.platform.github_auth import _InstallationBearerAuth

    captured_auth_headers: list[str] = []
    captured_auth_instances: list[object] = []
    _orig_async_client = httpx.AsyncClient

    class _CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured_auth_headers.append(request.headers.get("authorization", ""))
            return httpx.Response(200, json={})

    mock_app_auth = MagicMock()
    mock_app_auth.get_token = AsyncMock(return_value="ghs-installation-token-abc123")

    def _capturing_client(*args, **kwargs):
        # Record the auth instance _get_client() supplies, then inject our transport
        captured_auth_instances.append(kwargs.get("auth"))
        kwargs["transport"] = _CapturingTransport()
        return _orig_async_client(*args, **kwargs)

    adapter = GitHubAdapter(app_auth=mock_app_auth)
    pr = _pr()

    # Patch httpx.AsyncClient in the github module so _get_client() runs naturally
    # through the app_auth branch while requests are captured by _CapturingTransport.
    with patch("pr_guardian.platform.github.httpx.AsyncClient", side_effect=_capturing_client):
        await adapter.post_comment(pr, "Guardian review complete")
        await adapter.set_status(pr, "success", "All checks passed", context="guardian/review")
        await adapter.approve_pr(pr)

    # _get_client() created the client exactly once (cached thereafter)
    assert len(captured_auth_instances) == 1
    assert isinstance(captured_auth_instances[0], _InstallationBearerAuth), (
        f"Expected _InstallationBearerAuth, got {type(captured_auth_instances[0])!r}"
    )

    # Every request must carry Bearer auth with the installation token
    assert len(captured_auth_headers) == 3
    for auth_header in captured_auth_headers:
        assert auth_header == "Bearer ghs-installation-token-abc123", (
            f"Expected Bearer installation token, got: {auth_header!r}"
        )

    # get_token() is called per-request by the auth flow
    assert mock_app_auth.get_token.call_count == 3

    # GITHUB_TOKEN env var must not appear in any auth header
    env_token = os.environ.get("GITHUB_TOKEN", "")
    if env_token:
        for auth_header in captured_auth_headers:
            assert env_token not in auth_header
