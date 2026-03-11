"""Permission checking dependencies for API endpoints.

Each endpoint declares the permission it requires (e.g. ``Review.Execute``).
The dependency checks:
- Delegated tokens: the ``scp`` claim contains the scope
- App-only tokens: the ``roles`` claim contains the role

When auth is disabled (dev mode), all requests pass through.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from pr_guardian.auth.entra import AUTH_ENABLED, TokenClaims, _validate_token


def require_permission(permission: str):
    """FastAPI dependency factory that enforces a specific permission.

    Usage::

        @router.get("/reviews", dependencies=[Depends(require_permission("Dashboard.Read"))])
        async def list_reviews(): ...

    Or to access the claims::

        @router.post("/review")
        async def trigger_review(claims: TokenClaims = Depends(require_permission("Review.Execute"))):
            ...
    """

    async def _check(
        claims: TokenClaims | None = Depends(_validate_token),
    ) -> TokenClaims | None:
        if not AUTH_ENABLED or claims is None:
            return None

        # Strip the API URI prefix — accept both "Review.Execute" and
        # "api://<id>/Review.Execute"
        short = permission.split("/")[-1] if "/" in permission else permission

        # Delegated: check scp claim
        if claims.scopes:
            normalised = [s.split("/")[-1] for s in claims.scopes]
            if short in normalised:
                return claims

        # App-only: check roles claim
        if claims.roles:
            normalised = [r.split("/")[-1] for r in claims.roles]
            if short in normalised:
                return claims

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {permission}",
        )

    return _check
