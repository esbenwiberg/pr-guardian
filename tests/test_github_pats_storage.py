"""Tests for GitHub PAT storage and token resolution helpers."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from pr_guardian.persistence.crypto import decrypt, encrypt
from pr_guardian.persistence.storage import (
    create_github_pat,
    delete_github_pat,
    list_github_pats,
    resolve_github_token,
    update_github_pat,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pat_row(
    *,
    name: str = "test-pat",
    token: str = "ghp_abc123",
    description: str = "",
    is_default: bool = False,
    pat_id: uuid.UUID | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = pat_id or uuid.uuid4()
    row.name = name
    row.description = description
    row.encrypted_token = encrypt(token)
    row.token_prefix = token[:16] + "..." if len(token) > 16 else token
    row.is_default = is_default
    row.created_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    return row


def _session_cm(session: AsyncMock):
    """Wrap a mock session so it works as `async with async_session() as s:`."""
    @asynccontextmanager
    async def _factory():
        yield session
    return _factory


class _FailingCM:
    """Async context manager that raises on entry (simulates DB unavailability)."""
    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_):
        return False


def _failing_session(msg: str = "DB down"):
    """Return an async_session-compatible callable that raises on entry."""
    exc = RuntimeError(msg)
    return lambda: _FailingCM(exc)


# ---------------------------------------------------------------------------
# list_github_pats
# ---------------------------------------------------------------------------


async def test_list_github_pats_returns_dicts():
    row = _make_pat_row(name="org-a", token="ghp_x" * 4, is_default=True)
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = [row]
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        result = await list_github_pats()

    assert len(result) == 1
    assert result[0]["name"] == "org-a"
    assert result[0]["is_default"] is True
    assert "encrypted_token" not in result[0]


async def test_list_github_pats_db_failure_returns_empty():
    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        result = await list_github_pats()

    assert result == []


# ---------------------------------------------------------------------------
# create_github_pat
# ---------------------------------------------------------------------------


async def test_create_github_pat_encrypts_token():
    plain = "ghp_supersecret_abcdefghijklmnop"
    session = AsyncMock()
    session.execute = AsyncMock()
    captured_row = None

    def _cap(row):
        nonlocal captured_row
        captured_row = row

    session.add = MagicMock(side_effect=_cap)
    session.commit = AsyncMock()

    async def _refresh(row):
        row.created_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_refresh)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await create_github_pat(name="my-pat", token=plain, is_default=False)

    assert captured_row is not None
    assert captured_row.encrypted_token != plain
    assert decrypt(captured_row.encrypted_token) == plain


async def test_create_github_pat_sets_token_prefix():
    token = "ghp_abcdefghijklmnopqrstuvwxyz"  # > 16 chars
    session = AsyncMock()
    session.execute = AsyncMock()
    captured = None

    def _cap(row):
        nonlocal captured
        captured = row

    session.add = MagicMock(side_effect=_cap)
    session.commit = AsyncMock()

    async def _refresh(row):
        row.created_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_refresh)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await create_github_pat(name="pat", token=token, is_default=False)

    assert captured.token_prefix == token[:16] + "..."


async def test_create_github_pat_is_default_clears_others():
    """is_default=True must execute an UPDATE to clear other defaults before inserting."""
    session = AsyncMock()
    executed = []
    session.execute = AsyncMock(side_effect=lambda s: executed.append(s))
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _refresh(row):
        row.created_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_refresh)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await create_github_pat(name="new-default", token="ghp_x", is_default=True)

    assert len(executed) >= 1, "Expected UPDATE to clear other defaults"


async def test_create_github_pat_non_default_skips_clear():
    """is_default=False must NOT execute the mass-clear UPDATE."""
    session = AsyncMock()
    executed = []
    session.execute = AsyncMock(side_effect=lambda s: executed.append(s))
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _refresh(row):
        row.created_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_refresh)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await create_github_pat(name="non-default", token="ghp_x", is_default=False)

    assert len(executed) == 0


# ---------------------------------------------------------------------------
# update_github_pat
# ---------------------------------------------------------------------------


async def test_update_github_pat_not_found_returns_none():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        result = await update_github_pat(uuid.uuid4(), name="x")

    assert result is None


async def test_update_github_pat_re_encrypts_token():
    row = _make_pat_row(token="old_token")
    session = AsyncMock()
    session.get = AsyncMock(return_value=row)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await update_github_pat(row.id, token="new_token_abcdefghijk")

    assert decrypt(row.encrypted_token) == "new_token_abcdefghijk"
    assert row.token_prefix == "new_token_abcdef..."


async def test_update_github_pat_default_true_clears_others():
    row = _make_pat_row()
    session = AsyncMock()
    session.get = AsyncMock(return_value=row)
    executed = []
    session.execute = AsyncMock(side_effect=lambda s: executed.append(s))
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        await update_github_pat(row.id, is_default=True)

    assert len(executed) >= 1
    assert row.is_default is True


# ---------------------------------------------------------------------------
# delete_github_pat
# ---------------------------------------------------------------------------


async def test_delete_github_pat_not_found_returns_false():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        result = await delete_github_pat(uuid.uuid4())

    assert result is False


async def test_delete_github_pat_found_returns_true():
    row = _make_pat_row()
    session = AsyncMock()
    session.get = AsyncMock(return_value=row)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        result = await delete_github_pat(row.id)

    assert result is True
    session.delete.assert_awaited_once_with(row)
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# resolve_github_token
# ---------------------------------------------------------------------------


async def test_resolve_github_token_named_pat():
    row = _make_pat_row(token="ghp_named_secret")
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = row
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        token = await resolve_github_token("my-pat")

    assert token == "ghp_named_secret"


async def test_resolve_github_token_default_pat():
    row = _make_pat_row(token="ghp_default_secret", is_default=True)
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = row
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        token = await resolve_github_token()

    assert token == "ghp_default_secret"


async def test_resolve_github_token_no_db_pat_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env_token_123")
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = None
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        token = await resolve_github_token()

    assert token == "env_token_123"


async def test_resolve_github_token_db_error_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fallback_env_token")

    with patch("pr_guardian.persistence.storage.async_session", _failing_session()):
        token = await resolve_github_token()

    assert token == "fallback_env_token"


async def test_resolve_github_token_corrupt_ciphertext_falls_back_to_env(monkeypatch):
    """A corrupted encrypted_token must fall back to GITHUB_TOKEN env var."""
    monkeypatch.setenv("GITHUB_TOKEN", "env_fallback")
    row = MagicMock()
    row.encrypted_token = "definitely-not-valid-fernet-token"
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = row
    session.scalars = AsyncMock(return_value=scalars_result)

    with patch("pr_guardian.persistence.storage.async_session", _session_cm(session)):
        token = await resolve_github_token()

    # decrypt() returns "" on bad ciphertext; resolve_github_token falls back to env var
    assert token == "env_fallback"
