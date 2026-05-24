# PR Guardian

Automated PR review pipeline — auto-approves low-risk PRs, escalates the rest.

## Commands

- Build: `pip install -e ".[dev]"`
- Test: `python -m pytest`
- Start: `bash scripts/agent-serve.sh`
- Health check: `/api/health`

## Key Notes

- App must listen on `$PORT` (defaults to 8000 if not set)
- PostgreSQL is optional; app degrades gracefully to no-DB mode
- `GUARDIAN_DEV_ADMIN=1` enables dev admin access without authentication
- The `scripts/agent-serve.sh` startup script seeds demo data when Postgres is available
