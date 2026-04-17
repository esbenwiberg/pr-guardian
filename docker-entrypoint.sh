#!/bin/bash
set -e

echo "Running database migrations..."

# If no Alembic version is tracked yet, the DB was either:
#   (a) bootstrapped with SQLAlchemy create_all before Alembic was introduced
#       → stamp at 003 so only 004+ are applied
#   (b) a fresh empty database (e.g. new Azure deployment)
#       → run all migrations from scratch
# Distinguish the two cases by checking whether the 'reviews' table exists.
if ! alembic current 2>/dev/null | grep -q '[0-9]'; then
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
        echo "Legacy schema detected (reviews table exists) — stamping at 003..."
        alembic stamp 003
    else
        echo "Fresh database — running all migrations from scratch..."
    fi
fi

alembic upgrade head
echo "Migrations complete."

exec "$@"
