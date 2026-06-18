"""Leader-election gating for the app-local background loops.

Covers the non-Postgres always-leader short-circuit and that each loop only
runs its work when it holds the lock. True advisory mutual-exclusion is a
Postgres behaviour (sqlite has no ``pg_advisory_lock``), so it is not exercised
here — the in-memory test backend always reports leader by design.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from pr_guardian.core.pr_sync import pr_sync_loop
from pr_guardian.core.readiness_reconciler import readiness_reconciler_loop
from pr_guardian.persistence.leader_lock import SYNC_LOCK_KEY, leader_lock


class _StopLoop(Exception):
    """Sentinel raised from a patched sleep to break the infinite loop after one tick."""


def _fake_lock(result: bool):
    @asynccontextmanager
    async def _cm(key, *, label):
        yield result

    return _cm


async def test_leader_lock_always_leader_on_non_postgres():
    # sqlite/dev/test is single-process: every caller is the leader and no
    # connection is attempted.
    with patch(
        "pr_guardian.persistence.database._get_database_url",
        return_value="sqlite+aiosqlite://",
    ):
        async with leader_lock(SYNC_LOCK_KEY, label="test") as is_leader:
            assert is_leader is True


async def test_pr_sync_loop_runs_when_leader():
    calls = []

    async def fake_run():
        calls.append(1)

    async def fake_sleep(_seconds):
        raise _StopLoop

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(True)),
        patch("pr_guardian.core.pr_sync.run_pr_sync", fake_run),
        patch("pr_guardian.core.pr_sync.asyncio.sleep", fake_sleep),
        pytest.raises(_StopLoop),
    ):
        await pr_sync_loop()

    assert calls == [1]


async def test_pr_sync_loop_skips_when_follower():
    calls = []

    async def fake_run():
        calls.append(1)

    async def fake_sleep(_seconds):
        raise _StopLoop

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(False)),
        patch("pr_guardian.core.pr_sync.run_pr_sync", fake_run),
        patch("pr_guardian.core.pr_sync.asyncio.sleep", fake_sleep),
        pytest.raises(_StopLoop),
    ):
        await pr_sync_loop()

    assert calls == []


async def test_readiness_loop_runs_when_leader():
    calls = []

    async def fake_reconcile():
        calls.append(1)

    async def fake_sleep(_seconds):
        raise _StopLoop

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(True)),
        patch("pr_guardian.core.readiness_reconciler.reconcile_readiness_once", fake_reconcile),
        patch("pr_guardian.core.readiness_reconciler.asyncio.sleep", fake_sleep),
        pytest.raises(_StopLoop),
    ):
        await readiness_reconciler_loop()

    assert calls == [1]


async def test_readiness_loop_skips_when_follower():
    calls = []

    async def fake_reconcile():
        calls.append(1)

    async def fake_sleep(_seconds):
        raise _StopLoop

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(False)),
        patch("pr_guardian.core.readiness_reconciler.reconcile_readiness_once", fake_reconcile),
        patch("pr_guardian.core.readiness_reconciler.asyncio.sleep", fake_sleep),
        pytest.raises(_StopLoop),
    ):
        await readiness_reconciler_loop()

    assert calls == []
