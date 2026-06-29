"""Deep ("fat nightly") scan: re-review each merged PR at full PR-review depth.

These tests mock ``run_review`` so the orchestration + mapping logic is exercised
without invoking the real LLM pipeline. A separate test pins the ``persist=False``
contract on ``run_review`` itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core import pr_like_scan
from pr_guardian.core.pr_like_scan import run_pr_like_scan
from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    Finding,
    Severity,
    Verdict,
)
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import Diff, DiffFile
from pr_guardian.models.scan import ScanType


def _gh_pr(number: int, *, base="b" * 40, head="h" * 40, title="t") -> dict:
    return {
        "number": number,
        "title": f"{title}{number}",
        "user": {"login": "alice"},
        "base": {"ref": "main", "sha": f"{base}{number}"},
        "head": {"ref": f"feat-{number}", "sha": f"{head}{number}"},
    }


def _review_result(decision: Decision, *, findings=None, score=0.5, cost=0.01) -> ReviewResult:
    return ReviewResult(
        pr_id="x",
        repo="o/r",
        risk_tier=RiskTier.LOW,
        repo_risk_class=RepoRiskClass.STANDARD,
        decision=decision,
        combined_score=score,
        agent_results=[
            AgentResult(
                agent_name="security_privacy",
                verdict=Verdict.FLAG_HUMAN,
                findings=findings or [],
            )
        ],
        summary=f"summary for {decision.value}",
        cost_usd=cost,
        total_input_tokens=100,
        total_output_tokens=50,
    )


class _DeepAdapter:
    def __init__(self, prs: list[dict], compare: Diff | None = None):
        self._prs = prs
        self._compare = compare or Diff(files=[DiffFile(path="a.py", status="modified", patch="+x")])
        self.merged_calls: list[dict] = []
        self.compare_calls: list[tuple] = []

    async def fetch_merged_prs(self, repo, since, base="main"):
        self.merged_calls.append({"repo": repo, "since": since, "base": base})
        return list(self._prs)

    async def fetch_compare_diff(self, repo, base_sha, head_sha, project=""):
        self.compare_calls.append((repo, base_sha, head_sha))
        return self._compare


def _finding() -> Finding:
    return Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="injection",
        language="python",
        file="a.py",
        line=3,
        description="SQL injection",
    )


async def test_deep_scan_reviews_each_pr_and_maps_results():
    adapter = _DeepAdapter([_gh_pr(1), _gh_pr(2)])

    async def fake_run_review(pr, _adapter, **kwargs):
        # First PR clean auto-approve, second hard-blocks with a finding.
        if pr.pr_id == "1":
            return _review_result(Decision.AUTO_APPROVE, score=0.95)
        return _review_result(Decision.HARD_BLOCK, findings=[_finding()], score=0.1)

    with patch.object(pr_like_scan, "run_review", side_effect=fake_run_review) as rr:
        result = await run_pr_like_scan(
            repo="o/r", platform="github", adapter=adapter, config=GuardianConfig()
        )

    assert result.scan_type == ScanType.RECENT_CHANGES_DEEP
    assert len(result.agent_results) == 2
    by_name = {ar.agent_name: ar for ar in result.agent_results}
    pr1 = next(ar for n, ar in by_name.items() if n.startswith("PR #1"))
    pr2 = next(ar for n, ar in by_name.items() if n.startswith("PR #2"))

    # Decision → verdict mapping + per-PR summary content.
    assert pr1.verdict == Verdict.PASS
    assert "Auto-approve" in pr1.summary and "github.com/o/r/pull/1" in pr1.summary
    assert pr2.verdict == Verdict.FLAG_HUMAN
    assert "Hard block" in pr2.summary

    # Findings flatten through, tagged with the originating lens.
    assert result.total_findings == 1
    assert pr2.findings[0].category == "Security/Privacy"
    assert pr2.findings[0].severity == Severity.HIGH

    # Cost aggregated across PRs.
    assert result.cost_usd == 0.02
    assert result.total_input_tokens == 200

    # Every review ran off-platform and unpersisted, with a pre-built diff.
    for call in rr.call_args_list:
        assert call.kwargs["persist"] is False
        assert call.kwargs["skip_platform_side_effects"] is True
        assert call.kwargs["post_comment"] is False
        assert call.kwargs["diff_override"] is not None


async def test_deep_scan_caps_pr_count():
    prs = [_gh_pr(i) for i in range(1, 11)]
    adapter = _DeepAdapter(prs)
    config = GuardianConfig()
    config.recent_changes.deep_max_prs = 3

    with patch.object(
        pr_like_scan, "run_review", AsyncMock(return_value=_review_result(Decision.AUTO_APPROVE))
    ) as rr:
        result = await run_pr_like_scan(
            repo="o/r", platform="github", adapter=adapter, config=config
        )

    assert rr.await_count == 3
    assert len(result.agent_results) == 3
    # No silent truncation — the cap is logged.
    assert any("Capped at 3" in e["msg"] for e in result.pipeline_log)


async def test_deep_scan_skips_unresolvable_pr():
    bad = {"number": 9, "title": "no shas", "user": {"login": "x"}}  # missing base/head
    adapter = _DeepAdapter([_gh_pr(1), bad])

    with patch.object(
        pr_like_scan, "run_review", AsyncMock(return_value=_review_result(Decision.AUTO_APPROVE))
    ) as rr:
        result = await run_pr_like_scan(
            repo="o/r", platform="github", adapter=adapter, config=GuardianConfig()
        )

    assert rr.await_count == 1  # only the resolvable PR
    assert len(result.agent_results) == 1
    assert any("could not resolve" in e["msg"] for e in result.pipeline_log)


async def test_deep_scan_empty_window():
    adapter = _DeepAdapter([])

    with patch.object(pr_like_scan, "run_review", AsyncMock()) as rr:
        result = await run_pr_like_scan(
            repo="o/r", platform="github", adapter=adapter, config=GuardianConfig()
        )

    assert rr.await_count == 0
    assert result.total_findings == 0
    assert "No merged PRs" in result.summary


async def test_deep_scan_isolates_one_pr_failure():
    adapter = _DeepAdapter([_gh_pr(1), _gh_pr(2)])

    async def flaky(pr, _adapter, **kwargs):
        if pr.pr_id == "2":
            raise RuntimeError("boom")
        return _review_result(Decision.AUTO_APPROVE)

    with patch.object(pr_like_scan, "run_review", side_effect=flaky):
        result = await run_pr_like_scan(
            repo="o/r", platform="github", adapter=adapter, config=GuardianConfig()
        )

    assert len(result.agent_results) == 2  # the failure is captured, not dropped
    failed = next(ar for ar in result.agent_results if ar.error)
    assert "boom" in failed.error


# ---------------------------------------------------------------------------
# run_review(persist=False) contract: no review DB row is created.
# ---------------------------------------------------------------------------


async def test_run_review_persist_false_creates_no_row():
    from pr_guardian.core import orchestrator
    from pr_guardian.models.pr import Platform, PlatformPR

    pr = PlatformPR(
        platform=Platform.GITHUB,
        pr_id="1",
        repo="o/r",
        repo_url="",
        source_branch="feat",
        target_branch="main",
        author="a",
        title="t",
        head_commit_sha="h" * 40,
    )
    diff = Diff(files=[DiffFile(path="a.py", status="modified", patch="+x")])
    storage = AsyncMock()
    sentinel = _review_result(Decision.AUTO_APPROVE)

    with (
        patch.object(orchestrator, "_try_import_storage", return_value=storage),
        patch.object(orchestrator, "_run_pipeline", AsyncMock(return_value=sentinel)) as pipe,
    ):
        result = await orchestrator.run_review(
            pr, AsyncMock(), diff_override=diff, persist=False
        )

    assert result is sentinel
    storage.create_review_record.assert_not_called()
    # storage is forced to None internally → pipeline sees no persistence handle.
    assert pipe.await_args.args[3] is None


async def test_run_review_persist_true_creates_row():
    from pr_guardian.core import orchestrator
    from pr_guardian.models.pr import Platform, PlatformPR

    pr = PlatformPR(
        platform=Platform.GITHUB,
        pr_id="1",
        repo="o/r",
        repo_url="",
        source_branch="feat",
        target_branch="main",
        author="a",
        title="t",
        head_commit_sha="h" * 40,
    )
    diff = Diff(files=[DiffFile(path="a.py", status="modified", patch="+x")])
    storage = AsyncMock()

    with (
        patch.object(orchestrator, "_try_import_storage", return_value=storage),
        patch.object(
            orchestrator, "_run_pipeline", AsyncMock(return_value=_review_result(Decision.AUTO_APPROVE))
        ),
    ):
        await orchestrator.run_review(
            pr, AsyncMock(), diff_override=diff, skip_platform_side_effects=True, persist=True
        )

    storage.create_review_record.assert_called_once()
