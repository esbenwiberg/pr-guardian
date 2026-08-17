"""Startup migration step: when to re-stamp the baseline, and lock-wait behaviour.

The production failure these cover: the entrypoint used to run
``alembic stamp 001 --purge`` on every boot of every replica, rewinding
``alembic_version`` and replaying the whole chain, with N replicas racing on a
primary-keyed version row under ``set -e``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pr_guardian.persistence.migrate import (
    _LOCK_WAIT_SECONDS,
    _acquire_with_wait,
    known_revisions,
    needs_baseline_restamp,
)

_KNOWN = frozenset({"001", "002", "003"})


@pytest.mark.parametrize(
    ("existing_schema", "current_revision", "expected"),
    [
        # Fresh database: `upgrade head` writes the version itself.
        (False, None, False),
        # The bug: an established database already on a known revision must be
        # left alone, not rewound to the baseline on every container start.
        (True, "003", False),
        (True, "001", False),
        # A pre-squash revision this build cannot resolve — the only case the
        # purge-and-restamp was ever meant to cover.
        (True, "024", True),
        # Version row lost or table absent while the schema exists.
        (True, None, True),
    ],
    ids=[
        "fresh_db_no_stamp",
        "existing_at_head_no_stamp",
        "existing_at_baseline_no_stamp",
        "existing_at_removed_revision_restamps",
        "existing_without_version_row_restamps",
    ],
)
def test_needs_baseline_restamp(existing_schema, current_revision, expected):
    assert (
        needs_baseline_restamp(
            existing_schema=existing_schema,
            current_revision=current_revision,
            known_revisions=_KNOWN,
        )
        is expected
    )


def test_known_revisions_resolves_this_builds_chain():
    revisions = known_revisions()
    # The squashed baseline is always present; guards against a config path that
    # silently resolves to an empty chain, which would restamp on every boot.
    assert "001" in revisions
    assert needs_baseline_restamp(
        existing_schema=True, current_revision="024", known_revisions=revisions
    )


def test_known_revisions_is_cwd_independent(tmp_path, monkeypatch):
    """``script_location`` is relative in alembic.ini and Alembic resolves it
    against the process CWD, so an unanchored config finds no scripts at all."""
    monkeypatch.chdir(tmp_path)
    assert "001" in known_revisions()


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Connection whose ``pg_try_advisory_lock`` succeeds after N refusals."""

    def __init__(self, grant_on_attempt: int):
        self._grant_on_attempt = grant_on_attempt
        self.attempts = 0

    async def execute(self, _statement, _params=None):
        self.attempts += 1
        return _FakeResult(self.attempts >= self._grant_on_attempt)


async def test_acquire_with_wait_returns_true_once_granted():
    conn = _FakeConn(grant_on_attempt=3)
    with patch("pr_guardian.persistence.migrate.asyncio.sleep", AsyncMock()):
        assert await _acquire_with_wait(conn, 1234) is True
    assert conn.attempts == 3


async def test_acquire_with_wait_gives_up_instead_of_hanging_boot():
    """A wedged lock holder must not stall the deploy forever."""
    conn = _FakeConn(grant_on_attempt=10**9)  # never granted
    times = iter([0.0] + [float(_LOCK_WAIT_SECONDS)] * 10)

    class _Loop:
        def time(self):
            return next(times)

    with (
        patch("pr_guardian.persistence.migrate.asyncio.get_running_loop", lambda: _Loop()),
        patch("pr_guardian.persistence.migrate.asyncio.sleep", AsyncMock()),
    ):
        assert await _acquire_with_wait(conn, 1234) is False
