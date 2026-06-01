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
            patch("pr_guardian.api.profiles._probe_connection", _healthy_probe),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            connection_response = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Org GitHub",
                    "platform": "github",
                    "token": "ghp_fixture_profile_manager_can_link_repo",
                    "sync_enabled": False,
                },
            )
            assert connection_response.status_code == 201, connection_response.text
            connection = connection_response.json()
            assert connection["health_status"] == "healthy"
            assert "token" not in connection

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
    raw_token = "ghp_raw_secret_token_for_audit_create"
    replacement_token = "ghp_raw_secret_token_for_audit_update"
    engine, factory = asyncio.run(_make_session_factory())
    try:

        async def seed_manager() -> None:
            with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                await storage.add_profile_manager("manager@example.com")

        asyncio.run(seed_manager())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.api.profiles._probe_connection", _healthy_probe),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            created = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={"name": "Audit GitHub", "platform": "github", "token": raw_token},
            )
            assert created.status_code == 201, created.text
            connection_id = created.json()["id"]

            updated = client.patch(
                f"/api/profiles/connections/{connection_id}",
                headers=_manager_headers(),
                json={"token": replacement_token},
            )
            assert updated.status_code == 200, updated.text
            assert replacement_token not in updated.text

            audit = client.get(
                "/api/profiles/audit",
                headers=_manager_headers(),
                params={"target_type": "connection", "target_id": connection_id},
            )
            assert audit.status_code == 200, audit.text
            audit_text = json.dumps(audit.json())
            assert raw_token not in audit_text
            assert replacement_token not in audit_text
            update_event = next(
                event
                for event in audit.json()
                if event["action"] == "connection.updated" and "token_secret" in event["diff"]
            )
            assert "token_secret" in update_event["diff"]
            if "token_prefix" in update_event["diff"]:
                assert "ghp_raw_secret" not in json.dumps(update_event["diff"]["token_prefix"])
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
