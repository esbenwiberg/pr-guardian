# Handover — reluctant-butterfly (Brief 07)

**Pod:** reluctant-butterfly
**Branch:** autopod/yelling-monkey (stacked)
**Date:** 2026-06-07
**Brief:** 07 — Clean Docs and Remove GitHub PAT Runtime References

---

## What was built

### Code fixes
- **`src/pr_guardian/persistence/storage.py`** — removed `GITHUB_TOKEN` env
  fallback from `resolve_github_token`. The function now raises `LookupError`
  when no stored token is found instead of silently returning
  `os.environ.get("GITHUB_TOKEN", "")`. Per the fantastic-whippet handover,
  no product code calls this function anymore; the raise makes accidental
  callers fail explicitly rather than silently using an empty token.

- **`src/pr_guardian/api/pr_dashboard_api.py`** — replaced
  `os.environ.get("GITHUB_TOKEN", "")` in the wizard self-assign code with
  `await create_github_adapter()` from `pr_guardian.platform.factory`. The
  call is inside a broad `try/except Exception` so `ValueError` (no App
  Connection available) is logged as a warning and doesn't fail the wizard.

### New documentation
- **`docs/github-app-setup.md`** — full operator guide: required GitHub App
  permissions, create/install the App, add a Guardian Connection via the
  Profiles UI, link a repository, merge gate (`guardian/review`), `@guardian`
  ChatOps grammar, sticky guidance comment behavior, and sandbox E2E harness.

### Updated documentation
- **`CLAUDE.md`** — replaced `GITHUB_TOKEN + GITHUB_WEBHOOK_SECRET enable
  platform mode` with an explicit note: GitHub uses a Connection (not
  GITHUB_TOKEN), GITHUB_WEBHOOK_SECRET is still needed for signature
  verification, ADO still uses ADO_PAT + ADO_ORG_URL, and E2E harness is
  opt-in sandbox-only.

- **`README.md`** — removed `export GITHUB_TOKEN=ghp_...` from env vars
  section; replaced with `GITHUB_WEBHOOK_SECRET` note and a call-out block
  explaining that GitHub credentials are managed via the App Connection UI.

### New test
- **`tests/test_no_github_pat_runtime.py`** — two static scans:
  - `test_no_github_token_runtime_fallback_remains`: rg-style scan of
    `src/pr_guardian/` for `os.environ.get("GITHUB_TOKEN"` / `os.getenv("GITHUB_TOKEN"`;
    fails if any hit found. Prevents regressions.
  - `test_current_docs_describe_github_app_only_runtime`: asserts
    `docs/github-app-setup.md` exists and contains GitHub App, App ID, private
    key, `guardian/review`, and `@guardian`. Both pass (618 total pass).

---

## Interfaces or contracts changed

- `resolve_github_token()` in `storage.py` now raises `LookupError` instead of
  returning `os.environ.get("GITHUB_TOKEN", "")` as a last resort. Any caller
  that expected a silent empty-string fallback will now get a `LookupError`.
  No product code calls this function (verified by grep); it is kept only for
  any external tooling that may import it.

- `pr_dashboard_api.py` wizard self-assign: the code now calls
  `create_github_adapter()` which requires a GitHub App Connection in the DB.
  If no Connection is found a `ValueError` is raised and caught as a warning.
  Previously it used a bare token (often empty) which silently no-oped.

---

## Files this pod owns (do not modify without good reason)

- `docs/github-app-setup.md` — primary operator setup guide
- `tests/test_no_github_pat_runtime.py` — regression guard; only relax
  with a clear justification
- `CLAUDE.md` runtime notes section (GitHub App paragraph)
- `README.md` Environment Variables section

---

## Discovered constraints and landmines

- `resolve_github_token` in `storage.py` is kept but deprecated. Its docstring
  was updated to say "Deprecated — use `build_github_adapter_from_connection()`".
  A future cleanup pass could remove the function entirely once confident
  nothing external calls it.

- `infra/docker-compose/.env.example` and `infra/docker-compose/docker-compose.yml`
  still reference `GITHUB_TOKEN` in compose passthrough env vars. These are
  infrastructure files (not scanned by the static test) and reflect pre-App
  deployment config. A separate infra cleanup pass should update them to
  document the App Connection approach (or remove `GITHUB_TOKEN` from the
  compose template entirely since Guardian no longer reads it).

- `infra/azure/deploy.sh` and `infra/azure/container-app.bicep` still
  reference `GITHUB_TOKEN`. Same situation — out of scope for Brief 07 but
  should be cleaned up by whoever next touches the Azure deployment.

- `docs/plan/11-external-api-and-auth.md` contains historical references to
  `GITHUB_TOKEN` as a legacy fallback path. The brief constrains us not to
  rewrite historical plan docs, so this was left intact. It is not linked from
  current setup docs.

- The static scan (`test_no_github_token_runtime_fallback_remains`) only covers
  `src/pr_guardian/`. Test files (`tests/`), infrastructure (`infra/`), and
  spec docs (`specs/`) are intentionally excluded because they legitimately use
  monkeypatch, compose passthrough, and historical references.
