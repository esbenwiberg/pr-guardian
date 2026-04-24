#!/usr/bin/env bash
# Agent/validator startup: Postgres + demo data + uvicorn.
#
# Intended for code-factory sandbox validation only. The profile's Health Path
# (/api/health) gates the validator on readiness — this script execs uvicorn
# in the foreground and does not run its own health loop.
#
# Assumes:
#  - Debian/Ubuntu-based image with the 'postgresql' package installed
#    (see Dockerfile.agent)
#  - Running as root (autopod containers do)
#  - Package already installed (pip install -e .[dev])
set -euo pipefail

: "${PG_USER:=guardian}"
: "${PG_PASSWORD:=guardian}"
: "${PG_DB:=pr_guardian}"
: "${PG_PORT:=5432}"
: "${APP_HOST:=0.0.0.0}"
# Use $PORT if set by the harness, fall back to 8000
: "${APP_PORT:=${PORT:-8000}}"

log() { printf '[agent-serve] %s\n' "$*"; }

export GUARDIAN_DEV_ADMIN=1

# ---------------------------------------------------------------------------
# 1. Start Postgres (idempotent) — skip gracefully if not installed
# ---------------------------------------------------------------------------
_pg_available=0
if command -v pg_isready >/dev/null 2>&1; then
    if ! pg_isready -h localhost -p "$PG_PORT" >/dev/null 2>&1; then
        log "starting postgres"
        if command -v pg_ctlcluster >/dev/null 2>&1; then
            PG_VER="$(ls /etc/postgresql/ 2>/dev/null | head -n1 || true)"
            if [[ -n "${PG_VER}" ]]; then
                pg_ctlcluster "${PG_VER}" main start || true
            else
                service postgresql start || true
            fi
        else
            service postgresql start || true
        fi

        # Wait up to ~15s for pg to be ready
        for _ in $(seq 1 30); do
            pg_isready -h localhost -p "$PG_PORT" >/dev/null 2>&1 && break
            sleep 0.5
        done
    fi

    if pg_isready -h localhost -p "$PG_PORT" >/dev/null 2>&1; then
        _pg_available=1
        log "postgres ready on :${PG_PORT}"
    else
        log "postgres not reachable — starting in no-DB mode"
    fi
else
    log "pg_isready not found — starting in no-DB mode"
fi

if [[ "${_pg_available}" -eq 1 ]]; then
    # ---------------------------------------------------------------------------
    # 2. Ensure role + database exist (idempotent)
    # ---------------------------------------------------------------------------
    as_pg() { su - postgres -c "$1"; }

    if ! as_pg "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${PG_USER}'\"" | grep -q 1; then
        log "creating role ${PG_USER}"
        as_pg "psql -c \"CREATE ROLE ${PG_USER} WITH LOGIN PASSWORD '${PG_PASSWORD}' CREATEDB SUPERUSER;\""
    fi

    if ! as_pg "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${PG_DB}'\"" | grep -q 1; then
        log "creating database ${PG_DB}"
        as_pg "createdb -O ${PG_USER} ${PG_DB}"
    fi

    export DATABASE_URL="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@localhost:${PG_PORT}/${PG_DB}"

    # ---------------------------------------------------------------------------
    # 3. Nuke & seed demo data
    # ---------------------------------------------------------------------------
    log "seeding demo data"
    python "$(dirname "$0")/dev_seed.py"
fi

# ---------------------------------------------------------------------------
# 4. Hand off to uvicorn (foreground; harness Health Path gates readiness)
# ---------------------------------------------------------------------------
log "starting uvicorn on ${APP_HOST}:${APP_PORT}"
exec python -m uvicorn pr_guardian.main:app --host "${APP_HOST}" --port "${APP_PORT}"
