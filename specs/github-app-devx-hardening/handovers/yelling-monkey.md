# Handover: Brief 01 — GitHub App Connection Data Model

**Pod:** yelling-monkey  
**Branch:** autopod/yelling-monkey  
**Date:** 2026-06-06

## What was built

Added a GitHub App Connection data model to replace PAT-only GitHub storage:

- **Migration 022** (`alembic/versions/022_add_github_app_connection_fields.py`): 9 new
  nullable columns on `connections` — `auth_kind`, `app_id`, `app_slug`, `installation_id`,
  `installation_account`, `installation_target_type`, `encrypted_private_key`,
  `private_key_fingerprint`, `app_permissions`.

- **`ConnectionRow`** (`src/pr_guardian/persistence/models.py`): new fields added; existing
  PAT rows have `auth_kind=NULL`, new App rows have `auth_kind='github_app'`.

- **Storage layer** (`src/pr_guardian/persistence/storage.py`):
  - `_connection_to_dict` emits new fields (excluding `encrypted_private_key`).
  - `_secretish_key` extended to redact `private_key` keys in audit diffs.
  - `_private_key_fingerprint(pem)` computes `sha256:<hex>` fingerprint.
  - `create_connection` and `update_connection` accept all GitHub App parameters.
  - `get_connection_private_key(id)` decrypts and returns the PEM private key.
  - Audit markers: `private_key_secret` (set/changed) — no raw PEM material.

- **API layer** (`src/pr_guardian/api/profiles.py`):
  - `ConnectionPayload` for GitHub now requires `app_id` + `private_key`; rejects `token`.
  - `ConnectionPayload` for ADO still requires `token` + `org_url`.
  - `ConnectionUpdatePayload` accepts `private_key` and all App metadata fields.
  - `env_imports` endpoint no longer returns `GITHUB_TOKEN`.
  - `create_connection` endpoint: GitHub App connections skip `_probe_connection` and are
    stored with `health_status='unknown'` (Brief 02 wires the auth adapter).
  - `validate_connection` endpoint: returns current state for GitHub App connections
    without probing (Brief 02 must add installation token validation).

## Interfaces and contracts Brief 02 must know about

### `storage.get_connection_private_key(connection_id: UUID) -> str`
Returns the decrypted PEM private key. Returns `""` if absent or decryption fails.
Brief 02 calls this to mint installation tokens.

### `auth_kind` field on connections
`auth_kind == 'github_app'` identifies a GitHub App connection. Brief 02 should check
this before attempting installation token auth (ADO and legacy rows will have `auth_kind=None`).

### `validate_connection` endpoint short-circuits for GitHub App
`POST /api/profiles/connections/{id}/validate` returns current state (no probe) when
`auth_kind == 'github_app'`. Brief 02 should extend this to call its installation auth
adapter and update `health_status` + `health_checked_at`.

### Health state starts as `"unknown"` for GitHub App connections
Unlike ADO (which probes immediately on create), GitHub App connections are saved
as `health_status='unknown'`. Brief 02's validation sets them to `"healthy"` or
`"unhealthy"` after a real installation token exchange.

## Files Brief 02 should NOT modify without good reason

- `alembic/versions/022_add_github_app_connection_fields.py` — append-only migration
- `src/pr_guardian/persistence/models.py` lines 447–485 — the 9 new `ConnectionRow` fields
- `src/pr_guardian/persistence/crypto.py` — unchanged, reused as-is
- `tests/test_github_app_connection_storage.py` — fact test for this brief

## Discovered constraints / landmines

1. **`test_readiness_storage.py` and `test_profiles_storage.py` have hand-rolled `_meta`
   tables.** When adding columns to `ConnectionRow`, you MUST also add those columns to the
   `connections` `sa.Table(...)` definitions in both test files, or tests that use those
   custom session factories will fail with `OperationalError: table connections has no column`.

2. **`_probe_connection` is not called for GitHub App connections.** The `create_connection`
   and `update_connection` API endpoints intentionally skip the PAT-based probe for
   `auth_kind='github_app'`. Brief 02 must add its own validation path.

3. **Existing tests that created GitHub connections via the API using `token` were migrated
   to ADO connections** (the two health-gate tests in `test_connection_health.py`). The
   health gate mechanic is platform-independent and the ADO fixtures still exercise it fully.

4. **`test_automatic_review_startup_failure_is_marked_and_reported` was already failing
   on the base branch** before this brief. It is not related to these changes.
