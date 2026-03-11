"""Entra ID (Azure AD) JWT validation for the external API.

When ENTRA_TENANT_ID and ENTRA_API_CLIENT_ID are set, all /api/v1/* routes
require a valid Bearer token issued by Entra ID.

When those vars are unset the auth dependency is a no-op (dev mode) with a
warning logged at startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
_API_CLIENT_ID = os.environ.get("ENTRA_API_CLIENT_ID", "")

AUTH_ENABLED = bool(_TENANT_ID and _API_CLIENT_ID)

# Lazy-initialised; populated by init_entra_auth()
_azure_scheme = None  # SingleTenantAzureAuthorizationCodeBearer | None

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class TokenClaims:
    """Parsed claims from a validated Entra ID JWT."""

    oid: str  # Object ID (user or service principal)
    sub: str
    name: str  # preferred_username or appid
    scopes: list[str]  # delegated (scp claim)
    roles: list[str]  # application (roles claim)
    is_app_token: bool  # True when oid == sub (client-credentials flow)
    raw: dict  # Full decoded token for audit/debug


async def init_entra_auth() -> None:
    """Initialise the Entra ID OIDC validator.

    Call once at app startup (in the lifespan handler).
    """
    global _azure_scheme

    if not AUTH_ENABLED:
        log.warning(
            "entra_auth_disabled",
            hint="Set ENTRA_TENANT_ID and ENTRA_API_CLIENT_ID to enable",
        )
        return

    try:
        from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

        _azure_scheme = SingleTenantAzureAuthorizationCodeBearer(
            app_client_id=_API_CLIENT_ID,
            tenant_id=_TENANT_ID,
            scopes={
                f"api://{_API_CLIENT_ID}/Review.Execute": "Trigger and view PR reviews",
                f"api://{_API_CLIENT_ID}/Scan.Execute": "Trigger and view scans",
                f"api://{_API_CLIENT_ID}/Dashboard.Read": "Read dashboard data",
                f"api://{_API_CLIENT_ID}/Settings.Write": "Modify settings and prompts",
            },
        )
        await _azure_scheme.openid_config.load_config()
        log.info("entra_auth_ready", tenant=_TENANT_ID, client=_API_CLIENT_ID)
    except ImportError:
        log.warning(
            "entra_auth_deps_missing",
            hint="Install fastapi-azure-auth to enable Entra ID auth",
        )
    except Exception as exc:
        log.error("entra_auth_init_failed", error=str(exc))


async def _validate_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TokenClaims | None:
    """Core token validation dependency.

    Returns TokenClaims when auth is enabled, None when in dev mode.
    """
    if not AUTH_ENABLED or _azure_scheme is None:
        return None

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # fastapi-azure-auth validates signature, issuer, audience, expiry
        token = await _azure_scheme(request)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = token.dict() if hasattr(token, "dict") else dict(token)

    oid = claims.get("oid", "")
    sub = claims.get("sub", "")
    # Delegated tokens have scp (space-separated), app tokens have roles (list)
    raw_scp = claims.get("scp", "")
    scopes = raw_scp.split() if isinstance(raw_scp, str) else list(raw_scp)
    roles = claims.get("roles", [])
    if isinstance(roles, str):
        roles = [roles]

    return TokenClaims(
        oid=oid,
        sub=sub,
        name=claims.get("preferred_username", claims.get("appid", "")),
        scopes=scopes,
        roles=roles,
        is_app_token=(oid == sub),
        raw=claims,
    )
