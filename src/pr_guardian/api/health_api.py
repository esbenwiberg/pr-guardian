from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "pr-guardian",
        "version": "0.1.0",
    }


@router.get("/me")
async def whoami(request: Request):
    """Return the caller's identity. Used by sidebar.js to admin-gate the Settings nav item."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        return {
            "kind": "anonymous",
            "email": None,
            "is_admin": False,
            "can_manage_profiles": False,
        }
    return {
        "kind": identity.kind,
        "email": identity.email,
        "is_admin": bool(identity.is_admin),
        "can_manage_profiles": bool(
            identity.kind != "api_key" and (identity.is_admin or identity.can_manage_profiles)
        ),
    }
