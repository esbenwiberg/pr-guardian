"""Identity resolution middleware.

Resolves every request to a unified Identity by checking (in order):
1. Authorization: Bearer prg_* header → API key auth
2. X-MS-CLIENT-PRINCIPAL-NAME header → Easy Auth (Entra ID)
3. Anonymous fallback (admin in local dev, non-admin otherwise)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = structlog.get_logger()

# Paths that skip identity resolution entirely
_SKIP_PREFIXES = ("/api/health", "/api/webhooks/", "/static/")


@dataclass
class Identity:
    """Unified identity for all auth methods."""

    kind: str  # "user", "api_key", "anonymous"
    email: str | None = None
    key_id: str | None = None
    key_name: str | None = None
    scopes: list[str] = field(default_factory=list)
    is_admin: bool = False

    @property
    def display_name(self) -> str:
        if self.kind == "api_key":
            return f"api_key:{self.key_name or self.key_id or '?'}"
        if self.email:
            return self.email
        return "anonymous"


def _db_available() -> bool:
    return bool(
        os.environ.get("DATABASE_URL")
        or os.environ.get("GUARDIAN_DB_ENABLED", "").lower() in ("1", "true", "yes")
    )


def _dev_admin_mode() -> bool:
    """Force anonymous identity to be admin (dev/sandbox validation only)."""
    return os.environ.get("GUARDIAN_DEV_ADMIN", "").lower() in ("1", "true", "yes")


class IdentityMiddleware(BaseHTTPMiddleware):
    """Resolve caller identity on every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip auth for health, webhooks, static assets
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            request.state.identity = Identity(kind="anonymous", is_admin=False)
            return await call_next(request)

        identity = await self._resolve(request)
        request.state.identity = identity
        return await call_next(request)

    async def _resolve(self, request: Request) -> Identity:
        auth_header = request.headers.get("authorization", "")

        # 1. API key auth
        if auth_header.lower().startswith("bearer prg_"):
            raw_key = auth_header.split(" ", 1)[1]
            return await self._resolve_api_key(raw_key)

        # 2. Easy Auth (Entra ID)
        email = request.headers.get("x-ms-client-principal-name", "").strip()
        if email:
            return await self._resolve_user(email)

        # 3. Anonymous fallback
        if not _db_available() or _dev_admin_mode():
            # No DB, or GUARDIAN_DEV_ADMIN=1 — treat anonymous as admin
            return Identity(kind="anonymous", is_admin=True)

        return Identity(kind="anonymous", is_admin=False)

    async def _resolve_api_key(self, raw_key: str) -> Identity:
        if not _db_available():
            return Identity(kind="anonymous", is_admin=True)

        try:
            from pr_guardian.persistence import storage

            key_data = await storage.validate_api_key(raw_key)
        except Exception as e:
            log.warning("api_key_validation_error", error=str(e))
            raise _unauthorized("API key validation failed")

        if not key_data:
            raise _unauthorized("Invalid or expired API key")

        # API keys with write scope on admin endpoints need admin check
        is_admin = await self._check_admin_by_email(key_data.get("created_by", ""))

        return Identity(
            kind="api_key",
            key_id=key_data["id"],
            key_name=key_data["name"],
            scopes=key_data.get("scopes", ["read"]),
            is_admin=is_admin,
        )

    async def _resolve_user(self, email: str) -> Identity:
        is_admin = await self._check_admin_by_email(email)
        return Identity(kind="user", email=email, is_admin=is_admin)

    async def _check_admin_by_email(self, email: str) -> bool:
        if not email or not _db_available():
            return not _db_available()  # admin in local dev
        try:
            from pr_guardian.persistence import storage

            return await storage.is_admin(email)
        except Exception:
            return False


def _unauthorized(detail: str):
    """Return an exception that the middleware can raise to short-circuit."""
    from starlette.exceptions import HTTPException

    return HTTPException(status_code=401, detail=detail)
