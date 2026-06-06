from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence import models
from pr_guardian.persistence import storage


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _healthy_probe(platform: str, token: str, org_url: str | None) -> tuple[str, str]:
    return "healthy", "fixture validation passed"


def _manager_headers() -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL-NAME": "manager@example.com"}


def _admin_api_key_identity():
    from pr_guardian.auth.identity import Identity

    return Identity(
        kind="api_key",
        key_id="fixture",
        key_name="admin-key",
        scopes=["read", "write"],
        is_admin=True,
        can_manage_profiles=False,
    )


def test_profile_manager_can_create_connection_profile_and_repo_link():
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
            connection_response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Org GitHub",
                    "platform": "github",
                    "app_id": "12345",
                    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJfixture\n-----END RSA PRIVATE KEY-----",
                    "installation_id": "98765",
                    "installation_account": "octo",
                    "sync_enabled": False,
                },
            )
            assert connection_response.status_code == 201, connection_response.text
            connection = connection_response.json()
            assert connection["auth_kind"] == "github_app"
            assert "token" not in connection
            assert "private_key" not in connection

            # GitHub App connections start as "unknown" until Brief 02 installs auth adapter.
            # Manually mark healthy so the repo link can be created.
            async def mark_healthy() -> None:
                with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                    await storage.update_connection(
                        __import__("uuid").UUID(connection["id"]),
                        health_status="healthy",
                        health_message="fixture healthy",
                    )

            asyncio.run(mark_healthy())

            profile_response = client.post(
                "/api/profiles/profiles",
                headers=_manager_headers(),
                json={
                    "name": "Standard Service",
                    "settings": {
                        "severity_floor": "medium",
                        "readiness": {"quiet_period_seconds": 10},
                    },
                },
            )
            assert profile_response.status_code == 201, profile_response.text
            profile = profile_response.json()

            link_response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
                    "repo_name": "service",
                    "repo_url": "https://github.com/octo/service",
                    "profile_id": profile["id"],
                    "connection_id": connection["id"],
                    "auto_review_enabled": True,
                },
            )
            assert link_response.status_code == 201, link_response.text
            link = link_response.json()
            assert link["profile_id"] == profile["id"]
            assert link["connection_id"] == connection["id"]
            assert link["auto_review_enabled"] is True

            me_response = client.get("/api/me", headers=_manager_headers())
            assert me_response.status_code == 200
            assert me_response.json()["can_manage_profiles"] is True
    finally:
        asyncio.run(engine.dispose())


def test_profile_audit_diffs_redact_connection_secrets():
    raw_private_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA_RAW_FIXTURE_KEY\n-----END RSA PRIVATE KEY-----"
    replacement_private_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA_REPLACEMENT_FIXTURE\n-----END RSA PRIVATE KEY-----"
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
            created = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Audit GitHub",
                    "platform": "github",
                    "app_id": "12345",
                    "private_key": raw_private_key,
                },
            )
            assert created.status_code == 201, created.text
            connection_id = created.json()["id"]

            updated = client.patch(
                f"/api/profiles/connections/{connection_id}",
                headers=_manager_headers(),
                json={"private_key": replacement_private_key},
            )
            assert updated.status_code == 200, updated.text
            assert replacement_private_key not in updated.text

            audit = client.get(
                "/api/profiles/audit",
                headers=_manager_headers(),
                params={"target_type": "connection", "target_id": connection_id},
            )
            assert audit.status_code == 200, audit.text
            audit_text = json.dumps(audit.json())
            assert raw_private_key not in audit_text
            assert replacement_private_key not in audit_text
            update_event = next(
                event
                for event in audit.json()
                if event["action"] == "connection.updated"
                and "private_key_secret" in event["diff"]
            )
            assert "private_key_secret" in update_event["diff"]
            assert update_event["actor"] == "manager@example.com"
            assert update_event["target_id"] == connection_id
    finally:
        asyncio.run(engine.dispose())


def test_api_keys_cannot_manage_profile_managers_or_report_profile_capability():
    engine, factory = asyncio.run(_make_session_factory())
    try:
        with (
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch(
                "pr_guardian.auth.identity.IdentityMiddleware._resolve",
                return_value=_admin_api_key_identity(),
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            me = client.get("/api/me", headers={"Authorization": "Bearer prg_fixture"})
            assert me.status_code == 200
            assert me.json()["is_admin"] is True
            assert me.json()["can_manage_profiles"] is False

            managers = client.post(
                "/api/profiles/managers",
                headers={"Authorization": "Bearer prg_fixture"},
                json={"email": "new-manager@example.com"},
            )
            assert managers.status_code == 403
    finally:
        asyncio.run(engine.dispose())


def test_github_connections_reject_token_credentials_and_hide_env_import():
    """
    fact-github-token-import-removed

    GitHub Connection payloads must reject token-only credentials.
    The env-imports endpoint must not offer GITHUB_TOKEN.
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
            # Token-only GitHub Connection must be rejected
            token_only_response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Bad GitHub PAT",
                    "platform": "github",
                    "token": "ghp_should_be_rejected",
                },
            )
            assert token_only_response.status_code == 400, token_only_response.text
            assert (
                "app_id" in token_only_response.text or "token" in token_only_response.text.lower()
            )

            # GitHub Connection missing app_id must be rejected
            no_app_id_response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Incomplete GitHub App",
                    "platform": "github",
                    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
                },
            )
            assert no_app_id_response.status_code == 400, no_app_id_response.text

            # env-imports must NOT include GITHUB_TOKEN
            with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_should_not_appear"}):
                env_response = client.get(
                    "/api/profiles/connections/env-imports",
                    headers=_manager_headers(),
                )
            assert env_response.status_code == 200, env_response.text
            env_data = env_response.json()
            assert "GITHUB_TOKEN" not in env_data
            # ADO env imports still present
            assert "ADO_PAT" in env_data
    finally:
        asyncio.run(engine.dispose())


def test_ado_connection_validation_rejects_untrusted_org_url():
    engine, factory = asyncio.run(_make_session_factory())
    try:

        async def seed_manager() -> None:
            with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                await storage.add_profile_manager("manager@example.com")

        async def fail_probe(platform: str, token: str, org_url: str | None) -> tuple[str, str]:
            raise AssertionError("validation probe should not run for untrusted org_url")

        asyncio.run(seed_manager())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.api.profiles._probe_connection", fail_probe),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Bad ADO",
                    "platform": "ado",
                    "org_url": "http://169.254.169.254/latest",
                    "token": "ado-secret-should-not-leave",
                },
            )
            assert response.status_code == 400
            assert "HTTPS Azure DevOps" in response.text
    finally:
        asyncio.run(engine.dispose())
