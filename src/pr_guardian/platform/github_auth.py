from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from pr_guardian.platform.github import GitHubAdapter

log = structlog.get_logger()

_JWT_EXPIRY_SECONDS = 600  # 10 minutes (GitHub maximum)
_JWT_CLOCK_SKEW_SECONDS = 60  # backdate iat to tolerate clock skew
_TOKEN_REFRESH_BUFFER_SECONDS = 60  # refresh token this many seconds before expiry


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: str
    private_key_pem: str
    installation_id: str


@dataclass(frozen=True)
class GitHubInstallationToken:
    token: str
    expires_at: datetime


class GitHubAppAuth:
    """Mints RS256 app JWTs and caches GitHub App installation tokens per instance.

    Thread-safe under asyncio's single-threaded concurrency model. Each instance
    holds its own token cache — tests create fresh instances to stay isolated.
    """

    def __init__(self, credentials: GitHubAppCredentials) -> None:
        self._creds = credentials
        self._cached: GitHubInstallationToken | None = None

    def _make_jwt(self) -> str:
        """Create an RS256-signed GitHub App JWT."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iat": now - _JWT_CLOCK_SKEW_SECONDS,
            "exp": now + _JWT_EXPIRY_SECONDS,
            "iss": self._creds.app_id,
        }
        header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        message = f"{header_b64}.{payload_b64}".encode()

        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        key = serialization.load_pem_private_key(
            self._creds.private_key_pem.encode(), password=None
        )
        if not isinstance(key, RSAPrivateKey):
            raise TypeError(f"GitHub App private key must be RSA; got {type(key).__name__}")
        signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return f"{header_b64}.{payload_b64}.{_b64url(signature)}"

    async def get_token(self) -> str:
        """Return a valid installation access token, refreshing if needed."""
        now = datetime.now(timezone.utc)
        if self._cached is not None:
            remaining = (self._cached.expires_at - now).total_seconds()
            if remaining > _TOKEN_REFRESH_BUFFER_SECONDS:
                return self._cached.token

        jwt = self._make_jwt()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/app/installations/{self._creds.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._cached = GitHubInstallationToken(
            token=data["token"],
            expires_at=datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")),
        )
        log.debug(
            "github_installation_token_refreshed",
            installation_id=self._creds.installation_id,
            expires_at=self._cached.expires_at.isoformat(),
        )
        return self._cached.token


class _InstallationBearerAuth(httpx.Auth):
    """httpx auth that injects a GitHub App Bearer token on every request.

    Implements ``async_auth_flow`` so that ``get_token()`` (which may perform
    an async HTTP call to refresh an expired token) is awaited properly.
    """

    requires_request_body = False

    def __init__(self, token_provider: Callable[[], Coroutine[Any, Any, str]]) -> None:
        self._provider = token_provider

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._provider()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


async def build_github_adapter_from_connection(connection: dict) -> GitHubAdapter:
    """Resolve a GitHub App Connection into a configured GitHubAdapter.

    Raises ValueError if the connection is not a valid GitHub App connection.
    Does NOT fall back to env tokens.
    """
    from pr_guardian.persistence import storage
    from pr_guardian.platform.github import GitHubAdapter

    conn_id_str = connection.get("id", "")
    if not conn_id_str:
        raise ValueError("GitHub App Connection has no id")
    if connection.get("auth_kind") != "github_app":
        raise ValueError(
            f"Connection {conn_id_str} is not a GitHub App connection "
            f"(auth_kind={connection.get('auth_kind')!r}); "
            "GITHUB_TOKEN env fallback has been removed"
        )
    import uuid

    private_key = await storage.get_connection_private_key(uuid.UUID(conn_id_str))
    if not private_key:
        raise ValueError(f"GitHub App connection {conn_id_str} has no private key stored")

    app_id = connection.get("app_id") or ""
    installation_id = connection.get("installation_id") or ""
    if not app_id or not installation_id:
        raise ValueError(
            f"GitHub App connection {conn_id_str} is missing app_id or installation_id"
        )

    creds = GitHubAppCredentials(
        app_id=app_id,
        private_key_pem=private_key,
        installation_id=installation_id,
    )
    return GitHubAdapter(app_auth=GitHubAppAuth(creds))
