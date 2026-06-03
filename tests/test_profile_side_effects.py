from __future__ import annotations

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
    return adapter


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
