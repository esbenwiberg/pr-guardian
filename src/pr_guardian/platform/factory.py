from __future__ import annotations

import uuid
from typing import Any, Mapping

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
            matched = [
                c
                for c in await storage.list_connections()
                if c.get("name") == connection_id_or_name
                and c.get("platform") == "github"
                and c.get("auth_kind") == "github_app"
            ]
            if matched:
                connection = matched[0]
        if connection is None:
            raise ValueError(
                f"No GitHub App Connection found with id or name {connection_id_or_name!r}. "
                "GITHUB_TOKEN env fallback has been removed."
            )
    else:
        app_connections = [
            c
            for c in await storage.list_connections()
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


async def create_adapter_for_review(
    review: Mapping[str, Any], platform_name: str
) -> PlatformAdapter:
    """Resolve an adapter using the Connection the review originally ran against.

    Re-review, re-evaluate, and on-demand diff/capability fetches must
    authenticate with the *same* Connection the review used — NOT the
    ``ADO_PAT`` / ``ADO_ORG_URL`` env fallback. Deployments that authenticate
    solely via stored Connections leave those env vars empty, so falling back
    to ``create_adapter(platform)`` builds an ADO adapter with a blank PAT and
    every API call 401s.

    GitHub is resolved through the standard GitHub App Connection path
    (``create_github_adapter``), keyed by the review's stored connection id.
    ADO prefers the stored Connection's PAT + org_url, and only falls back to
    env vars for legacy reviews that recorded no connection.
    """
    connection_id = review.get("connection_id")
    if platform_name == "github":
        return await create_github_adapter(connection_id or review.get("pat_name"))

    if connection_id:
        from pr_guardian.persistence import storage

        cid = uuid.UUID(str(connection_id))
        token = await storage.get_connection_token(cid)
        if token:
            connection = await storage.get_connection(cid)
            snapshot = review.get("connection_snapshot") or {}
            org_url = (connection or {}).get("org_url") or snapshot.get("org_url") or ""
            return create_adapter(
                platform_name,
                token_override=token,
                org_url_override=org_url or None,
            )

    return create_adapter(platform_name)


def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
    """Normalize webhook payload to PlatformPR using the right adapter."""
    if payload.platform == "github":
        return GitHubAdapter.normalize_webhook(payload)
    if payload.platform == "ado":
        return ADOAdapter.normalize_webhook(payload)
    return None
