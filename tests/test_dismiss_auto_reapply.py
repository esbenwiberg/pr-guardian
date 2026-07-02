"""Tests for the dismiss-auto-reapply feature.

After a human replies with @guardian dismiss <status>: <reason>, the PR should
be re-scored immediately (no manual @guardian re-review) without calling any
LLM agents. These facts prove the four contract scenarios.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.orchestrator import re_decide_after_dismissal
from pr_guardian.decision.engine import combined_score
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import models, storage
from pr_guardian.persistence.storage import upsert_dismissal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pr(*, head_sha: str = "live-abc123") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="https://github.com/org/repo",
        source_branch="feat/x",
        target_branch="main",
        author="author",
        title="test PR",
        head_commit_sha=head_sha,
    )


def _make_review(
    findings_by_agent: dict[str, list[dict]],
    *,
    head_sha: str = "stored-old-sha",
    risk_tier: str = "medium",
    repo_risk_class: str = "standard",
) -> dict:
    """Build a minimal review dict like storage.get_review returns."""
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "head_commit_sha": head_sha,
        "comment_mode": "summary",
        "risk_tier": risk_tier,
        "repo_risk_class": repo_risk_class,
        "trust_tier": None,
        "target_branch": "main",
        "auto_approve_unlocked": False,
        "sticky_triggers": [],
        "agent_results": [
            {
                "agent_name": agent_name,
                "verdict": "warn",
                "languages_reviewed": [],
                "error": None,
                "verdict_explanation": "",
                "findings": findings,
            }
            for agent_name, findings in findings_by_agent.items()
        ],
        "profile_id": None,
        "profile_snapshot": None,
        "connection_id": None,
        "connection_snapshot": None,
        "repo_link_id": None,
        "candidate_id": None,
    }


def _low_finding(file: str = "a.py", category: str = "test_coverage") -> dict:
    return {
        "severity": "low",
        "certainty": "detected",
        "category": category,
        "language": "python",
        "file": file,
        "line": 1,
        "description": "Missing tests",
        "suggestion": "",
        "cwe": None,
    }


def _medium_finding(file: str = "b.py", category: str = "code_quality", line: int = 10) -> dict:
    return {
        "severity": "medium",
        "certainty": "detected",
        "category": category,
        "language": "python",
        "file": file,
        "line": line,
        "description": "Code quality issue",
        "suggestion": "",
        "cwe": None,
    }


class _FakeAdapter:
    """Minimal adapter that records status calls and provides a configurable live PR."""

    def __init__(self, live_sha: str = "live-abc123") -> None:
        self._live_sha = live_sha
        self.status_calls: list[tuple[PlatformPR, str, str]] = []

    async def fetch_pr(self, repo: str, pr_id: str | int) -> PlatformPR:
        return _make_pr(head_sha=self._live_sha)

    def set_review_status(
        self, pr: PlatformPR, state: str, description: str, target_url: str = ""
    ) -> None:
        self.status_calls.append((pr, state, description))

    async def post_comment(self, pr: PlatformPR, body: str) -> None:
        pass

    async def add_label(self, pr: PlatformPR, label: str) -> None:
        pass

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory SQLite DB with the Guardian schema, per-test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
        yield storage
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_flip_to_approve(db):
    """flip-to-approve: dismiss the sole finding → re-decide yields AUTO_APPROVE."""
    agent_name = "test_quality"
    finding = _low_finding()
    review = _make_review({agent_name: [finding]})
    pr = _make_pr()
    adapter = _FakeAdapter()

    # Record the dismissal so re_decide_after_dismissal picks it up.
    await upsert_dismissal(
        pr_id="42",
        repo="org/repo",
        platform="github",
        finding=finding,
        agent_name=agent_name,
        status="false_positive",
        comment="not a real issue",
    )

    result = await re_decide_after_dismissal(
        pr,
        adapter,
        review=review,
        storage=db,
        config=GuardianConfig(),
    )

    assert result.decision == Decision.AUTO_APPROVE


async def test_partial_dismiss_lowers_score(db):
    """partial-dismiss-lowers-score: dismissing one of two findings lowers the score.

    Uses HIGH + LOW findings in the same agent so the peak-based agent_score
    changes when the HIGH finding is dismissed.  The review is tagged HIGH /
    ELEVATED so both the two-finding and one-finding decisions are HUMAN_REVIEW,
    proving the score differentiation without a decision flip.
    """
    agent_name = "performance"
    # HIGH finding: score contribution = 6 × 0.5 (SUSPECTED after validation) = 3.0
    f_high = {
        "severity": "high",
        "certainty": "detected",
        "category": "memory_leak",
        "language": "python",
        "file": "hot.py",
        "line": 10,
        "description": "Hot path allocation",
        "suggestion": "",
        "cwe": None,
    }
    # LOW finding: score contribution = 1 × 0.5 = 0.5
    f_low = {
        "severity": "low",
        "certainty": "detected",
        "category": "minor_perf",
        "language": "python",
        "file": "cold.py",
        "line": 20,
        "description": "Minor allocation",
        "suggestion": "",
        "cwe": None,
    }

    review = _make_review(
        {agent_name: [f_high, f_low]},
        risk_tier="high",
        repo_risk_class="elevated",
    )
    pr = _make_pr()
    adapter = _FakeAdapter()

    # Pre-dismiss score: both HIGH + LOW in the performance agent.
    # agent_score = max(avg=(3.0+0.5)/2, peak=3.0) = 3.0
    pre_score = combined_score(
        [
            AgentResult(
                agent_name=agent_name,
                verdict=Verdict.FLAG_HUMAN,
                findings=[
                    Finding(
                        severity=Severity.HIGH,
                        certainty=Certainty.DETECTED,
                        category=f_high["category"],
                        language="python",
                        file=f_high["file"],
                        line=f_high["line"],
                        description=f_high["description"],
                    ),
                    Finding(
                        severity=Severity.LOW,
                        certainty=Certainty.DETECTED,
                        category=f_low["category"],
                        language="python",
                        file=f_low["file"],
                        line=f_low["line"],
                        description=f_low["description"],
                    ),
                ],
            )
        ],
        GuardianConfig(),
    )
    assert pre_score > 0.0, "pre-dismiss score must be positive"

    # Dismiss the HIGH finding — only the LOW one survives.
    await upsert_dismissal(
        pr_id="42",
        repo="org/repo",
        platform="github",
        finding=f_high,
        agent_name=agent_name,
        status="by_design",
        comment="accepted risk",
    )

    result = await re_decide_after_dismissal(
        pr,
        adapter,
        review=review,
        storage=db,
        config=GuardianConfig(),
    )

    # The LOW finding alone still triggers HUMAN_REVIEW under HIGH/ELEVATED.
    assert result.decision != Decision.AUTO_APPROVE
    # But the combined score must be strictly lower than with both findings.
    assert result.combined_score < pre_score


async def test_re_decide_skips_llm(db):
    """re-decide-skips-llm: no agent re_evaluate or LLM provider call is made."""
    agent_name = "code_quality_obs"
    finding = _low_finding()
    review = _make_review({agent_name: [finding]})
    pr = _make_pr()
    adapter = _FakeAdapter()

    await upsert_dismissal(
        pr_id="42",
        repo="org/repo",
        platform="github",
        finding=finding,
        agent_name=agent_name,
        status="acknowledged",
        comment="accepted",
    )

    # Patch AGENT_REGISTRY.get to detect if any agent class is looked up.
    registry_accessed: list[str] = []

    class _SpyRegistry(dict):
        def get(self, key, default=None):  # type: ignore[override]
            registry_accessed.append(key)
            return default

    with patch("pr_guardian.core.orchestrator.AGENT_REGISTRY", _SpyRegistry()):
        result = await re_decide_after_dismissal(
            pr,
            adapter,
            review=review,
            storage=db,
            config=GuardianConfig(),
        )

    # Re-decide must complete without touching the agent registry.
    assert registry_accessed == [], (
        f"re_decide_after_dismissal accessed AGENT_REGISTRY for: {registry_accessed}"
    )
    assert result.decision == Decision.AUTO_APPROVE


async def test_status_targets_live_head(db):
    """status-targets-live-head: status is posted to the live SHA, not the stored one."""
    stored_sha = "stored-old-deadbeef"
    live_sha = "live-new-cafebabe"

    agent_name = "hotspot"
    finding = _low_finding()
    # review carries the OLD stored sha
    review = _make_review({agent_name: [finding]}, head_sha=stored_sha)
    # adapter.fetch_pr returns the LIVE sha
    adapter = _FakeAdapter(live_sha=live_sha)
    pr = _make_pr(head_sha=live_sha)

    await upsert_dismissal(
        pr_id="42",
        repo="org/repo",
        platform="github",
        finding=finding,
        agent_name=agent_name,
        status="false_positive",
        comment="not real",
    )

    result = await re_decide_after_dismissal(
        pr,
        adapter,
        review=review,
        storage=db,
        config=GuardianConfig(),
    )

    # At least one status call must have been made to the live SHA.
    assert adapter.status_calls, "expected at least one status call"
    called_shas = {call_pr.head_commit_sha for (call_pr, _state, _desc) in adapter.status_calls}
    assert live_sha in called_shas, f"status was not posted to the live SHA; saw: {called_shas}"
    assert stored_sha not in called_shas, "status was incorrectly posted to the stored (stale) SHA"
    assert result.decision == Decision.AUTO_APPROVE
