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


app = FastAPI(
    title="PR Guardian",
    description="Automated PR review pipeline",
    version="0.2.0",
    lifespan=lifespan,
)

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
