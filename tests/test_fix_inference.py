"""Tests for fix-inference: fix detection, regression detection, no-DB graceful path."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_guardian.persistence.storage import FindingState, infer_fixes

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_cm(session):
    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


class _FailingCM:
    async def __aenter__(self):
        raise RuntimeError("DB down")

    async def __aexit__(self, *_):
        return False


def _failing_session():
    return lambda: _FailingCM()


def _make_row(*, signature, resolution_kind=None, fixed_by_sha=None):
    row = MagicMock()
    row.signature = signature
    row.resolution_kind = resolution_kind
    row.fixed_by_sha = fixed_by_sha
    row.regressed_from_sha = None
    row.updated_at = datetime.now(timezone.utc)
    return row


def _session_for(rows):
    session = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    scalars.first.return_value = rows[0] if rows else None
    session.scalars = AsyncMock(return_value=scalars)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run1_to_run2_detects_fixed():
    """3 findings on run 1, 1 finding on run 2 → 2 land in fixed."""
    session = _session_for([])

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b", "c"}, {"a"}, "sha-run2")

    assert fixed == {"b", "c"}
    assert regressed == set()


async def test_run3_detects_regression_with_regressed_from_sha():
    """Sigs fixed on run-2 that reappear on run-3 → regressed, regressed_from_sha=sha-run2."""
    row_b = _make_row(signature="b", resolution_kind=FindingState.FIXED, fixed_by_sha="sha-run2")
    row_c = _make_row(signature="c", resolution_kind=FindingState.FIXED, fixed_by_sha="sha-run2")
    session = _session_for([row_b, row_c])

    captured: list[tuple[str, str]] = []

    async def _capture(pr_id, sig, sha, prev_sha):
        captured.append((sig, prev_sha))

    with (
        patch("pr_guardian.persistence.storage.async_session", _session_cm(session)),
        patch("pr_guardian.persistence.storage.mark_regressed", _capture),
    ):
        fixed, regressed = await infer_fixes("pr-1", {"a"}, {"a", "b", "c"}, "sha-run3")

    assert regressed == {"b", "c"}
    assert fixed == set()
    from_sha_map = dict(captured)
    assert from_sha_map["b"] == "sha-run2"
    assert from_sha_map["c"] == "sha-run2"


async def test_no_findings_disappear_returns_empty():
    """When current_sigs == prev_sigs, both fixed and regressed are empty."""
    session = _session_for([])

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b", "c"}, {"a", "b", "c"}, "sha-1")

    assert fixed == set()
    assert regressed == set()


async def test_no_db_returns_empty_sets_and_does_not_raise():
    """No-DB mode: infer_fixes returns empty sets and does not raise."""
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b"}, {"a"}, "sha-1")

    assert fixed == set()
    assert regressed == set()
