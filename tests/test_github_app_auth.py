"""Tests for GitHub App auth helper: JWT minting, installation token exchange, and caching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_guardian.platform.github_auth import (
    GitHubAppAuth,
    GitHubAppCredentials,
    GitHubInstallationToken,
)


def _test_rsa_pem() -> str:
    """Generate a fresh RSA-2048 private key for use in tests."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _creds(pem: str | None = None) -> GitHubAppCredentials:
    return GitHubAppCredentials(
        app_id="12345",
        private_key_pem=pem or _test_rsa_pem(),
        installation_id="98765",
    )


def _make_token_response(token: str, hours_ahead: float = 1.0) -> MagicMock:
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"token": token, "expires_at": expires_at}
    return resp


@pytest.mark.asyncio
async def test_installation_token_is_cached_and_refreshed_before_expiry():
    """Two calls before expiry share one token; a call near expiry triggers refresh."""
    pem = _test_rsa_pem()
    auth = GitHubAppAuth(_creds(pem))

    exchange_count = 0

    async def _fake_post(*args, **kwargs):
        nonlocal exchange_count
        exchange_count += 1
        return _make_token_response(f"install-token-{exchange_count}")

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_ctx.post = AsyncMock(side_effect=_fake_post)

    with patch("pr_guardian.platform.github_auth.httpx.AsyncClient", return_value=mock_ctx):
        # First call: performs exchange
        token1 = await auth.get_token()
        assert token1 == "install-token-1"
        assert exchange_count == 1

        # Second call before expiry: uses cached token — no new exchange
        token2 = await auth.get_token()
        assert token2 == "install-token-1"
        assert exchange_count == 1

        # Simulate token near expiry (< _TOKEN_REFRESH_BUFFER_SECONDS remaining)
        auth._cached = GitHubInstallationToken(
            token="install-token-1",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        # Third call: cache is stale — triggers a new exchange
        token3 = await auth.get_token()
        assert token3 == "install-token-2"
        assert exchange_count == 2

        # Both API calls use the cached installation token
        assert token1 == token2  # same token for the first two calls


@pytest.mark.asyncio
async def test_jwt_is_rs256_signed():
    """_make_jwt produces a three-part JWT with RS256 header and correct iss claim."""
    import base64
    import json

    pem = _test_rsa_pem()
    auth = GitHubAppAuth(_creds(pem))
    jwt = auth._make_jwt()

    parts = jwt.split(".")
    assert len(parts) == 3

    def _b64decode(s: str) -> bytes:
        padding = 4 - len(s) % 4
        return base64.urlsafe_b64decode(s + "=" * padding)

    header = json.loads(_b64decode(parts[0]))
    payload = json.loads(_b64decode(parts[1]))

    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    assert payload["iss"] == "12345"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


@pytest.mark.asyncio
async def test_expired_cache_entry_triggers_refresh():
    """An already-expired token in cache is not returned — a new exchange happens."""
    pem = _test_rsa_pem()
    auth = GitHubAppAuth(_creds(pem))
    auth._cached = GitHubInstallationToken(
        token="stale-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    async def _fake_post(*args, **kwargs):
        return _make_token_response("fresh-token")

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_ctx.post = AsyncMock(side_effect=_fake_post)

    with patch("pr_guardian.platform.github_auth.httpx.AsyncClient", return_value=mock_ctx):
        token = await auth.get_token()
        assert token == "fresh-token"
        assert mock_ctx.post.call_count == 1


# ---------------------------------------------------------------------------
# build_github_adapter_from_connection — error branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_adapter_raises_when_connection_has_no_id():
    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    with pytest.raises(ValueError, match="no id"):
        await build_github_adapter_from_connection({})


@pytest.mark.asyncio
async def test_build_adapter_raises_for_non_app_auth_kind():
    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    with pytest.raises(ValueError, match="not a GitHub App connection"):
        await build_github_adapter_from_connection({"id": "abc123", "auth_kind": None})


@pytest.mark.asyncio
async def test_build_adapter_raises_when_private_key_missing():
    import uuid

    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    conn_id = str(uuid.uuid4())
    with patch(
        "pr_guardian.persistence.storage.get_connection_private_key",
        new=AsyncMock(return_value=""),
    ):
        with pytest.raises(ValueError, match="no private key"):
            await build_github_adapter_from_connection(
                {
                    "id": conn_id,
                    "auth_kind": "github_app",
                    "app_id": "12345",
                    "installation_id": "98765",
                }
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field,connection_extra",
    [
        ("app_id", {"app_id": "", "installation_id": "98765"}),
        ("installation_id", {"app_id": "12345", "installation_id": ""}),
    ],
)
async def test_build_adapter_raises_when_app_id_or_installation_id_missing(
    missing_field: str, connection_extra: dict
):
    import uuid

    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    conn_id = str(uuid.uuid4())
    with patch(
        "pr_guardian.persistence.storage.get_connection_private_key",
        new=AsyncMock(return_value=_test_rsa_pem()),
    ):
        with pytest.raises(ValueError, match="missing app_id or installation_id"):
            await build_github_adapter_from_connection(
                {"id": conn_id, "auth_kind": "github_app", **connection_extra}
            )
