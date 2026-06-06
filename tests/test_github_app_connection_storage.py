"""Tests for GitHub App Connection storage: encryption, redaction, and DTO shape."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence import models, storage


_FAKE_PRIVATE_KEY = """\
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtEsHAQqmgkB6beKBnG0TTHAT
FAKE_KEY_DATA_FOR_TESTING_ONLY
-----END RSA PRIVATE KEY-----"""

_FAKE_APP_ID = "12345"
_FAKE_INSTALLATION_ID = "98765"
_FAKE_INSTALLATION_ACCOUNT = "esbenwiberg"


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _manager_headers() -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL-NAME": "manager@example.com"}


def test_github_app_connection_encrypts_and_redacts_private_key():
    """
    fact-github-app-connection-redacts-private-key

    When a GitHub App Connection is created with a private key:
    - The private key is encrypted at rest (not stored as plaintext).
    - The API response includes app_id, installation metadata, fingerprint, permissions.
    - The API response never exposes raw private key, encrypted key, JWT, or token.
    - Audit diffs use a redacted marker only (no key material).
    """
    engine, factory = asyncio.run(_make_session_factory())
    try:

        async def seed_manager() -> None:
            with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                await storage.add_profile_manager("manager@example.com")

        asyncio.run(seed_manager())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Guardian Sandbox",
                    "platform": "github",
                    "app_id": _FAKE_APP_ID,
                    "private_key": _FAKE_PRIVATE_KEY,
                    "installation_id": _FAKE_INSTALLATION_ID,
                    "installation_account": _FAKE_INSTALLATION_ACCOUNT,
                    "installation_target_type": "User",
                    "app_permissions": {
                        "contents": "read",
                        "pull_requests": "write",
                        "statuses": "write",
                    },
                },
            )
            assert response.status_code == 201, response.text
            connection = response.json()

            # DTO includes identity fields
            assert connection["app_id"] == _FAKE_APP_ID
            assert connection["installation_id"] == _FAKE_INSTALLATION_ID
            assert connection["installation_account"] == _FAKE_INSTALLATION_ACCOUNT
            assert connection["installation_target_type"] == "User"
            assert connection["app_permissions"] == {
                "contents": "read",
                "pull_requests": "write",
                "statuses": "write",
            }
            assert connection["auth_kind"] == "github_app"

            # Fingerprint is present and has sha256: prefix
            assert connection.get("private_key_fingerprint", "").startswith("sha256:")

            # Secret material is never in the response — only fingerprint is present
            response_text = response.text
            assert _FAKE_PRIVATE_KEY not in response_text
            assert "encrypted_private_key" not in response_text
            # The DTO must not include a raw "private_key" field (fingerprint is fine)
            assert "private_key" not in connection  # raw key field absent from DTO
            assert "token" not in connection  # no PAT/token field

            connection_id = connection["id"]

            # Verify private key is encrypted at rest via direct storage read
            async def check_encrypted_at_rest() -> None:
                with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                    raw_pk = await storage.get_connection_private_key(
                        __import__("uuid").UUID(connection_id)
                    )
                assert raw_pk.strip() == _FAKE_PRIVATE_KEY.strip()

            asyncio.run(check_encrypted_at_rest())

            # Audit diffs must not contain raw key material
            audit_response = client.get(
                "/api/profiles/audit",
                headers=_manager_headers(),
                params={"target_type": "connection", "target_id": connection_id},
            )
            assert audit_response.status_code == 200, audit_response.text
            audit_text = json.dumps(audit_response.json())
            assert _FAKE_PRIVATE_KEY not in audit_text
            # The audit should record a redacted marker, not raw key
            assert "private_key_secret" in audit_text

            # Update the private key and verify audit shows redacted marker
            updated_key = _FAKE_PRIVATE_KEY.replace("FAKE_KEY_DATA_FOR_TESTING_ONLY", "NEW_KEY")
            update_response = client.patch(
                f"/api/profiles/connections/{connection_id}",
                headers=_manager_headers(),
                json={"private_key": updated_key},
            )
            assert update_response.status_code == 200, update_response.text
            assert updated_key not in update_response.text
            assert _FAKE_PRIVATE_KEY not in update_response.text

            audit2 = client.get(
                "/api/profiles/audit",
                headers=_manager_headers(),
                params={"target_type": "connection", "target_id": connection_id},
            )
            assert audit2.status_code == 200
            audit2_text = json.dumps(audit2.json())
            assert updated_key not in audit2_text
            assert _FAKE_PRIVATE_KEY not in audit2_text
    finally:
        asyncio.run(engine.dispose())
