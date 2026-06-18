from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.orchestrator import _post_results
from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.protocol import InlinePostResult, PlatformPRMetadata


def _pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="https://github.com/org/repo",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="Feature",
        head_commit_sha="sha1",
        org="org",
    )


def _result() -> ReviewResult:
    return ReviewResult(
        pr_id="42",
        repo="org/repo",
        risk_tier=RiskTier.TRIVIAL,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=Decision.AUTO_APPROVE,
        summary="No blocking findings.",
    )


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.post_comment = AsyncMock()
    adapter.post_inline_comments = AsyncMock(
        return_value=InlinePostResult(posted_ids=[], skipped=[])
    )
    adapter.delete_inline_comments = AsyncMock()
    adapter.add_label = AsyncMock()
    adapter.request_reviewers = AsyncMock()
    adapter.approve_pr = AsyncMock()
    adapter.request_changes = AsyncMock()
    adapter.set_review_status = AsyncMock()
    adapter.fetch_pr_metadata = AsyncMock(return_value=PlatformPRMetadata(head_sha="sha1"))
    adapter.upsert_guidance_comment = AsyncMock(return_value=None)
    return adapter


@pytest.mark.asyncio
async def test_auto_approve_is_guardian_clearance_unless_profile_enables_platform_approval():
    disabled = GuardianConfig()
    disabled.platform_approval_enabled = False
    disabled.side_effects.formal_approve = True
    first = _adapter()

    await _post_results(
        first,
        _pr(),
        _result(),
        disabled,
        comment_mode="summary",
        manual_comment_override=False,
    )

    first.set_review_status.assert_awaited_once()
    assert first.set_review_status.await_args.args[2] == "Guardian cleared"
    first.approve_pr.assert_not_awaited()
    first.post_comment.assert_not_awaited()

    enabled = GuardianConfig()
    enabled.platform_approval_enabled = True
    enabled.side_effects.formal_approve = True
    second = _adapter()

    await _post_results(
        second,
        _pr(),
        _result(),
        enabled,
        comment_mode="summary",
        manual_comment_override=False,
    )

    second.approve_pr.assert_awaited_once()


@pytest.mark.asyncio
async def test_github_app_formal_approval_requires_profile_switches_and_non_fork():
    """Formal approval is gated on Profile switches and fork check.

    Cases tested:
    1. AUTO_APPROVE + platform_approval_enabled=True + formal_approve=True + fork=False → approve
    2. AUTO_APPROVE + platform_approval_enabled=False → no approve, postback skipped_profile
    3. AUTO_APPROVE + platform_approval_enabled=True + formal_approve=True + fork=True → no approve
    """
    # Case 1: all gates pass → approve_pr called
    config_full = GuardianConfig()
    config_full.platform_approval_enabled = True
    config_full.side_effects.formal_approve = True
    full_adapter = _adapter()
    full_adapter.fetch_pr_metadata = AsyncMock(
        return_value=PlatformPRMetadata(head_sha="sha1", fork=False)
    )

    await _post_results(
        full_adapter,
        _pr(),
        _result(),
        config_full,
        comment_mode="summary",
        manual_comment_override=False,
    )

    full_adapter.approve_pr.assert_awaited_once()

    # Case 2: platform_approval_enabled=False → no approve
    config_disabled = GuardianConfig()
    config_disabled.platform_approval_enabled = False
    config_disabled.side_effects.formal_approve = True
    disabled_adapter = _adapter()
    result_disabled = _result()

    await _post_results(
        disabled_adapter,
        _pr(),
        result_disabled,
        config_disabled,
        comment_mode="summary",
        manual_comment_override=False,
    )

    disabled_adapter.approve_pr.assert_not_awaited()
    assert result_disabled.postback_meta.get("formal_approval") == "skipped_profile"

    # Case 3: fork=True → no approve even if profile switches enabled
    config_fork = GuardianConfig()
    config_fork.platform_approval_enabled = True
    config_fork.side_effects.formal_approve = True
    fork_adapter = _adapter()
    fork_adapter.fetch_pr_metadata = AsyncMock(
        return_value=PlatformPRMetadata(head_sha="sha1", fork=True)
    )
    result_fork = _result()

    await _post_results(
        fork_adapter,
        _pr(),
        result_fork,
        config_fork,
        comment_mode="summary",
        manual_comment_override=False,
    )

    fork_adapter.approve_pr.assert_not_awaited()
    assert result_fork.postback_meta.get("formal_approval") == "skipped_fork"


@pytest.mark.asyncio
async def test_completed_review_reasserts_readiness_success():
    """A completed review re-posts guardian/readiness=success.

    Guards the orphaned-readiness bug: when the reconciler's readiness-success
    write fails mid-flight it proceeds with the review, then the candidate goes
    terminal and is never re-evaluated — stranding guardian/readiness at its
    last value. Posting success on result completion self-heals that.
    """
    adapter = _adapter()
    adapter.set_readiness_status = AsyncMock()

    await _post_results(
        adapter,
        _pr(),
        _result(),
        GuardianConfig(),
        comment_mode="summary",
        manual_comment_override=False,
    )

    adapter.set_readiness_status.assert_awaited_once()
    assert adapter.set_readiness_status.await_args.args[1] == "success"


@pytest.mark.asyncio
async def test_readiness_reassert_failure_does_not_break_result_posting():
    """A failing readiness-status write must not abort _post_results."""
    adapter = _adapter()
    adapter.set_readiness_status = AsyncMock(side_effect=RuntimeError("boom"))
    result = _result()

    await _post_results(
        adapter,
        _pr(),
        result,
        GuardianConfig(),
        comment_mode="summary",
        manual_comment_override=False,
    )

    # Review status still posted despite the readiness write blowing up.
    adapter.set_review_status.assert_awaited_once()
    assert result.postback_meta.get("readiness_status") == "write_failed"
