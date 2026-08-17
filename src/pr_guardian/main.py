from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pr_guardian.api.admin import router as admin_router
from pr_guardian.api.agent_api import router as agent_router
from pr_guardian.api.dashboard import router as dashboard_api_router
from pr_guardian.api.dashboard_page import router as dashboard_page_router
from pr_guardian.api.health_api import router as health_router
from pr_guardian.api.pr_dashboard_api import router as pr_dashboard_router
from pr_guardian.api.profiles import router as profiles_router
from pr_guardian.api.review import router as review_router
from pr_guardian.api.reviews_queue import router as reviews_queue_router
from pr_guardian.api.scans import router as scans_router
from pr_guardian.api.webhooks import router as webhooks_router
from pr_guardian.auth.identity import IdentityMiddleware


def _configure_logging() -> None:
    """Configure structlog for the hosted process.

    Two things matter here beyond formatting, both learned from a production
    incident where readiness probes timed out against ``/api/health`` — an
    endpoint that returns a static dict and touches nothing. The only way that
    times out is a blocked event loop, and unfiltered debug logging was a
    contributor: writes to stdout are synchronous, so once the platform's log
    collector applies backpressure every log call stalls the loop that is
    supposed to be answering the probe.

    * **Level filtering.** Without a ``wrapper_class`` structlog emits every
      level, so per-repo ``log.debug`` calls from the PR-sync walk were being
      rendered and written in production (tens of thousands of lines a day).
      Default to INFO; ``GUARDIAN_LOG_LEVEL`` re-enables debug when explicitly
      needed.
    * **No ANSI when not a terminal.** ``ConsoleRenderer`` colourises by default,
      which is noise in a log sink and actively breaks querying: the escape codes
      around a token defeat Log Analytics term tokenisation, so ``Log_s has
      'pr_sync_start'`` silently matches nothing.
    """
    level_name = os.environ.get("GUARDIAN_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


_configure_logging()

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown: initialise DB, clean up on exit."""
    if os.environ.get("DATABASE_URL") or os.environ.get("GUARDIAN_DB_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        from pr_guardian.persistence.database import close_db, init_db

        await init_db()
        log.info("db_ready")
        # Seed initial admin (idempotent)
        try:
            from pr_guardian.persistence import storage

            await storage.add_admin("ewi@projectum.com", added_by="system")
        except Exception as e:
            log.debug("admin_seed_skipped", error=str(e))
        # Bridge the SSE event bus across replicas (Postgres NOTIFY). No-op on
        # sqlite/single-process; never blocks startup if it can't connect.
        from pr_guardian.core.event_bridge import bridge as event_bridge

        await event_bridge.start()
        # Start PR sync background loop
        import asyncio
        from pr_guardian.core.pr_sync import pr_sync_loop
        from pr_guardian.core.readiness_reconciler import readiness_reconciler_loop

        sync_task = asyncio.create_task(pr_sync_loop())
        readiness_task = asyncio.create_task(readiness_reconciler_loop())
        yield
        await event_bridge.stop()
        for task in (sync_task, readiness_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        from pr_guardian.persistence.leader_lock import dispose_lock_engine

        await dispose_lock_engine()
        await close_db()
    else:
        log.info(
            "db_disabled", hint="Set DATABASE_URL or GUARDIAN_DB_ENABLED=1 to enable persistence"
        )
        yield


app = FastAPI(
    title="PR Guardian",
    description="Automated PR review pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(IdentityMiddleware)

app.include_router(health_router)
app.include_router(review_router)
app.include_router(reviews_queue_router)
app.include_router(webhooks_router)
app.include_router(scans_router)
app.include_router(dashboard_api_router)
app.include_router(dashboard_page_router)
app.include_router(pr_dashboard_router)
app.include_router(profiles_router)
app.include_router(admin_router)
app.include_router(agent_router)

_STATIC_DIR = Path(__file__).resolve().parent / "dashboard" / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


_PROTOTYPES_DIR = Path(__file__).resolve().parent.parent.parent / "prototypes"
if _PROTOTYPES_DIR.is_dir():
    app.mount(
        "/prototypes",
        StaticFiles(directory=str(_PROTOTYPES_DIR), html=True),
        name="prototypes",
    )
