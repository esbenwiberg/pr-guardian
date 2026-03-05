from __future__ import annotations

import structlog
from fastapi import FastAPI

from pr_guardian.api.health_api import router as health_router
from pr_guardian.api.webhooks import router as webhooks_router

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

app = FastAPI(
    title="PR Guardian",
    description="Automated PR review pipeline",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(webhooks_router)


@app.get("/")
async def root():
    return {
        "service": "PR Guardian",
        "version": "0.1.0",
        "docs": "/docs",
    }
