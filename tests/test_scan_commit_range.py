"""Path A: macro scan over an explicit base..head commit range."""

from __future__ import annotations

from unittest.mock import patch

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.recent_changes import run_recent_changes_scan
from pr_guardian.models.findings import Verdict
from pr_guardian.models.pr import Diff, DiffFile
from pr_guardian.models.scan import ScanAgentResult


class _FakeAgent:
    """Stand-in scan agent — returns one clean (finding-free) result, no LLM."""

    def __init__(self, config):
        self.config = config

    async def analyze(self, context):
        return ScanAgentResult(
            agent_name="fake",
            verdict=Verdict.PASS,
            findings=[],
            summary="fake ok",
        )


class _RangeScanAdapter:
    def __init__(self, compare: Diff):
        self._compare = compare
        self.compare_calls: list[tuple] = []

    async def fetch_compare_diff(self, repo, base_sha, head_sha, project=""):
        self.compare_calls.append((repo, base_sha, head_sha))
        return self._compare

    async def fetch_merged_prs(self, *a, **k):  # pragma: no cover - must not be called
        raise AssertionError("range mode must not enumerate merged PRs")

    async def fetch_recent_commits(self, *a, **k):  # pragma: no cover
        raise AssertionError("range mode must not fetch recent commits")


async def test_scan_range_mode_uses_compare_diff_and_records_range():
    compare = Diff(files=[DiffFile(path="src/x.py", status="modified", patch="+a\n+b")])
    adapter = _RangeScanAdapter(compare)

    with patch(
        "pr_guardian.core.recent_changes.RECENT_CHANGES_AGENTS",
        {"fake": _FakeAgent},
    ):
        result = await run_recent_changes_scan(
            repo="o/r",
            platform="github",
            adapter=adapter,
            config=GuardianConfig(),
            base_ref="basesha",
            head_ref="headsha",
        )

    assert adapter.compare_calls == [("o/r", "basesha", "headsha")]
    assert result.base_sha == "basesha"
    assert result.head_sha == "headsha"


async def test_scan_range_mode_head_defaults_to_branch():
    compare = Diff(files=[DiffFile(path="src/x.py", status="modified", patch="+a")])
    adapter = _RangeScanAdapter(compare)
    config = GuardianConfig()

    with patch(
        "pr_guardian.core.recent_changes.RECENT_CHANGES_AGENTS",
        {"fake": _FakeAgent},
    ):
        result = await run_recent_changes_scan(
            repo="o/r",
            platform="github",
            adapter=adapter,
            config=config,
            base_ref="basesha",
        )

    # head_ref omitted → falls back to the configured branch.
    assert adapter.compare_calls[0][2] == config.recent_changes.branch
    assert result.head_sha == config.recent_changes.branch


async def test_scan_range_mode_empty_range_short_circuits():
    adapter = _RangeScanAdapter(Diff(files=[]))

    with patch(
        "pr_guardian.core.recent_changes.RECENT_CHANGES_AGENTS",
        {"fake": _FakeAgent},
    ):
        result = await run_recent_changes_scan(
            repo="o/r",
            platform="github",
            adapter=adapter,
            config=GuardianConfig(),
            base_ref="basesha",
            head_ref="headsha",
        )

    assert result.total_findings == 0
    assert "No changes" in result.summary
    assert result.base_sha == "basesha"
