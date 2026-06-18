"""Postgres advisory-lock leader election for app-local background loops.

The PR-sync and readiness-reconciler loops start on *every* replica (see
``main.py`` lifespan). On Container Apps that means up to ``maxReplicas`` copies
running the same work uncoordinated, multiplying GitHub/ADO API traffic and —
more painfully — the Postgres connection demand against a small flexible-server
``max_connections`` ceiling. A burst of that contention is what starves the tiny
SQLAlchemy pool right after a deploy.

This module gates those loops behind a session-level Postgres advisory lock so
only one replica (the "leader") runs each loop at a time. The lock is acquired
on a *dedicated* connection from a ``NullPool`` engine, so it never consumes a
slot in the request/work pool. Session-level locks are released automatically
when the connection drops, so a dead leader needs no TTL bookkeeping — the next
tick on another replica simply acquires the lock.

On non-Postgres backends (sqlite in dev/tests) there is only one process, so the
gate is a no-op: every caller is the leader.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog

log = structlog.get_logger()

# Stable, arbitrary advisory-lock keys. Postgres advisory locks are keyed on a
# single signed bigint; these constants are fixed and unrelated to any app id.
SYNC_LOCK_KEY = 0x7067_7561_7264_0001  # pr-sync loop
READINESS_LOCK_KEY = 0x7067_7561_7264_0002  # readiness reconciler loop

_lock_engine = None


def _is_postgres() -> bool:
    from pr_guardian.persistence.database import _get_database_url

    return _get_database_url().startswith("postgresql")


def _get_lock_engine():
    """Lazy dedicated engine for advisory locks (NullPool, autocommit)."""
    global _lock_engine
    if _lock_engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from pr_guardian.persistence.database import _get_database_url

        _lock_engine = create_async_engine(
            _get_database_url(),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={"timeout": 10},
        )
    return _lock_engine


@asynccontextmanager
async def leader_lock(key: int, *, label: str) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the advisory lock ``key``.

    The lock is held for the duration of the ``async with`` block and released
    on exit. Followers (that fail to acquire it) yield ``False`` and should skip
    their work this tick. On non-Postgres backends every caller is the leader.
    """
    if not _is_postgres():
        yield True
        return

    from sqlalchemy import text

    conn = await _get_lock_engine().connect()
    acquired = False
    try:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        acquired = bool(result.scalar())
        if not acquired:
            log.debug("leader_lock_not_acquired", lock=label)
        yield acquired
    finally:
        try:
            if acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        finally:
            await conn.close()


async def dispose_lock_engine() -> None:
    """Dispose the dedicated lock engine on shutdown."""
    global _lock_engine
    if _lock_engine is not None:
        await _lock_engine.dispose()
        _lock_engine = None
