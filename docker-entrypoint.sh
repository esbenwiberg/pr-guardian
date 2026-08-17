#!/bin/bash
set -e

# Migrations run through pr_guardian.persistence.migrate rather than bare alembic
# calls. That module serialises the whole step behind a Postgres advisory lock
# (Container Apps starts every replica at once, and the old revision stays alive
# through a rolling deploy, so N copies used to race on alembic_version) and only
# re-stamps the squashed baseline when the recorded revision is one this build
# cannot resolve, instead of on every single boot. See its docstring and ADR-010.
echo "Running database migrations..."
python -m pr_guardian.persistence.migrate
echo "Migrations complete."

exec "$@"
