# CLI Authentication & API Access

This guide covers how to authenticate against the PR Guardian API from a CLI
or automation script using Entra ID, and how to use the OpenAPI spec for client
code generation.

---

## Prerequisites

- An Entra ID tenant with the **PR Guardian API** app registration configured
  (see [plan/11](plan/11-external-api-and-auth.md) §1.1)
- The **PR Guardian CLI** app registration (public client, device-code enabled)
- Python 3.11+ with `msal` installed, or any language with an MSAL library

You'll need these values from your Entra ID setup:

| Value | Where to find it |
|---|---|
| `TENANT_ID` | Azure Portal → Entra ID → Overview |
| `CLI_CLIENT_ID` | App Registrations → PR Guardian CLI → Application (client) ID |
| `API_CLIENT_ID` | App Registrations → PR Guardian API → Application (client) ID |

---

## Device-Code Flow (Interactive CLI)

The device-code flow lets a developer authenticate without embedding secrets
in the CLI. The user signs in via a browser on any device, and the CLI receives
a token.

### How it works

```
CLI                          Entra ID                    Browser
 │                              │                           │
 ├─ POST /devicecode ──────────►│                           │
 │◄── device_code + user_code ──┤                           │
 │                              │                           │
 │  "Go to https://microsoft.com/devicelogin                │
 │   and enter code: ABCD-EFGH"                             │
 │                              │      ┌────────────────────┤
 │                              │◄─────┤ User enters code   │
 │                              │      │ and signs in        │
 │                              │──────► Consent prompt      │
 │                              │◄─────┤ User approves       │
 │                              │      └────────────────────┘
 │  (polling)                   │
 ├─ POST /token ───────────────►│
 │◄── access_token ─────────────┤
 │                              │
 ├─ GET /api/v1/reviews ───────────────────────────► PR Guardian API
 │  Authorization: Bearer <access_token>
```

### Python example

```python
import msal
import requests

TENANT_ID = "your-tenant-id"
CLI_CLIENT_ID = "your-cli-client-id"
API_CLIENT_ID = "your-api-client-id"
API_BASE = "https://your-pr-guardian-instance.azurecontainerapps.io"

SCOPES = [f"api://{API_CLIENT_ID}/Dashboard.Read"]

app = msal.PublicClientApplication(
    CLI_CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
)

# 1. Try the token cache first (silent auth)
accounts = app.get_accounts()
result = None
if accounts:
    result = app.acquire_token_silent(SCOPES, account=accounts[0])

# 2. Fall back to device-code flow
if not result:
    flow = app.initiate_device_flow(scopes=SCOPES)
    print(flow["message"])  # "Go to https://microsoft.com/devicelogin ..."
    result = app.acquire_token_by_device_flow(flow)

if "access_token" not in result:
    raise SystemExit(f"Auth failed: {result.get('error_description', result)}")

token = result["access_token"]

# 3. Call the API
resp = requests.get(
    f"{API_BASE}/api/v1/reviews",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())
```

### Available scopes

Request the scopes your CLI needs. All are prefixed with `api://<API_CLIENT_ID>/`:

| Scope | Grants |
|---|---|
| `Dashboard.Read` | Read reviews, scans, stats, settings, prompts |
| `Review.Execute` | Trigger and cancel PR reviews |
| `Scan.Execute` | Trigger scans |
| `Settings.Write` | Modify settings and prompts |

You can request multiple scopes in a single token:

```python
SCOPES = [
    f"api://{API_CLIENT_ID}/Dashboard.Read",
    f"api://{API_CLIENT_ID}/Review.Execute",
]
```

### Token caching

MSAL caches tokens in memory by default. For a persistent CLI experience,
use `msal.SerializableTokenCache` to store tokens on disk:

```python
import json
from pathlib import Path

CACHE_FILE = Path.home() / ".pr-guardian" / "token_cache.json"

cache = msal.SerializableTokenCache()
if CACHE_FILE.exists():
    cache.deserialize(CACHE_FILE.read_text())

app = msal.PublicClientApplication(
    CLI_CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    token_cache=cache,
)

# ... authenticate as above ...

# Persist cache after acquiring tokens
if cache.has_state_changed:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(cache.serialize())
```

---

## Client-Credentials Flow (Daemon / CI)

For unattended automation (CI pipelines, scheduled jobs), use the **Service**
app registration with client credentials. No user interaction required.

```python
import msal

TENANT_ID = "your-tenant-id"
SERVICE_CLIENT_ID = "your-service-client-id"
SERVICE_CLIENT_SECRET = "your-service-client-secret"
API_CLIENT_ID = "your-api-client-id"

app = msal.ConfidentialClientApplication(
    SERVICE_CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    client_credential=SERVICE_CLIENT_SECRET,
)

# Client-credentials always uses .default scope
result = app.acquire_token_for_client(
    scopes=[f"api://{API_CLIENT_ID}/.default"],
)

token = result["access_token"]
# Use Authorization: Bearer <token> with API calls
```

The service principal's permissions come from **app roles** assigned in Entra
ID (admin consent required), not from scopes. The token will contain a `roles`
claim instead of `scp`.

---

## OpenAPI Spec & Client Code Generation

The PR Guardian API serves a full OpenAPI 3.x specification, including auth
scheme definitions, at:

```
GET /api/v1/openapi.json
```

Interactive docs are available at:

- **Swagger UI**: `/api/v1/docs`
- **ReDoc**: `/api/v1/redoc`

### Downloading the spec

```bash
# Unauthenticated — the spec endpoint doesn't require auth
curl -o openapi.json https://your-instance.azurecontainerapps.io/api/v1/openapi.json
```

### Generating a client

Use any OpenAPI code generator. Examples:

**Python (openapi-python-client)**:
```bash
pip install openapi-python-client
openapi-python-client generate --url https://your-instance/api/v1/openapi.json
```

**TypeScript (openapi-typescript)**:
```bash
npx openapi-typescript https://your-instance/api/v1/openapi.json -o schema.d.ts
```

**Any language (openapi-generator)**:
```bash
npx @openapitools/openapi-generator-cli generate \
  -i https://your-instance/api/v1/openapi.json \
  -g python \
  -o ./generated-client
```

The generated client will automatically include the OAuth2 security scheme
definitions, so most generators will scaffold auth helpers out of the box.

---

## Endpoint Quick Reference

See the full endpoint list and permission mapping in
[plan/11 §1.4 and §2.1](plan/11-external-api-and-auth.md).

| Method | Endpoint | Permission |
|---|---|---|
| `POST` | `/api/v1/review` | `Review.Execute` |
| `GET` | `/api/v1/reviews` | `Dashboard.Read` |
| `GET` | `/api/v1/reviews/{id}` | `Dashboard.Read` |
| `GET` | `/api/v1/reviews/{id}/export?format=json\|csv` | `Dashboard.Read` |
| `DELETE` | `/api/v1/reviews/{id}` | `Review.Execute` |
| `GET` | `/api/v1/active` | `Dashboard.Read` |
| `POST` | `/api/v1/scan/recent` | `Scan.Execute` |
| `POST` | `/api/v1/scan/maintenance` | `Scan.Execute` |
| `GET` | `/api/v1/scans` | `Dashboard.Read` |
| `GET` | `/api/v1/scans/stats` | `Dashboard.Read` |
| `GET` | `/api/v1/scans/{id}` | `Dashboard.Read` |
| `GET` | `/api/v1/scans/{id}/export?format=json\|csv` | `Dashboard.Read` |
| `GET` | `/api/v1/stats` | `Dashboard.Read` |
| `GET` | `/api/v1/events` | `Dashboard.Read` |
| `GET` | `/api/v1/settings` | `Dashboard.Read` |
| `PUT` | `/api/v1/settings` | `Settings.Write` |
| `GET` | `/api/v1/prompts` | `Dashboard.Read` |
| `PUT` | `/api/v1/prompts/{name}` | `Settings.Write` |
| `DELETE` | `/api/v1/prompts/{name}` | `Settings.Write` |
| `GET` | `/api/v1/auth/config` | None (public) |
| `GET` | `/api/health` | None (public) |
