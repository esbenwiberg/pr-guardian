# Handover: payable-skink

## Built

- Added the `/api/profiles` management router for Profile CRUD, Connection CRUD/archive/validate,
  repo-link CRUD/archive/pause/auto-review, Profile Manager admin APIs, env import availability,
  and audit history.
- Registered the router in `main.py`.
- Added `Identity.can_manage_profiles`, profile-manager identity resolution from
  `profile_managers`, `/api/me` exposure, and `require_profile_manager`.
- Tightened admin dependencies so API-key identities no longer satisfy product administration
  routes, matching the Brief 02 permission matrix.
- Extended storage with list/update/archive helpers for Profiles, Connections, repo links,
  Profile Managers, and audit events.
- Added redacted field-level audit diffs. Connection token rotations record a
  `token_secret: changed` marker and sanitized token-prefix changes only; raw tokens and
  encrypted values are not returned or stored in audit diffs.
- Added healthy-Connection gates for repo-link creation/update and `sync_enabled=true`.
  Unhealthy validation still leaves the Connection saved with health status/message.
- Restricted ADO validation targets to canonical HTTPS Azure DevOps organization URLs before
  sending PAT credentials, preventing arbitrary org URL credential egress.
- Added required API tests:
  `tests/test_profiles_api.py` and `tests/test_connection_health.py`.

## Deviations

- Profile CRUD is exposed at both `/api/profiles` and `/api/profiles/profiles` for list/create.
  The nested path keeps the management router grouped with `/connections`, `/repo-links`,
  `/managers`, and `/audit`, while the root alias gives callers the natural Profile collection
  URL.
- Connection and repo-link updates support `PATCH` and `PUT`. Tests use `PATCH`, but `PUT`
  aliases were added for CRUD clients that expect it.
- The API validates GitHub/ADO credentials directly with `httpx` at runtime. Tests patch the
  `_probe_connection()` seam so contract tests never call external platforms.

## Interfaces downstream pods should know

- Management permission dependency is `require_profile_manager`; it accepts admins and signed-in
  users in `profile_managers`, and rejects API-key identities even if their creator is an admin.
- `require_admin` now rejects API-key identities for admin/settings/API-key/LLM management.
- `/api/me` now returns `can_manage_profiles`; admins report true through the capability flag.
- Profile payload `settings` is a structured dict constrained to active Profile top-level keys.
  Unknown or dormant fields are rejected instead of accepting arbitrary config blobs.
- Connection responses never include raw tokens. Use `token_prefix`, `health_status`,
  `health_message`, and `health_checked_at`.
- Connection validation helper seam is `pr_guardian.api.profiles._probe_connection(platform,
  token, org_url) -> (health_status, health_message)`.
- ADO `org_url` is normalized to `https://dev.azure.com/{org}` or
  `https://{org}.visualstudio.com`; URLs with non-Azure hosts, non-HTTPS schemes, userinfo,
  ports, query strings, fragments, or unexpected paths are rejected.
- Audit rows are returned through `storage.list_profile_audit_events()` and include a top-level
  `diff` computed from redacted field values.

## Files to avoid changing without a good reason

- `src/pr_guardian/api/profiles.py`
- `src/pr_guardian/auth/identity.py`
- `src/pr_guardian/auth/dependencies.py`
- `src/pr_guardian/persistence/storage.py` profile/connection/repo-link helper sections
- `tests/test_profiles_api.py`
- `tests/test_connection_health.py`

## Landmines

- `create_repo_link()` checks platform mismatch before health status so existing storage tests and
  API error semantics stay deterministic.
- `sync_enabled=true` is only persisted after validation reports healthy. If validation fails during
  Connection create, the Connection remains saved as unhealthy with `sync_enabled=false`.
- ADO Connection validation only accepts HTTPS Azure DevOps organization URLs
  (`https://dev.azure.com/{org}` or `https://{org}.visualstudio.com`) to avoid validating
  caller-controlled arbitrary hosts.
- The router intentionally exposes env secret availability only as booleans. It never imports or
  persists env secrets without an explicit future API path.

## Verification

- `python -m pytest tests/test_profiles_api.py::test_profile_manager_can_create_connection_profile_and_repo_link`
- `python -m pytest tests/test_profiles_api.py::test_profile_audit_diffs_redact_connection_secrets`
- `python -m pytest tests/test_connection_health.py::test_unhealthy_connection_blocks_repo_link_and_sync_enabled`
- `validate_locally` passed lint, build, and full `pytest` after re-running lint/tests: 545 passed.
