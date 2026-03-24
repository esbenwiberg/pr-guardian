"""FastAPI dependencies for authorization checks."""
from __future__ import annotations

from fastapi import HTTPException, Request

from pr_guardian.auth.identity import Identity


def _get_identity(request: Request) -> Identity:
    """Read the identity set by IdentityMiddleware."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(500, "Identity middleware not configured")
    return identity


async def require_admin(request: Request) -> Identity:
    """Require admin role. Returns 403 if not admin."""
    identity = _get_identity(request)
    if not identity.is_admin:
        raise HTTPException(403, "Admin access required")
    return identity


async def require_write_scope(request: Request) -> Identity:
    """Require write scope. Users always pass; API keys need 'write' scope."""
    identity = _get_identity(request)
    if identity.kind == "api_key" and "write" not in identity.scopes:
        raise HTTPException(403, "API key missing required scope: write")
    return identity
