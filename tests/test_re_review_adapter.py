"""Regression tests for adapter resolution on re-review / re-evaluate.

The bug: re-review built an ADO adapter via ``create_adapter("ado")``, which
only reads ``ADO_PAT`` / ``ADO_ORG_URL`` from the environment. In a
Connection-only deployment those env vars are empty, so the ADO adapter sent a
``Basic base64(":")`` header and every PR fetch 401'd — even though the
original review authenticated fine via its stored Connection.

``create_adapter_for_review`` must reuse the Connection the review ran against.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from pr_guardian.persistence import storage
from pr_guardian.platform import factory


def _ado_review(connection_id: str | None, *, snapshot: dict | None = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "platform": "ado",
        "connection_id": connection_id,
        "connection_snapshot": snapshot,
        "pr_url": "https://dev.azure.com/org/Proj/_git/Repo/pullrequest/14213",
    }


async def test_ado_re_review_uses_stored_connection_pat(monkeypatch):
    """connection_id present → resolve the stored PAT + org_url, NOT env vars."""
    cid = uuid.uuid4()
    review = _ado_review(str(cid))

    monkeypatch.setattr(storage, "get_connection_token", AsyncMock(return_value="stored-pat"))
    monkeypatch.setattr(
        storage,
        "get_connection",
        AsyncMock(return_value={"id": str(cid), "org_url": "https://dev.azure.com/org"}),
    )
    sentinel = object()
    create_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    adapter = await factory.create_adapter_for_review(review, "ado")

    assert adapter is sentinel
    create_adapter.assert_called_once_with(
        "ado",
        token_override="stored-pat",
        org_url_override="https://dev.azure.com/org",
    )


async def test_ado_re_review_falls_back_to_snapshot_org_url(monkeypatch):
    """When the live Connection has no org_url, the review's snapshot wins."""
    cid = uuid.uuid4()
    review = _ado_review(str(cid), snapshot={"org_url": "https://dev.azure.com/snap"})

    monkeypatch.setattr(storage, "get_connection_token", AsyncMock(return_value="stored-pat"))
    monkeypatch.setattr(
        storage, "get_connection", AsyncMock(return_value={"id": str(cid), "org_url": ""})
    )
    create_adapter = MagicMock(return_value=object())
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    await factory.create_adapter_for_review(review, "ado")

    create_adapter.assert_called_once_with(
        "ado", token_override="stored-pat", org_url_override="https://dev.azure.com/snap"
    )


async def test_ado_no_connection_no_org_match_uses_env_fallback(monkeypatch):
    """Legacy reviews with no connection AND no org-matching Connection fall
    back to env (env-only deployments keep working)."""
    review = _ado_review(None)
    # An ADO connection exists, but for a different org — must not match.
    monkeypatch.setattr(
        storage,
        "list_connections",
        AsyncMock(
            return_value=[
                {
                    "id": str(uuid.uuid4()),
                    "platform": "ado",
                    "org_url": "https://dev.azure.com/other",
                }
            ]
        ),
    )
    sentinel = object()
    create_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    adapter = await factory.create_adapter_for_review(review, "ado")

    assert adapter is sentinel
    create_adapter.assert_called_once_with("ado")


async def test_ado_no_connection_resolves_live_connection_by_org_url(monkeypatch):
    """The fix: a manual/legacy ADO review with connection_id=None resolves a
    healthy Connection covering its org — NOT the stale ADO_PAT env var."""
    review = _ado_review(None)  # pr_url org = "org" → https://dev.azure.com/org
    matching_id = uuid.uuid4()
    monkeypatch.setattr(
        storage,
        "list_connections",
        AsyncMock(
            return_value=[
                {
                    "id": str(matching_id),
                    "platform": "ado",
                    "org_url": "https://dev.azure.com/org",
                    "health_status": "healthy",
                    "sync_enabled": True,
                    "is_default": False,
                    "updated_at": "2026-07-02T18:23:07+00:00",
                }
            ]
        ),
    )
    monkeypatch.setattr(storage, "get_connection_token", AsyncMock(return_value="live-pat"))
    sentinel = object()
    create_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    adapter = await factory.create_adapter_for_review(review, "ado")

    assert adapter is sentinel
    create_adapter.assert_called_once_with(
        "ado",
        token_override="live-pat",
        org_url_override="https://dev.azure.com/org",
    )


async def test_ado_connection_without_token_resolves_by_org_url(monkeypatch):
    """A stamped connection that yields no token must not emit a blank-PAT
    adapter; it now tries org-URL resolution before the env fallback."""
    cid = uuid.uuid4()
    review = _ado_review(str(cid))
    matching_id = uuid.uuid4()

    async def _token(resolved_id):
        # The stamped (dead) connection has no token; the org-matched one does.
        return "" if resolved_id == cid else "live-pat"

    monkeypatch.setattr(storage, "get_connection_token", AsyncMock(side_effect=_token))
    monkeypatch.setattr(
        storage,
        "list_connections",
        AsyncMock(
            return_value=[
                {
                    "id": str(matching_id),
                    "platform": "ado",
                    "org_url": "https://dev.azure.com/org",
                    "health_status": "healthy",
                }
            ]
        ),
    )
    create_adapter = MagicMock(return_value=object())
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    await factory.create_adapter_for_review(review, "ado")

    create_adapter.assert_called_once_with(
        "ado", token_override="live-pat", org_url_override="https://dev.azure.com/org"
    )


async def test_ado_storage_failure_during_resolution_falls_back_to_env(monkeypatch):
    """A storage error while resolving a Connection must degrade to the env
    fallback, never break adapter creation for the verdict post."""
    review = _ado_review(None)
    monkeypatch.setattr(
        storage, "list_connections", AsyncMock(side_effect=RuntimeError("db down"))
    )
    sentinel = object()
    create_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    adapter = await factory.create_adapter_for_review(review, "ado")

    assert adapter is sentinel
    create_adapter.assert_called_once_with("ado")


async def test_github_re_review_resolves_stored_connection(monkeypatch):
    """GitHub re-review keys the App-connection lookup off the stored
    connection id (falling back to pat_name), not the first connection found."""
    cid = uuid.uuid4()
    review = {
        "id": str(uuid.uuid4()),
        "platform": "github",
        "connection_id": str(cid),
        "pr_url": "https://github.com/org/repo/pull/42",
    }
    sentinel = object()
    create_github_adapter = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_github_adapter", create_github_adapter)

    adapter = await factory.create_adapter_for_review(review, "github")

    assert adapter is sentinel
    create_github_adapter.assert_awaited_once_with(str(cid))
