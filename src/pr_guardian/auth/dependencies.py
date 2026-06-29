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


async def require_human_signed_in(request: Request) -> Identity:
    """Require a signed-in human user. API keys cannot perform human actions."""
    identity = _get_identity(request)
    if identity.kind == "anonymous":
        raise HTTPException(401, "Signed-in access required")
    if identity.kind == "api_key":
        raise HTTPException(403, "Human user required")
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


async def require_write(request: Request) -> Identity:
    """Require an authenticated principal that can write.

    Unlike ``require_write_scope`` (which only rejects under-scoped API keys and
    silently lets anonymous through), this also blocks anonymous callers — so
    it's safe on endpoints exposed to CI: a signed-in user passes, a write-scoped
    API key passes, everything else is rejected.

    Anonymous-but-admin is allowed: that's only ever the local dev / no-DB
    fallback (GUARDIAN_DEV_ADMIN or no DATABASE_URL), never a production caller.
    """
    identity = _get_identity(request)
    if identity.kind == "anonymous" and not identity.is_admin:
        raise HTTPException(401, "Signed-in or API-key access required")
    if identity.kind == "api_key" and "write" not in identity.scopes:
        raise HTTPException(403, "API key missing required scope: write")
    return identity
