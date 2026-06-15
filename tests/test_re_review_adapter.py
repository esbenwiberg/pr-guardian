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


async def test_ado_re_review_without_connection_uses_env_fallback(monkeypatch):
    """Legacy reviews with no recorded connection still fall back to env."""
    review = _ado_review(None)
    sentinel = object()
    create_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    adapter = await factory.create_adapter_for_review(review, "ado")

    assert adapter is sentinel
    create_adapter.assert_called_once_with("ado")


async def test_ado_connection_without_token_falls_back_to_env(monkeypatch):
    """A connection that yields no token must not silently emit a blank-PAT
    adapter via the override path — fall through to the env-keyed adapter."""
    cid = uuid.uuid4()
    review = _ado_review(str(cid))
    monkeypatch.setattr(storage, "get_connection_token", AsyncMock(return_value=""))
    create_adapter = MagicMock(return_value=object())
    monkeypatch.setattr(factory, "create_adapter", create_adapter)

    await factory.create_adapter_for_review(review, "ado")

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
