#!/bin/bash
set -e

echo "Running database migrations..."

# The migration chain was squashed to a single baseline (revision 001) generated
# from the SQLAlchemy models — the schema source of truth (the app also runs
# create_all on startup). Two cases:
#
#   (a) Existing database: its schema already matches the models, but its
#       alembic_version points at an old, now-removed revision (e.g. 024) or at
#       the baseline. Re-stamp it to the baseline (no DDL is run, so existing
#       data/schema are untouched) and let `upgrade head` apply any future
#       migrations layered on top.
#   (b) Fresh empty database: `upgrade head` runs the baseline from scratch.
#
# Detect an existing database by the presence of the 'reviews' table.
if python -c "
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    row = await conn.fetchrow(
        \"SELECT 1 FROM information_schema.tables WHERE table_name='reviews' LIMIT 1\"
    )
    await conn.close()
    exit(0 if row else 1)
asyncio.run(main())
" 2>/dev/null; then
    echo "Existing schema detected (reviews table present) — re-stamping to baseline 001..."
    # --purge clears any stale version row (e.g. a now-removed 024) before stamping,
    # so this works regardless of which old revision the DB was last at.
    alembic stamp 001 --purge
else
    echo "Fresh database — running baseline migration from scratch..."
fi

alembic upgrade head

# Reconcile any model columns missing from existing tables. The baseline stamp
# above marks the DB as current without applying columns from migrations a
# behind database never ran; create_all (on app startup) only adds missing
# tables, not columns. This closes that gap idempotently (no-op on a correct
# schema). See ADR-010 and src/pr_guardian/persistence/reconcile.py.
echo "Reconciling schema columns..."
python -m pr_guardian.persistence.reconcile

echo "Migrations complete."

exec "$@"
