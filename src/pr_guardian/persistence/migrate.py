"""Serialised, self-limiting startup migration step.

Replaces the raw ``alembic stamp``/``alembic upgrade`` sequence that
``docker-entrypoint.sh`` used to run inline. Two problems with running that
sequence directly:

1. **It re-stamped the baseline on every boot.** ``alembic stamp 001 --purge``
   ran whenever the ``reviews`` table existed — i.e. always, on an established
   database — rewinding ``alembic_version`` to the baseline so that the
   following ``upgrade head`` replayed every migration layered on top. That
   replay is harmless only because each of those migrations is hand-guarded to
   be idempotent; it still costs a full schema reflection per migration on every
   container start. Here the re-stamp is *conditional*: it happens only when the
   recorded revision is one this build does not know about (a pre-squash
   revision such as ``024``, or a lost version row), which is the only case it
   was ever meant to cover.

2. **Every replica ran it concurrently.** Container Apps starts all replicas at
   once and keeps the old revision alive through a rolling deploy, so N copies
   raced on ``alembic_version``. ``stamp --purge`` deletes the version row and
   re-inserts it, and ``alembic_version`` has a primary key on ``version_num``,
   so two replicas interleaving there can collide — under ``set -e`` that exits
   the container before the app ever starts, which reads as a crash loop.

So the whole step is serialised behind a Postgres advisory lock. Replicas that
lose the race wait; by the time they get in, the schema is already at head and
``upgrade head`` is a no-op. If the lock cannot be acquired within
:data:`_LOCK_WAIT_SECONDS` the step is *skipped* rather than failed: another
replica is demonstrably doing the work, and blocking or crash-looping the boot
is strictly worse than starting slightly early against a schema that
``init_db``'s ``create_all`` also converges.

Alembic and the column reconciler both call ``asyncio.run`` internally, so they
cannot be invoked in-process from here — they run as subprocesses while this
process holds the lock open on its own connection.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog

from pr_guardian.persistence.leader_lock import MIGRATION_LOCK_KEY

log = structlog.get_logger()

# How long to wait for another replica to finish migrating before giving up and
# booting anyway. Comfortably longer than a real migration run, short enough
# that a wedged holder cannot stall a deploy indefinitely.
_LOCK_WAIT_SECONDS = 120.0
_LOCK_POLL_SECONDS = 2.0

# Presence of this table is how an already-populated database is recognised.
_SCHEMA_SENTINEL_TABLE = "reviews"

_BASELINE_REVISION = "001"


class MigrationError(RuntimeError):
    """A migration subprocess exited non-zero."""


def needs_baseline_restamp(
    *,
    existing_schema: bool,
    current_revision: str | None,
    known_revisions: frozenset[str],
) -> bool:
    """Whether ``alembic_version`` must be purged and re-stamped at the baseline.

    Only true for an *existing* schema whose recorded revision this build cannot
    resolve — a pre-squash revision that no longer exists in
    ``alembic/versions``, or a missing/empty version row. A fresh database needs
    no stamp (``upgrade head`` writes the version itself), and a database already
    on a known revision must be left alone so the upgrade path runs forward
    instead of replaying from the baseline.
    """
    if not existing_schema:
        return False
    return current_revision is None or current_revision not in known_revisions


def known_revisions() -> frozenset[str]:
    """Revision ids present in this build's ``alembic/versions``."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini_path = _alembic_ini_path()
    config = Config(str(ini_path))
    # ``script_location`` in alembic.ini is relative, and Alembic resolves it
    # against the *process* CWD rather than the ini file. Anchor it to the ini's
    # directory so this works when invoked from somewhere other than the root.
    script_location = config.get_main_option("script_location") or "alembic"
    if not Path(script_location).is_absolute():
        config.set_main_option("script_location", str(ini_path.parent / script_location))
    return frozenset(
        script.revision for script in ScriptDirectory.from_config(config).walk_revisions()
    )


def _alembic_ini_path() -> Path:
    """Locate ``alembic.ini`` — the image WORKDIR, else the repo root."""
    cwd_candidate = Path.cwd() / "alembic.ini"
    if cwd_candidate.is_file():
        return cwd_candidate
    # src/pr_guardian/persistence/migrate.py -> repo root
    repo_candidate = Path(__file__).resolve().parents[3] / "alembic.ini"
    if repo_candidate.is_file():
        return repo_candidate
    raise MigrationError(
        f"alembic.ini not found (looked in {Path.cwd()} and {repo_candidate.parent})"
    )


async def _inspect_schema(conn) -> tuple[bool, str | None]:
    """Return ``(existing_schema, current_revision)`` for the connected database."""
    from sqlalchemy import text

    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :name AND table_schema = current_schema() LIMIT 1"
        ),
        {"name": _SCHEMA_SENTINEL_TABLE},
    )
    existing_schema = result.scalar() is not None

    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'alembic_version' AND table_schema = current_schema() LIMIT 1"
        )
    )
    if result.scalar() is None:
        return existing_schema, None

    result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    return existing_schema, result.scalar()


async def _run_step(*command: str) -> None:
    """Run a migration subprocess, raising :class:`MigrationError` on failure.

    Runs in the directory holding ``alembic.ini`` — the ``alembic`` CLI resolves
    both its config and its relative ``script_location`` against the CWD, so
    inheriting an arbitrary one would silently migrate nothing.
    """
    cwd = _alembic_ini_path().parent
    log.info("migration_step_start", command=" ".join(command), cwd=str(cwd))
    process = await asyncio.create_subprocess_exec(*command, cwd=str(cwd))
    returncode = await process.wait()
    if returncode != 0:
        raise MigrationError(f"`{' '.join(command)}` exited with {returncode}")


async def _acquire_with_wait(conn, key: int) -> bool:
    """Poll ``pg_try_advisory_lock`` until acquired or the wait budget runs out.

    Polling rather than a blocking ``pg_advisory_lock`` so the wait has a
    ceiling — a blocking acquire against a wedged holder would hang boot with no
    way out.
    """
    from sqlalchemy import text

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LOCK_WAIT_SECONDS
    while True:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        if bool(result.scalar()):
            return True
        if loop.time() >= deadline:
            return False
        log.info("migration_lock_busy", retry_in_seconds=_LOCK_POLL_SECONDS)
        await asyncio.sleep(_LOCK_POLL_SECONDS)


async def run_migrations() -> None:
    """Bring the database to head, serialised across replicas."""
    from pr_guardian.persistence.database import _get_database_url

    if not _get_database_url().startswith("postgresql"):
        # sqlite/dev has no advisory locks and no concurrent replicas.
        await _run_step("alembic", "upgrade", "head")
        await _run_step(sys.executable, "-m", "pr_guardian.persistence.reconcile")
        return

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    engine = create_async_engine(
        _get_database_url(),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        connect_args={"timeout": 10},
    )
    try:
        conn = await engine.connect()
        acquired = False
        try:
            acquired = await _acquire_with_wait(conn, MIGRATION_LOCK_KEY)
            if not acquired:
                log.warning(
                    "migration_lock_timeout_skipping",
                    waited_seconds=_LOCK_WAIT_SECONDS,
                    hint="another replica holds the migration lock; booting without migrating",
                )
                return

            existing_schema, current_revision = await _inspect_schema(conn)
            restamp = needs_baseline_restamp(
                existing_schema=existing_schema,
                current_revision=current_revision,
                known_revisions=known_revisions(),
            )
            log.info(
                "migration_plan",
                existing_schema=existing_schema,
                current_revision=current_revision,
                restamp_baseline=restamp,
            )
            if restamp:
                # --purge clears the unresolvable version row before stamping, so
                # this converges regardless of which removed revision it was at.
                await _run_step("alembic", "stamp", _BASELINE_REVISION, "--purge")

            await _run_step("alembic", "upgrade", "head")
            # Closes the gap where a stamped-but-behind database is missing model
            # columns that no migration will add. See ADR-010 and reconcile.py.
            await _run_step(sys.executable, "-m", "pr_guardian.persistence.reconcile")
            log.info("migrations_complete")
        finally:
            try:
                if acquired:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY}
                    )
            finally:
                await conn.close()
    finally:
        await engine.dispose()


def _db_enabled() -> bool:
    """Mirror the persistence gate in ``main.lifespan`` so boot stays consistent.

    Without this the entrypoint would migrate (or fail trying) against the
    default localhost URL in a deployment that the app itself then starts in
    no-DB mode.
    """
    if os.environ.get("DATABASE_URL"):
        return True
    return os.environ.get("GUARDIAN_DB_ENABLED", "").lower() in ("1", "true", "yes")


def main() -> None:
    if not _db_enabled():
        log.info("migrations_skipped_db_disabled")
        return
    asyncio.run(run_migrations())


if __name__ == "__main__":
    main()
