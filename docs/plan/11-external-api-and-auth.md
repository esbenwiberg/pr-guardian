# 11 — External API, Entra ID Auth & Platform Service Principals

## Goal

Expose a versioned external API that developers can build a CLI on top of,
secured by Microsoft Entra ID. Replace personal access tokens (PATs) with
service-principal / app-based auth for both Azure DevOps and GitHub.

---

## 1  Auth Model

### 1.1  Entra ID App Registrations

Three logical registrations (can be collapsed if desired):

| Registration | Type | Purpose |
|---|---|---|
| **PR Guardian API** | Web API | Exposes scopes + app roles, validates incoming JWTs |
| **PR Guardian CLI** | Public client | Device-code flow for developer CLI sessions |
| **PR Guardian Service** | Confidential client | Client-credentials flow for ADO SP + daemon-to-API calls |

#### API App Registration

- **Application ID URI**: `api://<api-client-id>`
- **Exposed scopes** (delegated — for device-code / browser flows):
  - `Review.Execute` — trigger and view PR reviews
  - `Scan.Execute` — trigger and view scans
  - `Dashboard.Read` — read dashboard stats, review & scan lists
  - `Settings.Write` — modify LLM settings, prompts
- **App roles** (application — for client-credentials flow):
  - Same four names: `Review.Execute`, `Scan.Execute`, `Dashboard.Read`, `Settings.Write`
  - `allowedMemberTypes: ["Application"]` (or `["User", "Application"]` to
    also allow user-assignment via Azure portal)

#### CLI App Registration

- **Redirect URI**: `http://localhost` (for device-code there is none, but
  MSAL may require a mobile/desktop redirect)
- **Public client**: yes (no secret)
- **API permissions**: delegated scopes from the API app
- Admin consent **not** required (scopes are user-consentable)

#### Service App Registration

- **Client credential**: secret or certificate (cert preferred for production)
- **API permissions**: application permissions (app roles) from the API app
- **Admin consent**: required (admin grants the app roles)
- Also used as the service principal for Azure DevOps (see §3)

### 1.2  Token Flows

```
┌────────────────────────────────────────────────────┐
│  CLI (developer)                                   │
│  ─ device code flow ─────────────────────► Entra   │
│  ← delegated token (scp claim) ◄──────── ID       │
│  ─ Authorization: Bearer <token> ──► FastAPI API   │
├────────────────────────────────────────────────────┤
│  Dashboard (browser)                               │
│  ─ auth code + PKCE (redirect) ──────────► Entra   │
│  ← delegated token (scp claim) ◄──────── ID       │
│  ─ Authorization: Bearer <token> ──► FastAPI API   │
├────────────────────────────────────────────────────┤
│  Service / daemon                                  │
│  ─ client credentials ───────────────────► Entra   │
│  ← app-only token (roles claim) ◄─────── ID       │
│  ─ Authorization: Bearer <token> ──► FastAPI API   │
└────────────────────────────────────────────────────┘
```

### 1.3  Token Validation (API Side)

FastAPI dependency validates every `/api/*` request:

1. Extract `Authorization: Bearer <token>` header
2. Fetch OIDC discovery doc from
   `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`
   (cached, refreshed periodically)
3. Validate JWT signature against JWKS public keys (RS256, match by `kid`)
4. Check standard claims: `iss`, `aud` (= API client ID), `exp`, `nbf`
5. Determine token type:
   - **Delegated** (`scp` claim present): check required scope for the endpoint
   - **App-only** (`roles` claim, and `oid == sub`): check required role
6. Return 401 on invalid token, 403 on missing permission

Library options (pick one):
- **`fastapi-azure-auth`** — handles discovery, JWKS rotation, nonce quirks
  out of the box. Recommended.
- **Manual `PyJWT`** — more control, more code. Only if we need to customise
  heavily.

### 1.4  Permission Mapping

| Endpoint pattern | Required permission |
|---|---|
| `POST /api/v1/review` | `Review.Execute` |
| `GET  /api/v1/reviews/*` | `Dashboard.Read` |
| `POST /api/v1/scan/*` | `Scan.Execute` |
| `GET  /api/v1/scans/*` | `Dashboard.Read` |
| `GET  /api/v1/dashboard/*` | `Dashboard.Read` |
| `GET  /api/v1/events` | `Dashboard.Read` |
| `PUT  /api/v1/settings` | `Settings.Write` |
| `GET  /api/v1/settings` | `Dashboard.Read` |
| `PUT  /api/v1/prompts/*` | `Settings.Write` |
| `GET  /api/v1/prompts` | `Dashboard.Read` |
| `DELETE /api/v1/reviews/*` | `Review.Execute` |
| `DELETE /api/v1/prompts/*` | `Settings.Write` |
| `POST /api/webhooks/*` | No Bearer — HMAC/platform signature only |
| `GET  /api/health` | No auth (health probes) |

---

## 2  External API (Versioned)

### 2.1  URL Structure

```
/api/v1/review              POST   trigger review
/api/v1/reviews             GET    list reviews
/api/v1/reviews/{id}        GET    review detail
/api/v1/reviews/{id}        DELETE cancel review
/api/v1/reviews/{id}/export GET    export findings (JSON / CSV)
/api/v1/active              GET    in-progress reviews

/api/v1/scan/recent         POST   trigger recent-changes scan
/api/v1/scan/maintenance    POST   trigger maintenance scan
/api/v1/scans               GET    list scans
/api/v1/scans/stats         GET    scan stats
/api/v1/scans/{id}          GET    scan detail
/api/v1/scans/{id}/export   GET    export scan findings

/api/v1/dashboard/stats     GET    dashboard overview stats
/api/v1/events              GET    SSE stream

/api/v1/settings            GET    current settings
/api/v1/settings            PUT    update settings
/api/v1/prompts             GET    list prompts
/api/v1/prompts/{name}      PUT    update prompt
/api/v1/prompts/{name}      DELETE reset prompt

/api/webhooks/github        POST   (no version — webhook URLs are registered externally)
/api/webhooks/ado           POST
/api/health                 GET
```

### 2.2  Backward Compatibility

During migration, mount old `/api/dashboard/*` and `/api/review` routes as
thin redirects or aliases pointing to `/api/v1/*`. Remove after one release
cycle.

### 2.3  OpenAPI & CLI Discoverability

- FastAPI auto-generates OpenAPI spec at `/api/v1/openapi.json`
- Add `security` schemes to the spec so generated clients know about Bearer auth
- CLI devs can use the spec to codegen a client or just reference it

---

## 3  Azure DevOps — Service Principal Auth

### 3.1  Current State

ADO adapter uses a PAT via `ADO_PAT` env var → Basic auth
(`Authorization: Basic base64(:pat)`).

### 3.2  Target State

Use the **Service** app registration (§1.1) as a service principal in the
Azure DevOps organization.

**Setup (one-time, manual):**
1. In the ADO org: Organization Settings → Users → Add the SP (by display name
   from Enterprise Applications pane — use the **Object ID** from Enterprise
   Applications, not from App Registrations)
2. Grant the SP appropriate project-level permissions (Code Read/Write, PR
   Contribute, etc.)
3. Assign an access level (Basic)

**Token acquisition (in code):**

```python
import msal

_ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"

app = msal.ConfidentialClientApplication(
    client_id=os.environ["ADO_CLIENT_ID"],
    authority=f"https://login.microsoftonline.com/{os.environ['ADO_TENANT_ID']}",
    client_credential=os.environ["ADO_CLIENT_SECRET"],
)

result = app.acquire_token_for_client(scopes=[_ADO_SCOPE])
access_token = result["access_token"]  # ~1 hour lifetime, MSAL caches
```

**Usage:**
```
Authorization: Bearer <access_token>
```
instead of `Basic base64(:pat)`.

### 3.3  Migration Strategy

- If `ADO_CLIENT_ID` + `ADO_TENANT_ID` + `ADO_CLIENT_SECRET` are set →
  use service principal (Bearer)
- Else if `ADO_PAT` is set → use PAT (Basic) — backward compatible
- Else → error on ADO adapter creation

### 3.4  Environment Variables (New)

```
ADO_CLIENT_ID       Entra ID app client ID for ADO service principal
ADO_TENANT_ID       Entra ID tenant ID
ADO_CLIENT_SECRET   Client secret (or ADO_CLIENT_CERT_PATH for cert)
ADO_ORG_URL         (unchanged) https://dev.azure.com/yourorg
```

---

## 4  GitHub — App-Based Auth

### 4.1  Current State

GitHub adapter uses a PAT via `GITHUB_TOKEN` env var →
`Authorization: token <pat>`.

### 4.2  Why GitHub App

GitHub doesn't accept Entra ID tokens. The closest equivalent to a service
principal is a **GitHub App**:

- Org-owned identity (not tied to a user account)
- Short-lived tokens (1 hour, auto-rotating)
- 15,000 req/hr rate limit (3× higher than PAT's 5,000)
- Granular repo + permission scoping per token
- Private key can be stored in Azure Key Vault

### 4.3  Auth Flow

```
Private Key (PEM)
    │
    ▼
Sign JWT (RS256, iss=app_client_id, exp=10min)
    │
    ▼
POST /app/installations/{install_id}/access_tokens
    │
    ▼
Installation token (1 hour, scoped to repos + permissions)
    │
    ▼
Authorization: token <installation_token>
```

### 4.4  Implementation

Add a `GitHubAppAuth` helper class:

```python
import time
import jwt  # PyJWT

class GitHubAppAuth:
    def __init__(self, app_id: str, private_key: str, installation_id: str):
        self._app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id
        self._token: str | None = None
        self._expires_at: float = 0

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 600, "iss": self._app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._expires_at - 300:
            return self._token  # cached, still valid

        app_jwt = self._generate_jwt()
        resp = await client.post(
            f"https://api.github.com/app/installations/{self._installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        # Parse ISO expiry to epoch
        from datetime import datetime, timezone
        self._expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        ).timestamp()
        return self._token
```

The `GitHubAdapter._get_client()` is modified to call `get_token()` before
each request (or use a token-refreshing auth transport).

### 4.5  Migration Strategy

- If `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY` + `GITHUB_APP_INSTALLATION_ID`
  are set → use GitHub App auth
- Else if `GITHUB_TOKEN` is set → use PAT — backward compatible
- Else → error on GitHub adapter creation

### 4.6  Environment Variables (New)

```
GITHUB_APP_ID                GitHub App's Client ID (not the numeric App ID)
GITHUB_APP_PRIVATE_KEY       PEM private key (or path via GITHUB_APP_KEY_FILE)
GITHUB_APP_INSTALLATION_ID   Installation ID (from app install URL or API)
```

---

## 5  Dashboard — MSAL.js Integration

### 5.1  Approach

The dashboard is a Jinja2-rendered HTML shell that fetches all data via JS
calls to `/api/*`. No server-side sessions needed.

- Add `@azure/msal-browser` (via CDN or vendored)
- On page load, MSAL.js checks for an authenticated session
- If not authenticated → redirect to Microsoft login (auth code + PKCE)
- On return, MSAL.js stores tokens in browser storage
- All `fetch()` calls to `/api/v1/*` include `Authorization: Bearer <token>`
- Token refresh is handled automatically by MSAL.js

### 5.2  Configuration

The dashboard JS needs the Entra ID config. Serve it via a small endpoint
or inject into the HTML template:

```javascript
const msalConfig = {
    auth: {
        clientId: "{{ entra_client_id }}",   // API app or separate SPA app
        authority: "https://login.microsoftonline.com/{{ entra_tenant_id }}",
        redirectUri: window.location.origin + "/dashboard",
    },
};
const loginRequest = {
    scopes: ["api://{{ entra_client_id }}/Dashboard.Read"],
};
```

### 5.3  Unauthenticated Pages

These remain accessible without auth:
- `/` (redirect to `/dashboard`)
- `/api/health`
- `/api/webhooks/*` (platform signature verification only)

All dashboard HTML pages are served unauthenticated (they're just UI shells).
The auth gate happens on the API calls — if the user isn't logged in, API
calls return 401 and the JS redirects to login.

---

## 6  New Dependencies

```toml
# pyproject.toml additions
"msal",                      # Entra ID token acquisition (ADO SP, test tooling)
"PyJWT>=2.0",                # GitHub App JWT signing
"fastapi-azure-auth",        # JWT validation middleware
"azure-identity",            # DefaultAzureCredential (managed identity, CLI, etc.)
"azure-keyvault-secrets",    # Key Vault SecretClient
```

```
# Dashboard (CDN or vendored)
@azure/msal-browser          # Browser-side auth for dashboard
```

---

## 7  Environment Variables — Full Picture

### Required for Entra ID API Auth

```
ENTRA_TENANT_ID          Entra ID tenant
ENTRA_API_CLIENT_ID      API app registration client ID
```

### Key Vault (optional, recommended for production)

```
AZURE_KEYVAULT_URL       https://prguardian-dev-kv.vault.azure.net/
                         (omit to disable — secrets fall back to env vars)
```

### Azure DevOps (choose one)

```
# Service principal (preferred)
ADO_CLIENT_ID
ADO_TENANT_ID
ADO_CLIENT_SECRET        (or ADO_CLIENT_CERT_PATH)
ADO_ORG_URL

# PAT (legacy fallback)
ADO_PAT
ADO_ORG_URL
```

### GitHub (choose one)

```
# GitHub App (preferred)
GITHUB_APP_ID
GITHUB_APP_PRIVATE_KEY   (or GITHUB_APP_KEY_FILE)
GITHUB_APP_INSTALLATION_ID

# PAT (legacy fallback)
GITHUB_TOKEN
```

### Unchanged

```
DATABASE_URL
GUARDIAN_DB_ENABLED
GUARDIAN_SECRET_KEY
GITHUB_WEBHOOK_SECRET
ANTHROPIC_API_KEY
AZURE_AI_FOUNDRY_API_KEY
AZURE_AI_FOUNDRY_ENDPOINT
```

---

## 8  Implementation Order

### Phase 1 — Key Vault, Auth Middleware & API Versioning

1. Add `src/pr_guardian/auth/` module:
   - `keyvault.py` — Key Vault init + `get_secret()` with env-var fallback
   - `entra.py` — JWT validation dependency (fastapi-azure-auth config)
   - `permissions.py` — `require_permission()` dependency factory
2. Wire Key Vault into `main.py` lifespan (before DB init)
3. Create versioned router mount at `/api/v1/`
4. Apply auth dependencies to all `/api/v1/*` routes
5. Keep `/api/health` and `/api/webhooks/*` unauthenticated
6. Add `ENTRA_TENANT_ID`, `ENTRA_API_CLIENT_ID`, `AZURE_KEYVAULT_URL` to config
7. Feature flag: if Entra env vars are not set, skip auth (dev mode with
   warning log). If Key Vault URL is not set, fall back to env vars.

### Phase 2 — ADO Service Principal

1. Add MSAL token acquisition to `ADOAdapter`
2. Auto-detect: SP env vars → Bearer, PAT → Basic
3. Token caching handled by MSAL (in-memory, ~1hr lifetime)
4. Update `platform/factory.py` to pass SP credentials

### Phase 3 — GitHub App Auth

1. Add `GitHubAppAuth` helper class
2. Modify `GitHubAdapter` to use installation tokens
3. Auto-detect: App env vars → GitHub App, `GITHUB_TOKEN` → PAT
4. Token caching with 5-minute early refresh
5. Update `platform/factory.py`

### Phase 4 — Dashboard MSAL.js

1. Add `@azure/msal-browser` to dashboard static assets
2. Create `auth.js` — MSAL init, login, token acquisition, fetch wrapper
3. Update all dashboard JS to use authenticated fetch
4. Add login/logout UI (user avatar, sign-out button)
5. Inject Entra config into HTML templates

### Phase 5 — CLI Support & OpenAPI

1. Add security schemes to OpenAPI spec
2. Add `/api/v1/reviews/{id}/export` and `/api/v1/scans/{id}/export`
3. Document device-code flow for CLI developers
4. Publish OpenAPI spec for client codegen

---

## 9  Security Considerations

- **Token lifetime**: Entra ID access tokens are ~1 hour, non-configurable for
  client credentials. MSAL handles caching and refresh.
- **No secrets in code**: All credentials via env vars or Key Vault. GitHub App
  private key should be in Key Vault in production.
- **Webhook endpoints stay out of Entra auth**: They use platform-native
  verification (GitHub HMAC, ADO basic auth / IP filtering).
- **Dev mode**: When `ENTRA_TENANT_ID` is not set, auth is disabled with a
  loud warning. This keeps local development frictionless.
- **CORS**: Dashboard served from same origin, no CORS needed. If CLI is
  browser-based in future, add CORS for the API app's redirect URIs.
- **Rate limiting**: Consider adding rate limiting to the external API
  (separate concern, not in this plan).

---

## 10  Decisions (Resolved)

1. **Single app registration for CLI and dashboard.** One public client
   registration supports both device-code (CLI) and auth-code + PKCE
   (dashboard SPA). Fewer moving parts, one client ID to configure. If
   independent revocation is ever needed, handle it in API logic.

2. **No role-based gating initially.** Any authenticated tenant user gets
   full access. The permission names (`Review.Execute`, `Dashboard.Read`,
   etc.) are defined in the Entra ID app and in the code structure, so
   adding role checks later requires no refactoring — just enable the
   checks. Identity is still captured (`oid` / `preferred_username` in the
   token) for audit purposes.

3. **Single-tenant only.** Authority is
   `https://login.microsoftonline.com/{tenant-id}`. Multi-tenant (switching
   to `/common` + tenant allowlist) is a straightforward migration if needed
   later.

4. **Azure Key Vault for all platform secrets.** The Container App's managed
   identity fetches secrets at startup. This covers:
   - GitHub App private key
   - ADO service principal client secret (or certificate)
   - Any future secrets (LLM keys could migrate here too)

   Benefits: rotation without redeployment (restart picks up new version),
   audit log on every secret access, no secrets in env vars or config.

   Implementation: use `azure-identity` (`DefaultAzureCredential`) +
   `azure-keyvault-secrets` (`SecretClient`). At startup, resolve configured
   Key Vault secret names → inject into adapters. Env-var fallback for local
   dev (when Key Vault is not available).

---

## 11  Key Vault Integration Detail

### 11.1  Secret Layout

```
Key Vault: prguardian-dev-kv (or via AZURE_KEYVAULT_URL env var)

Secrets:
  github-app-private-key       PEM-encoded RSA private key
  ado-client-secret            Entra ID SP client secret for ADO
  guardian-secret-key           Fernet master key for DB encryption
```

### 11.2  Access Policy

- Container App managed identity gets `Secret > Get, List` on the Key Vault
- No other principals need secret access in normal operation
- Rotation: update the secret in Key Vault, restart the Container App (or
  implement background refresh on a timer)

### 11.3  Code Pattern

```python
# src/pr_guardian/auth/keyvault.py
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

_client: SecretClient | None = None

async def init_keyvault():
    global _client
    vault_url = os.environ.get("AZURE_KEYVAULT_URL")
    if not vault_url:
        log.info("keyvault_disabled", hint="Set AZURE_KEYVAULT_URL to enable")
        return
    _client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    log.info("keyvault_ready", vault=vault_url)

def get_secret(name: str, fallback_env: str = "") -> str:
    """Fetch from Key Vault, fall back to env var."""
    if _client:
        try:
            return _client.get_secret(name).value or ""
        except Exception:
            log.warning("keyvault_secret_miss", name=name)
    return os.environ.get(fallback_env, "")
```

### 11.4  Startup Integration

In `main.py` lifespan:
1. `await init_keyvault()` (if `AZURE_KEYVAULT_URL` is set)
2. Resolve platform secrets via `get_secret()` with env-var fallbacks
3. Pass resolved secrets to adapter factories

### 11.5  New Dependencies

```toml
"azure-identity",           # DefaultAzureCredential (managed identity, CLI, etc.)
"azure-keyvault-secrets",   # SecretClient
```

### 11.6  Environment Variables

```
AZURE_KEYVAULT_URL    https://prguardian-dev-kv.vault.azure.net/
                      (omit to disable Key Vault, use env-var fallbacks)
```

### 11.7  Revised Implementation Order

Phase 1 becomes:
1. Key Vault module (`auth/keyvault.py`)
2. Auth middleware (`auth/entra.py`, `auth/permissions.py`)
3. API versioning (`/api/v1/`)
4. Wire Key Vault into lifespan + adapter factories
