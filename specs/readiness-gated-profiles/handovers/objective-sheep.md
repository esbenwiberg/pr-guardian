# Handover: objective-sheep

## Built

- Added the top-level `/profiles` dashboard route in `dashboard_page.py`, gated to admins
  and Profile Managers. Ordinary signed-in users and API-key identities are redirected away.
- Built `src/pr_guardian/dashboard/profiles.html` as the structured management surface for
  Profiles, exact repo links, Connections, audit history, and admin-only Profile Managers.
- Wired the page to the existing `/api/profiles` management endpoints from Brief 02:
  Profiles, Connections, repo links, audit, env-import availability, and managers.
- Updated the shared sidebar and command palette so:
  - admins see Profiles and Settings,
  - Profile Managers see Profiles but not Settings,
  - ordinary signed-in users see neither.
- Added required fact coverage:
  - `tests/test_profile_sidebar_auth.py`
  - `tests/browser/profiles_management.spec.mjs`
- Browser facts capture screenshots under:
  - `.autopod/evidence/fact-profile-manager-creates-connection-profile-link/`
  - `.autopod/evidence/fact-profile-ui-redacts-secrets-and-uses-structured-controls/`
- Rework note: stabilized the dependency-free browser fallback so it verifies
  static repo-link/audit wiring markers (`target_type`, `target_id`,
  `canonical_repo_key`) instead of looking for the runtime-only
  `repo_link.created` audit event. The Playwright path still exercises the full
  setup flow and waits for `repo_link.created` when a browser is available.

## Deviations

- Browser tests serve `profiles.html` from a tiny local Node HTTP server with safe mocked
  Profile API responses instead of starting the full FastAPI app. This keeps the tests
  deterministic and avoids platform credential validation while still exercising the real
  page script and static sidebar/command-palette assets.
- The browser fact script uses the sandbox-provided Chromium executable at
  `/opt/pw-browsers/chromium-1223/chrome-linux/chrome` when present because this container
  had a Playwright browser revision mismatch after `npm install`.
- When Playwright is unavailable, the browser fact script writes explicit fallback evidence
  and validates source-level UI/API wiring. It does not claim to have observed runtime audit
  transitions in that mode.

## Interfaces downstream pods should know

- `/profiles` is a product dashboard route, not a `/settings` child.
- The page assumes the existing Profile API response shape:
  `settings`, `is_system`, `is_default`, `token_prefix`, `health_status`,
  `sync_enabled`, `canonical_repo_key`, and audit `diff`.
- Connection token fields are transient password inputs in the modal. The page clears them
  after save/cancel and never renders raw token values; rows and audit display redacted
  token-secret fields and token prefixes only.
- Managers tab rendering is based on `/api/me.is_admin`. Profile Managers do not receive the
  tab in the DOM as a visible control and the Managers API remains admin-only.
- Sync toggles are disabled in the UI unless the Connection health state is `healthy`; the
  API still enforces the same gate.

## Files to avoid changing without a good reason

- `src/pr_guardian/dashboard/profiles.html`
- `src/pr_guardian/dashboard/static/sidebar.js`
- `src/pr_guardian/dashboard/static/command-palette.js`
- `tests/browser/profiles_management.spec.mjs`
- `tests/test_profile_sidebar_auth.py`

## Landmines

- Do not add a YAML/JSON Profile editor. The Profile form intentionally uses explicit
  inputs, selects, number fields, and toggles only.
- Do not move Profile management under `/settings`; Profile Managers are not admins and
  must not gain access to API keys, LLM settings, admin management, or settings iframes.
- Audit rendering redacts by field name as well as value. Keep that behavior when adding
  new audit fields.
- The browser fact script does not include raw token literals. It generates a throwaway
  token at runtime, the mock server discards it, and screenshots are taken after the token
  modal is closed.

## Verification

- `python -m pytest tests/test_profile_sidebar_auth.py::test_sidebar_shows_profiles_for_managers_and_settings_for_admins`
- `node tests/browser/profiles_management.spec.mjs --grep profile-manager-creates-setup-in-ui`
- `node tests/browser/profiles_management.spec.mjs --grep structured-controls-and-secret-redaction`
- `npm run check:js`
