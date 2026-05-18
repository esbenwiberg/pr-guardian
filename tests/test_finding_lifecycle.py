"""Round-trip tests for finding lifecycle storage helpers."""
from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_guardian.persistence.storage import (
    FindingState,
    get_finding_states,
    infer_fixes,
    mark_fixed,
    mark_regressed,
    mark_verified,
    verify_sticky_trigger,
)

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
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_):
        return False


def _failing_session(msg: str = "DB down"):
    exc = RuntimeError(msg)
    return lambda: _FailingCM(exc)


def _make_row(
    *,
    pr_id: str = "pr-1",
    signature: str = "sig-abc",
    status: str = "acknowledged",
    resolution_kind: str | None = None,
    fixed_by_sha: str | None = None,
    fixed_at: datetime | None = None,
    verified_by: str | None = None,
    verified_at: datetime | None = None,
    regressed_at: datetime | None = None,
    regressed_from_sha: str | None = None,
):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.pr_id = pr_id
    row.repo = ""
    row.platform = ""
    row.signature = signature
    row.status = status
    row.resolution_kind = resolution_kind
    row.fixed_by_sha = fixed_by_sha
    row.fixed_at = fixed_at
    row.verified_by = verified_by
    row.verified_at = verified_at
    row.regressed_at = regressed_at
    row.regressed_from_sha = regressed_from_sha
    row.updated_at = datetime.now(timezone.utc)
    return row


def _mock_session_for_rows(rows=None, first=None):
    """Build a mock session whose scalars() supports both .all() and .first()."""
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = rows if rows is not None else []
    scalars_result.first.return_value = first
    session.scalars = AsyncMock(return_value=scalars_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# mark_fixed
# ---------------------------------------------------------------------------


async def test_mark_fixed_creates_row_with_fixed_state():
    """mark_fixed when no row exists creates a FIXED row with fixed_by_sha populated."""
    captured = None

    session = _mock_session_for_rows(first=None)

    def _cap(row):
        nonlocal captured
        captured = row

    session.add = MagicMock(side_effect=_cap)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_fixed("pr-1", "sig-abc", "sha-111")

    assert captured is not None
    assert captured.resolution_kind == FindingState.FIXED
    assert captured.fixed_by_sha == "sha-111"
    assert captured.fixed_at is not None


async def test_mark_fixed_updates_existing_row():
    """mark_fixed on an existing row updates its resolution_kind and fixed_by_sha."""
    row = _make_row(resolution_kind=None)
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_fixed("pr-1", "sig-abc", "sha-222")

    assert row.resolution_kind == FindingState.FIXED
    assert row.fixed_by_sha == "sha-222"


async def test_mark_fixed_noop_when_verified():
    """mark_fixed on a VERIFIED row is a silent no-op."""
    row = _make_row(resolution_kind=FindingState.VERIFIED)
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_fixed("pr-1", "sig-abc", "sha-333")

    assert row.resolution_kind == FindingState.VERIFIED
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# mark_regressed
# ---------------------------------------------------------------------------


async def test_mark_regressed_after_fixed():
    """mark_regressed sets REGRESSED state and records regressed_from_sha."""
    row = _make_row(resolution_kind=FindingState.FIXED, fixed_by_sha="sha-a")
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_regressed("pr-1", "sig-abc", "sha-b", "sha-a")

    assert row.resolution_kind == FindingState.REGRESSED
    assert row.regressed_from_sha == "sha-a"
    assert row.regressed_at is not None


async def test_mark_regressed_noop_when_verified():
    """mark_regressed on a VERIFIED row is a silent no-op."""
    row = _make_row(resolution_kind=FindingState.VERIFIED)
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_regressed("pr-1", "sig-abc", "sha-b", "sha-a")

    assert row.resolution_kind == FindingState.VERIFIED
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# mark_verified (terminal)
# ---------------------------------------------------------------------------


async def test_mark_verified_sets_verified_state():
    """mark_verified transitions any non-terminal row to VERIFIED."""
    row = _make_row(resolution_kind=FindingState.FIXED)
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_verified("pr-1", "sig-abc", "alice@example.com")

    assert row.resolution_kind == FindingState.VERIFIED
    assert row.verified_by == "alice@example.com"
    assert row.verified_at is not None


async def test_mark_verified_is_terminal():
    """mark_fixed after mark_verified leaves state as VERIFIED (terminal)."""
    row = _make_row(resolution_kind=FindingState.VERIFIED, verified_by="alice@example.com")
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await mark_fixed("pr-1", "sig-abc", "sha-new")

    assert row.resolution_kind == FindingState.VERIFIED
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_finding_states
# ---------------------------------------------------------------------------


async def test_get_finding_states_aggregation():
    """Mix of states across multiple signatures returns the correct dict."""
    rows = [
        _make_row(signature="sig-fixed", resolution_kind=FindingState.FIXED, fixed_by_sha="sha-1"),
        _make_row(signature="sig-regressed", resolution_kind=FindingState.REGRESSED),
        _make_row(signature="sig-verified", resolution_kind=FindingState.VERIFIED),
        _make_row(signature="sig-dismissed", resolution_kind=None, status="by_design"),
    ]
    session = _mock_session_for_rows(rows=rows)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        states = await get_finding_states("pr-1")

    assert states["sig-fixed"] == FindingState.FIXED
    assert states["sig-regressed"] == FindingState.REGRESSED
    assert states["sig-verified"] == FindingState.VERIFIED
    assert states["sig-dismissed"] == FindingState.DISMISSED


async def test_get_finding_states_db_failure_returns_empty():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        result = await get_finding_states("pr-1")
    assert result == {}


# ---------------------------------------------------------------------------
# infer_fixes — set math
# ---------------------------------------------------------------------------


async def test_infer_fixes_newly_fixed():
    """prev={a,b,c}, current={a}, no previously fixed → fixed={b,c}, regressed={}."""
    session = _mock_session_for_rows(rows=[], first=None)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b", "c"}, {"a"}, "sha-new")

    assert fixed == {"b", "c"}
    assert regressed == set()


async def test_infer_fixes_detects_regression():
    """previously_fixed={d}, current contains d → regressed={d}."""
    row_d = _make_row(signature="d", resolution_kind=FindingState.FIXED, fixed_by_sha="sha-old")
    session = _mock_session_for_rows(rows=[row_d], first=row_d)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        fixed, regressed = await infer_fixes("pr-1", {"a"}, {"a", "d"}, "sha-new")

    assert "d" in regressed
    assert fixed == set()


async def test_infer_fixes_verified_sigs_excluded_from_fixed():
    """Verified signatures are not re-marked as fixed."""
    row_v = _make_row(signature="b", resolution_kind=FindingState.VERIFIED)
    session = _mock_session_for_rows(rows=[row_v], first=row_v)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b"}, {"a"}, "sha-new")

    # b is gone from current but already verified — must not appear in fixed
    assert "b" not in fixed
    assert regressed == set()


async def test_infer_fixes_db_failure_returns_empty_sets():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        fixed, regressed = await infer_fixes("pr-1", {"a", "b"}, {"a"}, "sha-new")
    assert fixed == set()
    assert regressed == set()


# ---------------------------------------------------------------------------
# verify_sticky_trigger
# ---------------------------------------------------------------------------


async def test_verify_sticky_trigger_writes_synthetic_row():
    """verify_sticky_trigger creates a VERIFIED row with the synthetic signature."""
    captured = None
    session = _mock_session_for_rows(first=None)

    def _cap(row):
        nonlocal captured
        captured = row

    session.add = MagicMock(side_effect=_cap)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await verify_sticky_trigger("pr-1", "new_dep", "requests==2.32.3", "alice@example.com")

    assert captured is not None
    assert captured.resolution_kind == FindingState.VERIFIED
    assert captured.verified_by == "alice@example.com"

    # Verify synthetic signature matches ADR-004 formula
    raw = "pr-1::new_dep::requests==2.32.3"
    expected_sig = hashlib.sha256(raw.encode()).hexdigest()[:16]
    assert captured.signature == expected_sig


async def test_verify_sticky_trigger_is_idempotent():
    """Posting the same trigger twice is a no-op success."""
    raw = "pr-1::new_dep::requests==2.32.3"
    sig = hashlib.sha256(raw.encode()).hexdigest()[:16]

    row = _make_row(signature=sig, resolution_kind=FindingState.VERIFIED, verified_by="alice@example.com")
    session = _mock_session_for_rows(first=row)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await verify_sticky_trigger("pr-1", "new_dep", "requests==2.32.3", "alice@example.com")

    assert row.resolution_kind == FindingState.VERIFIED
    session.commit.assert_not_awaited()


async def test_verify_sticky_trigger_db_failure_is_silent():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        await verify_sticky_trigger("pr-1", "new_dep", "requests==2.32.3", "alice@example.com")


# ---------------------------------------------------------------------------
# No-DB mode: all helpers return safe defaults, never raise
# ---------------------------------------------------------------------------


async def test_mark_fixed_db_failure_no_raise():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        await mark_fixed("pr-1", "sig", "sha")


async def test_mark_regressed_db_failure_no_raise():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        await mark_regressed("pr-1", "sig", "sha-new", "sha-old")


async def test_mark_verified_db_failure_no_raise():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        await mark_verified("pr-1", "sig", "user@example.com")
