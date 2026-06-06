from __future__ import annotations

from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.models.pr import PlatformPR


def create_adapter(
    platform: str,
    *,
    token_override: str | None = None,
    org_url_override: str | None = None,
) -> PlatformAdapter:
    """Create the appropriate platform adapter.

    For GitHub, ``token_override`` must be supplied explicitly; there is no
    GITHUB_TOKEN env fallback — callers must resolve a GitHub App Connection.
    Use ``build_github_adapter_from_connection()`` from ``github_auth`` for
    the standard runtime path.

    For ADO, ``token_override`` / ``org_url_override`` can fall back to env
    vars (``ADO_PAT``, ``ADO_ORG_URL``).
    """
    import os

    if platform == "github":
        token = token_override if token_override is not None else ""
        return GitHubAdapter(token=token)

    if platform == "ado":
        pat = token_override if token_override is not None else os.environ.get("ADO_PAT", "")
        org_url = (
            org_url_override if org_url_override is not None else os.environ.get("ADO_ORG_URL", "")
        )
        return ADOAdapter(pat=pat, org_url=org_url)

    raise ValueError(f"Unknown platform: {platform}")


async def create_github_adapter(connection_id_or_name: str | None = None) -> GitHubAdapter:
    """Create a GitHubAdapter from a stored GitHub App Connection.

    Raises ``ValueError`` if no suitable GitHub App Connection can be found.
    Does NOT fall back to ``GITHUB_TOKEN``.
    """
    from pr_guardian.persistence import storage
    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    connection: dict | None = None
    if connection_id_or_name:
        import uuid

        try:
            uid = uuid.UUID(connection_id_or_name)
            connection = await storage.get_connection(uid)
        except ValueError:
            # Not a UUID — search by name among GitHub App connections
            all_connections = await storage.list_connections()
            matched = [
                c
                for c in all_connections
                if c.get("name") == connection_id_or_name
                and c.get("platform") == "github"
                and c.get("auth_kind") == "github_app"
            ]
            if matched:
                connection = matched[0]

    if connection is None:
        connections = await storage.list_connections()
        app_connections = [
            c
            for c in connections
            if c.get("platform") == "github" and c.get("auth_kind") == "github_app"
        ]
        if not app_connections:
            raise ValueError(
                "No GitHub App Connection found. "
                "GITHUB_TOKEN env fallback has been removed — "
                "add a GitHub App Connection via the Connections UI."
            )
        connection = app_connections[0]

    return await build_github_adapter_from_connection(connection)


def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
    """Normalize webhook payload to PlatformPR using the right adapter."""
    if payload.platform == "github":
        return GitHubAdapter.normalize_webhook(payload)
    if payload.platform == "ado":
        return ADOAdapter.normalize_webhook(payload)
    return None
