from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from pr_guardian.api.dashboard import router as dashboard_api_router
from pr_guardian.api.dashboard_page import router as dashboard_page_router
from pr_guardian.api.health_api import router as health_router
from pr_guardian.api.review import router as review_router
from pr_guardian.api.scans import router as scans_router
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
    """Startup / shutdown: initialise DB, clean up on exit."""
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
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(review_router)
app.include_router(webhooks_router)
app.include_router(scans_router)
app.include_router(dashboard_api_router)
app.include_router(dashboard_page_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")
