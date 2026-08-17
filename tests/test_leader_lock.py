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
    """Sentinel raised from a patched sleep to break out of the infinite loop."""


def _fake_lock(result: bool):
    @asynccontextmanager
    async def _cm(key, *, label):
        yield result

    return _cm


def _stop_after(sleeps: int):
    """Patched ``asyncio.sleep`` that allows ``sleeps`` calls, then breaks the loop.

    Both loops sleep *before* their first pass (see ``pr_sync_loop``), so letting
    exactly one sleep through runs exactly one tick. ``_stop_after(0)`` breaks
    before any work can happen, which is how the sleep-first ordering is asserted.
    """
    remaining = sleeps

    async def _sleep(_seconds):
        nonlocal remaining
        if remaining <= 0:
            raise _StopLoop
        remaining -= 1

    return _sleep


async def test_leader_lock_always_leader_on_non_postgres():
    # sqlite/dev/test is single-process: every caller is the leader and no
    # connection is attempted.
    with patch(
        "pr_guardian.persistence.database._get_database_url",
        return_value="sqlite+aiosqlite://",
    ):
        async with leader_lock(SYNC_LOCK_KEY, label="test") as is_leader:
            assert is_leader is True


@pytest.mark.parametrize(
    ("is_leader", "expected"),
    [(True, [1]), (False, [])],
    ids=["leader_runs", "follower_skips"],
)
async def test_pr_sync_loop_gated_on_leadership(is_leader, expected):
    calls = []

    async def fake_run():
        calls.append(1)

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(is_leader)),
        patch("pr_guardian.core.pr_sync.run_pr_sync", fake_run),
        patch("pr_guardian.core.pr_sync.asyncio.sleep", _stop_after(1)),
        pytest.raises(_StopLoop),
    ):
        await pr_sync_loop()

    assert calls == expected


@pytest.mark.parametrize(
    ("is_leader", "expected"),
    [(True, [1]), (False, [])],
    ids=["leader_runs", "follower_skips"],
)
async def test_readiness_loop_gated_on_leadership(is_leader, expected):
    calls = []

    async def fake_reconcile():
        calls.append(1)

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(is_leader)),
        patch("pr_guardian.core.readiness_reconciler.reconcile_readiness_once", fake_reconcile),
        patch("pr_guardian.core.readiness_reconciler.asyncio.sleep", _stop_after(1)),
        pytest.raises(_StopLoop),
    ):
        await readiness_reconciler_loop()

    assert calls == expected


async def test_pr_sync_loop_sleeps_before_first_pass():
    """A full sync pass must never run at boot.

    Running one immediately turned an OOM restart into a self-sustaining crash
    loop in production: OOM → restart → instant full sync of every project →
    OOM. The leader lock does not help, because it serialises *concurrent*
    passes and does nothing about restart-driven frequency. A replica that
    cannot survive one interval must never reach a pass.
    """
    calls = []

    async def fake_run():
        calls.append(1)

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(True)),
        patch("pr_guardian.core.pr_sync.run_pr_sync", fake_run),
        patch("pr_guardian.core.pr_sync.asyncio.sleep", _stop_after(0)),
        pytest.raises(_StopLoop),
    ):
        await pr_sync_loop()

    assert calls == []


async def test_readiness_loop_sleeps_before_first_pass():
    calls = []

    async def fake_reconcile():
        calls.append(1)

    with (
        patch("pr_guardian.persistence.leader_lock.leader_lock", _fake_lock(True)),
        patch("pr_guardian.core.readiness_reconciler.reconcile_readiness_once", fake_reconcile),
        patch("pr_guardian.core.readiness_reconciler.asyncio.sleep", _stop_after(0)),
        pytest.raises(_StopLoop),
    ):
        await readiness_reconciler_loop()

    assert calls == []
