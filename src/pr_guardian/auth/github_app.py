"""GitHub App authentication — installation tokens with auto-refresh.

When GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY (or GITHUB_APP_KEY_FILE), and
GITHUB_APP_INSTALLATION_ID are set, PR Guardian authenticates as a GitHub
App instead of using a personal access token.

Benefits:
- Org-owned identity (not tied to a user)
- Short-lived tokens (~1 hour, auto-rotating)
- 15k req/hr rate limit (3× PAT)
- Granular permission scoping
"""
from __future__ import annotations

import time

import httpx
import structlog

log = structlog.get_logger()

# Buffer before expiry to trigger proactive refresh
_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


class GitHubAppAuth:
    """Manages GitHub App JWT signing and installation token lifecycle."""

    def __init__(self, app_id: str, private_key: str, installation_id: str):
        self._app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id
        self._token: str | None = None
        self._expires_at: float = 0

    def _generate_jwt(self) -> str:
        """Create a short-lived JWT signed with the app's private key."""
        import jwt  # PyJWT

        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60s ago to account for clock skew
            "exp": now + 600,  # 10 minute max per GitHub spec
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_token(self, client: httpx.AsyncClient) -> str:
        """Return a valid installation token, refreshing if needed.

        Tokens are cached and reused until 5 minutes before expiry.
        """
        if self._token and time.time() < self._expires_at - _REFRESH_BUFFER_SECONDS:
            return self._token

        app_jwt = self._generate_jwt()
        resp = await client.post(
            f"https://api.github.com/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["token"]

        # Parse ISO 8601 expiry to epoch
        from datetime import datetime

        expires_str = data["expires_at"].replace("Z", "+00:00")
        self._expires_at = datetime.fromisoformat(expires_str).timestamp()

        log.info(
            "github_app_token_refreshed",
            installation_id=self._installation_id,
            expires_in_seconds=int(self._expires_at - time.time()),
        )
        return self._token
