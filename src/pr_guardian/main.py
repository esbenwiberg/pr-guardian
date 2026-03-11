from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from pr_guardian.api.compat import router as compat_router
from pr_guardian.api.dashboard_page import router as dashboard_page_router
from pr_guardian.api.health_api import router as health_router
from pr_guardian.api.v1 import router as v1_router
from pr_guardian.api.webhooks import router as webhooks_router

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown: Key Vault → Entra auth → DB."""

    # 1. Key Vault (optional — secrets fall back to env vars when unset)
    from pr_guardian.auth.keyvault import init_keyvault
    await init_keyvault()

    # 2. Entra ID auth (optional — disabled in dev mode)
    from pr_guardian.auth.entra import init_entra_auth
    await init_entra_auth()

    # 3. Database (optional)
    if os.environ.get("DATABASE_URL") or os.environ.get("GUARDIAN_DB_ENABLED", "").lower() in (
        "1", "true", "yes",
    ):
        from pr_guardian.persistence.database import close_db, init_db
        await init_db()
        log.info("db_ready")
        yield
        await close_db()
    else:
        log.info("db_disabled", hint="Set DATABASE_URL or GUARDIAN_DB_ENABLED=1 to enable persistence")
        yield


_ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
_ENTRA_API_CLIENT_ID = os.environ.get("ENTRA_API_CLIENT_ID", "")

# OpenAPI security scheme (informational — tells generated clients about Bearer auth)
_openapi_security: dict | list = []
if _ENTRA_TENANT_ID and _ENTRA_API_CLIENT_ID:
    _openapi_security = [{"EntraID": []}]

app = FastAPI(
    title="PR Guardian",
    description="Automated PR review pipeline with Entra ID auth",
    version="0.2.0",
    lifespan=lifespan,
    swagger_ui_init_oauth={
        "clientId": _ENTRA_API_CLIENT_ID,
        "scopes": f"api://{_ENTRA_API_CLIENT_ID}/.default",
    } if _ENTRA_API_CLIENT_ID else None,
)

# Add OAuth2 security scheme to OpenAPI spec when auth is configured
if _ENTRA_TENANT_ID and _ENTRA_API_CLIENT_ID:
    from fastapi.openapi.utils import get_openapi

    def _custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["components"] = schema.get("components", {})
        schema["components"]["securitySchemes"] = {
            "EntraID": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": f"https://login.microsoftonline.com/{_ENTRA_TENANT_ID}/oauth2/v2.0/authorize",
                        "tokenUrl": f"https://login.microsoftonline.com/{_ENTRA_TENANT_ID}/oauth2/v2.0/token",
                        "scopes": {
                            f"api://{_ENTRA_API_CLIENT_ID}/Review.Execute": "Trigger PR reviews",
                            f"api://{_ENTRA_API_CLIENT_ID}/Scan.Execute": "Trigger scans",
                            f"api://{_ENTRA_API_CLIENT_ID}/Dashboard.Read": "Read dashboard data",
                            f"api://{_ENTRA_API_CLIENT_ID}/Settings.Write": "Modify settings",
                        },
                    },
                },
            },
        }
        schema["security"] = [{"EntraID": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi

# --- Versioned API (auth-protected) ---
app.include_router(v1_router)

# --- Unauthenticated endpoints ---
app.include_router(health_router)
app.include_router(webhooks_router)

# --- Backward compat redirects (old /api/* → /api/v1/*) ---
app.include_router(compat_router)

# --- Dashboard HTML pages (served unauthenticated — auth gate is on API calls) ---
app.include_router(dashboard_page_router)

_STATIC_DIR = Path(__file__).resolve().parent / "dashboard" / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")
