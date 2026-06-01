# Handover: cruel-reptile

## Built

- Moved broad PR sync off legacy GitHub PAT/env token discovery and onto
  active, unarchived, healthy Connections with `sync_enabled=true`.
- Added `storage.list_broad_sync_connections()` and wired sync to decrypt only
  those selected Connections by ID.
- Added GitHub and ADO broad sync provenance: `sync_sources` and `synced_prs`
  now receive `connection_id` and the redacted Connection snapshot used for the
  sync pass.
- Removed sync-time exclusion filtering from GitHub/ADO broad sync. Exclusions
  remain applied in browse listing APIs, so they hide `/pull-requests` rows
  without blocking explicit repo links or readiness candidates.
- Added `/pull-requests` as the browse-only page route, redirected
  `/pr-dashboard` and `/browse-pr` to it, and added sidebar/command-palette
  entries.
- Added a minimal `pull_requests.html` browse shell backed by `/api/prs`.
- Updated dev seed data so synced PR rows and sync sources include Connection
  provenance and the new page has visible seeded rows.
- Added required fact coverage in `tests/test_connection_sync.py` and
  `tests/browser/pull_requests_page.spec.mjs`.
- Updated existing exclusion sync tests to assert browse-only exclusions and
  Connection-based sync iteration.

## Deviations

- The requested browser fact command is `node tests/browser/pull_requests_page.spec.mjs`.
  This sandbox had the Playwright package but no browser binaries, so the script
  uses Node's built-in HTTP/fetch APIs against the real template and fake API
  responses instead of launching Chromium. It still proves `/pull-requests`
  route/page separation and `/reviews` queue data separation without downloading
  browsers.
- Touched `src/pr_guardian/config/profile_resolver.py`,
  `src/pr_guardian/platform/factory.py`, and
  `tests/test_profile_config_resolver.py` only to apply pending `ruff format`
  changes required by repo-level validation. No behavior changed there.
- The parent handover path
  `specs/readiness-gated-profiles/handovers/bloody-raccoon.md` was not present
  in this worktree. I read `payable-skink.md` and the available
  `ashamed-rhinoceros.md` handover instead.

## Interfaces downstream pods should know

- Broad sync source helper:
  `storage.list_broad_sync_connections() -> list[dict[str, Any]]` returns only
  unarchived Connections where `health_status == "healthy"` and
  `sync_enabled is True`.
- `pr_sync.run_pr_sync()` no longer reads `GITHUB_TOKEN`, `ADO_PAT`, or
  `ADO_ORG_URL`, and no longer calls legacy `list_github_pats()` or
  `resolve_github_token()` for broad browse sync.
- `_sync_github(token, connection)` and `_sync_ado(token, connection)` now
  require a redacted Connection DTO. Tests or downstream code patching these
  functions must pass the new argument.
- Browse exclusions are applied by `storage.list_synced_prs()` and related
  browse APIs, not by sync-time repository discovery.
- `/pull-requests` is the broad browse surface. `/pr-dashboard` and
  `/browse-pr` are compatibility redirects to `/pull-requests`.

## Files to avoid changing without a good reason

- `src/pr_guardian/core/pr_sync.py`
- `src/pr_guardian/persistence/storage.py` around `list_broad_sync_connections`
  and synced PR provenance helpers
- `src/pr_guardian/api/dashboard_page.py` route/redirect definitions
- `src/pr_guardian/dashboard/pull_requests.html`
- `tests/test_connection_sync.py`
- `tests/browser/pull_requests_page.spec.mjs`

## Landmines

- `get_connection_token()` is the only place broad sync decrypts selected
  Connection tokens. Do not reintroduce env token fallback for broad sync.
- `sync_enabled=false` Connections are intentionally ignored by broad sync but
  remain valid for repo links and manual flows.
- Because exclusions are browse-only, sync may persist rows for excluded repos.
  Downstream readiness work should avoid using browse-filtered list helpers for
  candidate lookup.
- Existing legacy PAT admin/storage APIs still exist as compatibility wrappers
  elsewhere; this brief only removed their use from broad sync.

## Verification

- `python -m pytest tests/test_connection_sync.py::test_pr_sync_uses_only_healthy_sync_enabled_connections`
- `python -m pytest tests/test_connection_sync.py::test_exclusions_hide_browse_rows_but_not_linked_candidates`
- `python -m pytest tests/test_connection_sync.py::test_non_opted_prs_stay_in_pull_requests_api_not_reviews`
- `node tests/browser/pull_requests_page.spec.mjs`
- `python -m pytest tests/test_exclusion_rules.py tests/test_connection_sync.py`
- `validate_locally` passed lint, build, and full pytest: 555 passed.
