# Handover: ashamed-rhinoceros

## Built

- Added migration `019_add_profiles_connections_readiness.py` with Profiles, Connections,
  repo links, profile managers, profile audit events, readiness candidates, candidate
  transitions, provenance columns on reviews/scans/synced PRs/sync sources, default/noop
  Profile bootstrap, and GitHub PAT to Connection migration.
- Added ORM rows and storage helpers for Profile, Connection, repo link, manager, audit,
  readiness candidate, candidate transition, review provenance, and scan provenance.
- Removed `GithubPatRow` and `ReviewRow.pat_name` from the active model contract.
  Existing legacy PAT storage functions remain as connection-backed compatibility shims
  because API and UI replacement belongs to later briefs.
- Added required storage tests in `tests/test_profiles_storage.py` and
  `tests/test_readiness_storage.py`.

## Deviations

- Touched `src/pr_guardian/api/dashboard.py` only because repo-wide `ruff format --check .`
  failed on a pre-existing wrap-only formatting issue. No dashboard behavior changed.
- Kept legacy `create_github_pat`, `list_github_pats`, `update_github_pat`,
  `delete_github_pat`, and `resolve_github_token` function names in storage, backed by
  `ConnectionRow`, so current routes keep importing until brief 02/05 removes old PAT APIs.

## Interfaces downstream pods should know

- `DEFAULT_PROFILE_ID` is `00000000-0000-0000-0000-000000000001`.
- Candidate states are enforced by `READINESS_STATES` and DB checks:
  `waiting`, `blocked`, `reviewing`, `reviewed`, `superseded`, `error`.
- Storage DTOs intentionally never expose `encrypted_token`; use `token_prefix`,
  `health_status`, `health_message`, and snapshots.
- `archive_profile()` and `archive_connection()` raise `ArchiveBlockedError` while an
  active repo link depends on the row. Pausing or disabling the repo link first permits
  archive.
- `create_repo_link()` rejects archived Profile/Connection dependencies, and
  `update_repo_link_state()` rejects reactivating a paused/disabled repo link while its
  Profile or Connection is archived or missing.
- `create_review_record(..., pat_name=...)` still accepts the legacy parameter but stores
  it only as `connection_snapshot={"legacy_pat_name": ...}` for historical readability.

## Files to avoid changing without a good reason

- `alembic/versions/019_add_profiles_connections_readiness.py`
- `src/pr_guardian/persistence/models.py`
- `src/pr_guardian/persistence/storage.py`
- `tests/test_profiles_storage.py`
- `tests/test_readiness_storage.py`

## Landmines

- The migration copies existing encrypted PAT blobs as-is. Do not decrypt/re-encrypt them
  in later migrations.
- Repo-link exact uniqueness is a partial Postgres index on active links in the migration.
  The ORM mirrors this with a PostgreSQL partial index; storage callers should still avoid
  creating duplicate active links.
- Existing API/dashboard code still talks in `pat_name` terms. Later briefs should replace
  those surfaces with Profile/Connection selection instead of relying on the compatibility
  shims long term.
