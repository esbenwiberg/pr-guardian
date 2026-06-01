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
    """Require a human admin user. API keys cannot administer product settings."""
    identity = _get_identity(request)
    if identity.kind == "api_key" or not identity.is_admin:
        raise HTTPException(403, "Admin access required")
    return identity


async def require_human_admin(request: Request) -> Identity:
    """Alias for routes that want to document the human-admin requirement explicitly."""
    return await require_admin(request)


async def require_signed_in(request: Request) -> Identity:
    """Require a signed-in user, admin, or API key identity."""
    identity = _get_identity(request)
    if identity.kind == "anonymous":
        raise HTTPException(401, "Signed-in access required")
    return identity


async def require_profile_manager(request: Request) -> Identity:
    """Require admin or Profile Manager access. API keys are never accepted."""
    identity = _get_identity(request)
    if identity.kind == "api_key" or not (identity.is_admin or identity.can_manage_profiles):
        raise HTTPException(403, "Profile Manager access required")
    return identity


async def require_write_scope(request: Request) -> Identity:
    """Require write scope. Users always pass; API keys need 'write' scope."""
    identity = _get_identity(request)
    if identity.kind == "api_key" and "write" not in identity.scopes:
        raise HTTPException(403, "API key missing required scope: write")
    return identity
