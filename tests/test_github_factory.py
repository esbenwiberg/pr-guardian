"""Tests for create_github_adapter resolution logic — all three lookup paths."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_APP_CONN = {
    "id": str(uuid.uuid4()),
    "name": "Test App Connection",
    "platform": "github",
    "auth_kind": "github_app",
    "app_id": "12345",
    "installation_id": "98765",
}


@pytest.mark.asyncio
async def test_create_github_adapter_uuid_path_returns_adapter():
    """UUID path: valid UUID → storage.get_connection → build_github_adapter_from_connection."""
    from pr_guardian.platform.factory import create_github_adapter

    conn_id = str(uuid.uuid4())
    conn = {**_APP_CONN, "id": conn_id}
    fake_adapter = MagicMock()

    with (
        patch(
            "pr_guardian.persistence.storage.get_connection",
            AsyncMock(return_value=conn),
        ),
        patch(
            "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
            AsyncMock(return_value=fake_adapter),
        ),
    ):
        result = await create_github_adapter(conn_id)

    assert result is fake_adapter


@pytest.mark.asyncio
async def test_create_github_adapter_uuid_not_found_raises():
    """UUID path: valid UUID but get_connection returns None → ValueError."""
    from pr_guardian.platform.factory import create_github_adapter

    conn_id = str(uuid.uuid4())

    with patch(
        "pr_guardian.persistence.storage.get_connection",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="No GitHub App Connection found"):
            await create_github_adapter(conn_id)


@pytest.mark.asyncio
async def test_create_github_adapter_name_path_returns_adapter():
    """Name path: non-UUID string matched by name in list_connections → adapter returned."""
    from pr_guardian.platform.factory import create_github_adapter

    conn = {**_APP_CONN, "name": "My GitHub App"}
    fake_adapter = MagicMock()

    with (
        patch(
            "pr_guardian.persistence.storage.list_connections",
            AsyncMock(return_value=[conn]),
        ),
        patch(
            "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
            AsyncMock(return_value=fake_adapter),
        ),
    ):
        result = await create_github_adapter("My GitHub App")

    assert result is fake_adapter


@pytest.mark.asyncio
async def test_create_github_adapter_name_not_found_raises():
    """Name path: non-UUID string, no name match in list_connections → ValueError."""
    from pr_guardian.platform.factory import create_github_adapter

    conn = {**_APP_CONN, "name": "Different Name"}

    with patch(
        "pr_guardian.persistence.storage.list_connections",
        AsyncMock(return_value=[conn]),
    ):
        with pytest.raises(ValueError, match="No GitHub App Connection found"):
            await create_github_adapter("My GitHub App")


@pytest.mark.asyncio
async def test_create_github_adapter_no_arg_uses_first_app_connection():
    """No-arg path: no connection_id_or_name → first App connection from list_connections."""
    from pr_guardian.platform.factory import create_github_adapter

    conn = {**_APP_CONN}
    fake_adapter = MagicMock()

    with (
        patch(
            "pr_guardian.persistence.storage.list_connections",
            AsyncMock(return_value=[conn]),
        ),
        patch(
            "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
            AsyncMock(return_value=fake_adapter),
        ),
    ):
        result = await create_github_adapter()

    assert result is fake_adapter


@pytest.mark.asyncio
async def test_create_github_adapter_no_arg_no_app_connections_raises():
    """No-arg path: no App connections in list_connections → ValueError."""
    from pr_guardian.platform.factory import create_github_adapter

    non_app_conn = {**_APP_CONN, "auth_kind": None}

    with patch(
        "pr_guardian.persistence.storage.list_connections",
        AsyncMock(return_value=[non_app_conn]),
    ):
        with pytest.raises(ValueError, match="No GitHub App Connection found"):
            await create_github_adapter()
