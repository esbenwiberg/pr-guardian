"""Azure DevOps service principal authentication via MSAL.

When ADO_CLIENT_ID, ADO_TENANT_ID, and ADO_CLIENT_SECRET are set,
PR Guardian authenticates to Azure DevOps using a service principal
(Entra ID client-credentials flow) instead of a personal access token.

Benefits:
- Org-owned identity (not tied to a user account)
- Token rotation handled by MSAL (~1 hour lifetime, auto-cached)
- Audit trail via Entra ID sign-in logs
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()

# The well-known resource ID for Azure DevOps
_ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


class ADOServicePrincipalAuth:
    """Manages Azure DevOps access tokens via MSAL client-credentials flow."""

    def __init__(self, client_id: str, tenant_id: str, client_secret: str):
        import msal

        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        self._client_id = client_id
        log.info("ado_sp_auth_ready", client_id=client_id)

    def get_token(self) -> str:
        """Acquire an access token for Azure DevOps.

        MSAL handles caching internally — repeated calls return the cached
        token until it's near expiry, then silently refresh.

        Raises:
            RuntimeError: If token acquisition fails.
        """
        result = self._app.acquire_token_for_client(scopes=[_ADO_SCOPE])

        if "access_token" in result:
            return result["access_token"]

        error = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"ADO SP token acquisition failed: {error}")
