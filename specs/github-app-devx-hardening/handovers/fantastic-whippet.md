# Handover: Brief 02 — Wire GitHub Adapter to Installation Tokens

**Pod:** fantastic-whippet  
**Branch:** autopod/yelling-monkey (stacked)  
**Date:** 2026-06-06

## What was built

### New module: `src/pr_guardian/platform/github_auth.py`

- **`GitHubAppCredentials`** (frozen dataclass): `app_id`, `private_key_pem`, `installation_id`
- **`GitHubInstallationToken`** (frozen dataclass): `token`, `expires_at`
- **`GitHubAppAuth`**: Per-instance installation token cache. `get_token()` is async and:
  - Creates an RS256 JWT using `cryptography` (PKCS1v15 + SHA256, iat backdated 60s for clock skew)
  - Exchanges the JWT at `POST /app/installations/{id}/access_tokens`
  - Caches the token; refreshes 60 seconds before `expires_at`
  - Raises `TypeError` if the PEM key is not RSA
- **`_InstallationBearerAuth`**: `httpx.Auth` subclass that calls `get_token()` via `async_auth_flow` on every request, injecting `Authorization: Bearer <token>`
- **`build_github_adapter_from_connection(connection: dict) -> GitHubAdapter`**: Async helper that validates `auth_kind == 'github_app'`, fetches the encrypted private key from storage, constructs `GitHubAppAuth`, and returns a configured `GitHubAdapter`. Raises `ValueError` for non-App connections.

### `src/pr_guardian/platform/github.py`

- `GitHubAdapter.__init__` gains `app_auth: GitHubAppAuth | None = None` keyword argument.
- `_get_client()`: when `app_auth` is set, creates the `httpx.AsyncClient` with `auth=_InstallationBearerAuth(app_auth.get_token)` (Bearer scheme). Static token path unchanged for backward compat in tests.
- No changes to any request methods — auth is transparent via httpx's auth flow.

### `src/pr_guardian/platform/factory.py`

- `create_adapter("github")` no longer reads `os.environ["GITHUB_TOKEN"]`. GitHub without a `token_override` gets an empty-token adapter (appropriate for webhook normalization only).
- `create_github_adapter(connection_id_or_name)`: async, finds a GitHub App Connection in the DB (by UUID or first in list), delegates to `build_github_adapter_from_connection`. Raises `ValueError` if none found.
- ADO path (`ADO_PAT`/`ADO_ORG_URL` env fallbacks) unchanged.

### `src/pr_guardian/core/readiness.py`

- `_adapter_for_candidate()`: For `platform == "github"`, requires a connection with `auth_kind == 'github_app'`. Calls `build_github_adapter_from_connection`. Raises `ValueError` if no connection or wrong auth_kind.
- Non-GitHub platforms (ADO) use the old token/create_adapter path unchanged.

### `src/pr_guardian/core/pr_sync.py`

- `_sync_github(connection: dict)`: signature changed — no `token` arg. Calls `build_github_adapter_from_connection` internally.
- `run_pr_sync()`: For GitHub connections, checks `auth_kind == 'github_app'`. Skips with a `pr_sync_github_connection_not_app_skipped` warning if not. ADO token resolution logic unchanged.

### `src/pr_guardian/core/github_chatops.py`

- `_fresh_adapter_for_review()`: Resolves the connection by `connection_id`, calls `build_github_adapter_from_connection`. Raises `ValueError` if no App Connection found. Removed fallback to `storage.resolve_github_token`.

## Interfaces and contracts Brief 03+ must know about

### `GitHubAdapter(app_auth=auth)` is the runtime pattern

All runtime GitHub paths now use `build_github_adapter_from_connection(connection)` which returns `GitHubAdapter(app_auth=GitHubAppAuth(creds))`. The old `GitHubAdapter(token="...")` pattern is preserved only for tests and legacy webhook normalization.

### `GitHubAppAuth` is per-instance, not a singleton

Each call to `build_github_adapter_from_connection` creates a fresh `GitHubAppAuth` instance. If Brief 03 or 04 creates adapters frequently (e.g. per PR), tokens will be re-fetched per instance unless the connection is long-lived. Brief 03/04 should reuse a connection-level `GitHubAppAuth` instance if they need to share a cache across multiple adapter creations.

### `build_github_adapter_from_connection` raises `ValueError` for non-App connections

Any caller that passes a connection without `auth_kind == 'github_app'` gets a `ValueError`. Brief 03 must ensure connections are App-typed before calling this.

### `storage.get_connection_private_key(connection_id: UUID) -> str`

Still available and unchanged. Returns empty string if key is absent or decryption fails. `build_github_adapter_from_connection` already handles the empty-string case by raising `ValueError`.

### `storage.resolve_github_token` is no longer called from runtime paths

The `resolve_github_token` storage function still exists (and still falls back to `GITHUB_TOKEN`) but no product code calls it now. Brief 07 (cleanup) should remove it or mark it deprecated.

## Files Brief 03+ should NOT modify without good reason

- `src/pr_guardian/platform/github_auth.py` — core auth module, owned by this brief
- `src/pr_guardian/platform/github.py` lines 62–106 — `GitHubAdapter.__init__` and `_get_client`

## Test changes

- `tests/test_github_app_auth.py` (new): 3 tests for `GitHubAppAuth`
- `tests/test_github_adapter.py`: added `test_github_adapter_uses_installation_token_for_platform_actions`
- `tests/test_connection_sync.py`: updated existing sync test to use App connection; added `test_github_sync_ignores_github_token_without_app_connection`
- `tests/test_exclusion_rules.py`: updated `TestSyncTimeFilter.test_github_sync_does_not_apply_browse_exclusion_rules` (removed token arg), renamed and redesigned `TestMultiPatSync` token-skipping test for App-auth-only behavior

## Discovered constraints / landmines

1. **`httpx.AsyncAuth` does not exist** — the correct base class is `httpx.Auth` which has both `auth_flow` (sync) and `async_auth_flow` (async) hooks. Only `async_auth_flow` is needed for async-only adapters.

2. **`load_pem_private_key` returns a union type** — mypy requires an `isinstance(key, RSAPrivateKey)` narrowing check before calling `.sign(message, padding.PKCS1v15(), hashes.SHA256())`.

3. **`_sync_github` signature change breaks tests that call it directly** — `tests/test_exclusion_rules.py::TestSyncTimeFilter` called `_sync_github("token-xyz", connection)`. Updated to patch `build_github_adapter_from_connection` instead. Future briefs should patch at the function level, not the adapter constructor.

4. **asyncio double-mint edge case** — two concurrent coroutines that both see a near-expired cache will both mint a token. This is benign (GitHub allows multiple valid tokens per installation) and avoids global process state. The per-instance design is intentional per the brief constraint.

5. **Legacy PAT GitHub connections in DB are now silently skipped during sync** — if a broad sync connection has `auth_kind=None`, `run_pr_sync` logs a warning and skips it. Brief 03 (setup/validation) should handle converting or rejecting these connections in the UI.
