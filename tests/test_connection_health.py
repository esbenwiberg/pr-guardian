from __future__ import annotations

import asyncio
import uuid
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


async def _unhealthy_probe(platform: str, token: str, org_url: str | None) -> tuple[str, str]:
    return "unhealthy", "fixture credential rejected"


async def _healthy_probe(platform: str, token: str, org_url: str | None) -> tuple[str, str]:
    return "healthy", "fixture validation passed"


def _manager_headers() -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL-NAME": "manager@example.com"}


def test_github_pat_probe_reports_unhealthy():
    """A legacy GitHub PAT connection (auth_kind != 'github_app') can never sync or
    review, so the probe must report it unhealthy rather than validating the PAT and
    showing a misleading 'healthy'."""
    from pr_guardian.api.profiles import _probe_connection

    status, message = asyncio.run(_probe_connection("github", "github_pat_fixture", None))
    assert status == "unhealthy"
    assert "GitHub App Connection" in message


def test_unhealthy_connection_blocks_repo_link_and_sync_enabled():
    """Health gate applies to any connection regardless of platform.

    Uses an ADO connection (PAT-based probe) so the probe-based health state
    is controlled via the existing _probe_connection patch pattern.
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
            with patch("pr_guardian.api.profiles._probe_connection", _unhealthy_probe):
                connection_response = client.post(
                    "/api/profiles/connections",
                    headers=_manager_headers(),
                    json={
                        "name": "Unhealthy ADO",
                        "platform": "ado",
                        "token": "ado-fixture-unhealthy-token",
                        "org_url": "https://dev.azure.com/myorg",
                    },
                )
            assert connection_response.status_code == 201, connection_response.text
            connection = connection_response.json()
            assert connection["health_status"] == "unhealthy"
            assert connection["sync_enabled"] is False

            profile_response = client.post(
                "/api/profiles/profiles",
                headers=_manager_headers(),
                json={"name": "Blocked Link Profile", "settings": {"repo_risk_class": "standard"}},
            )
            assert profile_response.status_code == 201, profile_response.text
            profile = profile_response.json()

            link_response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "ado",
                    "org_url": "https://dev.azure.com/myorg",
                    "project": "MyProject",
                    "repo_name": "unhealthy",
                    "profile_id": profile["id"],
                    "connection_id": connection["id"],
                },
            )
            assert link_response.status_code == 422
            assert "validate healthy" in link_response.text

            sync_response = client.patch(
                f"/api/profiles/connections/{connection['id']}",
                headers=_manager_headers(),
                json={"sync_enabled": True},
            )
            assert sync_response.status_code == 422
            assert "validate healthy" in sync_response.text

            still_saved = client.get("/api/profiles/connections", headers=_manager_headers())
            assert still_saved.status_code == 200
            saved_connection = still_saved.json()[0]
            assert saved_connection["id"] == connection["id"]
            assert saved_connection["health_status"] == "unhealthy"
            assert saved_connection["sync_enabled"] is False

            with patch("pr_guardian.api.profiles._probe_connection", _healthy_probe):
                validate_response = client.post(
                    f"/api/profiles/connections/{connection['id']}/validate",
                    headers=_manager_headers(),
                )
            assert validate_response.status_code == 200, validate_response.text
            assert validate_response.json()["health_status"] == "healthy"

            sync_enabled = client.patch(
                f"/api/profiles/connections/{connection['id']}",
                headers=_manager_headers(),
                json={"sync_enabled": True},
            )
            assert sync_enabled.status_code == 200, sync_enabled.text
            assert sync_enabled.json()["sync_enabled"] is True

            with patch("pr_guardian.api.profiles._probe_connection", _unhealthy_probe):
                rotated = client.patch(
                    f"/api/profiles/connections/{connection['id']}",
                    headers=_manager_headers(),
                    json={"token": "ado-fixture-rotated-unhealthy"},
                )
            assert rotated.status_code == 200, rotated.text
            assert rotated.json()["health_status"] == "unhealthy"
            assert rotated.json()["sync_enabled"] is False
    finally:
        asyncio.run(engine.dispose())


def test_unhealthy_connection_blocks_enabling_repo_link_auto_review():
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
            # Use ADO — PAT-based probe can be patched to control health state.
            connection = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Initially Healthy ADO",
                    "platform": "ado",
                    "token": "ado-fixture-initially-healthy",
                    "org_url": "https://dev.azure.com/myorg",
                },
            ).json()
            profile = client.post(
                "/api/profiles/profiles",
                headers=_manager_headers(),
                json={"name": "Auto Review Gate", "settings": {"repo_risk_class": "standard"}},
            ).json()
            link = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "ado",
                    "org_url": "https://dev.azure.com/myorg",
                    "project": "MyProject",
                    "repo_name": "auto-review-gate",
                    "profile_id": profile["id"],
                    "connection_id": connection["id"],
                    "auto_review_enabled": False,
                },
            ).json()

            async def mark_unhealthy() -> None:
                await storage.update_connection(
                    connection_id=uuid.UUID(connection["id"]),
                    health_status="unhealthy",
                    health_message="fixture later failure",
                )

            asyncio.run(mark_unhealthy())

            response = client.post(
                f"/api/profiles/repo-links/{link['id']}/auto-review",
                headers=_manager_headers(),
                params={"enabled": True},
            )
            assert response.status_code == 422
            assert "validate healthy" in response.text
    finally:
        asyncio.run(engine.dispose())


def test_ado_connection_health_still_uses_pat_shape():
    """
    fact-ado-connections-stay-pat-shaped

    ADO Connections created with org_url and token continue to use PAT-based auth.
    Health validation, redaction, and sync-gate behavior are unchanged for ADO.
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
            # Create ADO connection — PAT shape with org_url + token
            with patch("pr_guardian.api.profiles._probe_connection", _healthy_probe):
                response = client.post(
                    "/api/profiles/connections",
                    headers=_manager_headers(),
                    json={
                        "name": "ADO PAT Connection",
                        "platform": "ado",
                        "token": "ado-pat-secret-fixture",
                        "org_url": "https://dev.azure.com/myorg",
                        "sync_enabled": True,
                    },
                )
            assert response.status_code == 201, response.text
            connection = response.json()

            # ADO connection must be healthy via PAT probe
            assert connection["health_status"] == "healthy"
            assert connection["sync_enabled"] is True

            # Secret token must not appear in the response
            assert "ado-pat-secret-fixture" not in response.text
            # auth_kind is None for ADO (not a GitHub App connection)
            assert connection.get("auth_kind") is None
            # No GitHub App fields populated
            assert connection.get("app_id") is None
            assert connection.get("private_key_fingerprint") is None

            connection_id = connection["id"]

            # Token rotation still works via PAT-based probe
            with patch("pr_guardian.api.profiles._probe_connection", _unhealthy_probe):
                rotated = client.patch(
                    f"/api/profiles/connections/{connection_id}",
                    headers=_manager_headers(),
                    json={"token": "ado-pat-rotated-fixture"},
                )
            assert rotated.status_code == 200, rotated.text
            assert rotated.json()["health_status"] == "unhealthy"
            # sync disabled because health is now unhealthy
            assert rotated.json()["sync_enabled"] is False

            # Audit must not contain the raw PAT
            audit = client.get(
                "/api/profiles/audit",
                headers=_manager_headers(),
                params={"target_type": "connection", "target_id": connection_id},
            )
            assert audit.status_code == 200
            import json as _json

            audit_text = _json.dumps(audit.json())
            assert "ado-pat-secret-fixture" not in audit_text
            assert "ado-pat-rotated-fixture" not in audit_text
            assert "token_secret" in audit_text
    finally:
        asyncio.run(engine.dispose())
