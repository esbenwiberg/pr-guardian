from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core import orchestrator
from pr_guardian.core.orchestrator import is_exempt_author
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Platform, PlatformPR


def _pr(author: str, target_branch: str = "main") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="https://github.com/org/repo",
        source_branch="dependabot/pip/requests-2.32.0",
        target_branch=target_branch,
        author=author,
        title="Bump requests",
        head_commit_sha="abc123",
        org="org",
    )


# --- predicate ---------------------------------------------------------------


def test_is_exempt_author_matches_default_dependabot():
    assert is_exempt_author("dependabot[bot]", GuardianConfig()) is True


def test_is_exempt_author_is_case_insensitive():
    assert is_exempt_author("Dependabot[Bot]", GuardianConfig()) is True


def test_is_exempt_author_regression_brackets_are_literal_not_glob():
    """fnmatch would treat '[bot]' as a character class — these single-char
    strings must NOT match the literal 'dependabot[bot]' allowlist entry."""
    cfg = GuardianConfig()
    assert is_exempt_author("dependabotb", cfg) is False
    assert is_exempt_author("dependaboto", cfg) is False
    assert is_exempt_author("dependabott", cfg) is False


def test_is_exempt_author_non_match_and_empty():
    cfg = GuardianConfig()
    assert is_exempt_author("a-human-dev", cfg) is False
    assert is_exempt_author("", cfg) is False


def test_is_exempt_author_empty_allowlist_is_opt_out():
    cfg = GuardianConfig()
    cfg.auto_approve.exempt_authors = []
    assert is_exempt_author("dependabot[bot]", cfg) is False


# --- short-circuit in run_review --------------------------------------------


@pytest.mark.asyncio
async def test_exempt_author_fast_autoapprove_skips_pipeline(monkeypatch):
    """An exempt author auto-approves without fetching the diff or running gates,
    and still fires side effects so the required Guardian check goes green."""
    monkeypatch.setattr(orchestrator, "_try_import_storage", lambda: None)
    posted: dict = {}

    async def _capture_post(adapter, pr, result, config, **kwargs):
        posted["result"] = result

    monkeypatch.setattr(orchestrator, "_post_results", _capture_post)
    monkeypatch.setattr(orchestrator, "run_mechanical_checks", AsyncMock())

    adapter = MagicMock()
    adapter.fetch_diff = AsyncMock()
    adapter.set_status = AsyncMock()
    adapter.post_comment = AsyncMock()

    result = await orchestrator.run_review(
        _pr("dependabot[bot]"),
        adapter,
        service_config=GuardianConfig(),
    )

    assert result.decision == Decision.AUTO_APPROVE
    adapter.fetch_diff.assert_not_awaited()
    orchestrator.run_mechanical_checks.assert_not_awaited()
    assert posted["result"].decision == Decision.AUTO_APPROVE


@pytest.mark.asyncio
async def test_exempt_author_into_blocked_branch_falls_through(monkeypatch):
    """Blocked target branches (release/*) keep their guard rail: an exempt
    author targeting one must NOT short-circuit — the pipeline runs (diff fetch
    is reached)."""
    monkeypatch.setattr(orchestrator, "_try_import_storage", lambda: None)

    adapter = MagicMock()
    adapter.fetch_diff = AsyncMock(side_effect=RuntimeError("reached-diff-fetch"))
    adapter.set_status = AsyncMock()
    adapter.post_comment = AsyncMock()

    with pytest.raises(RuntimeError, match="reached-diff-fetch"):
        await orchestrator.run_review(
            _pr("dependabot[bot]", target_branch="release/1.2"),
            adapter,
            service_config=GuardianConfig(),
        )

    adapter.fetch_diff.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_exempt_author_falls_through(monkeypatch):
    """A human author never short-circuits — the pipeline runs."""
    monkeypatch.setattr(orchestrator, "_try_import_storage", lambda: None)

    adapter = MagicMock()
    adapter.fetch_diff = AsyncMock(side_effect=RuntimeError("reached-diff-fetch"))
    adapter.set_status = AsyncMock()
    adapter.post_comment = AsyncMock()

    with pytest.raises(RuntimeError, match="reached-diff-fetch"):
        await orchestrator.run_review(
            _pr("a-human-dev"),
            adapter,
            service_config=GuardianConfig(),
        )

    adapter.fetch_diff.assert_awaited_once()
