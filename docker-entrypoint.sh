#!/bin/bash
set -e

echo "Running database migrations..."

# If no Alembic version is tracked yet, the DB was bootstrapped with
# SQLAlchemy create_all.  Stamp it at 003 (the last migration already
# reflected in the schema) so only 004+ are applied.
if ! alembic current 2>/dev/null | grep -q '[0-9]'; then
    echo "No Alembic version found — stamping existing schema at 003..."
    alembic stamp 003
fi

alembic upgrade head
echo "Migrations complete."

exec "$@"
