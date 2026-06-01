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


def test_unhealthy_connection_blocks_repo_link_and_sync_enabled():
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
                        "name": "Unhealthy GitHub",
                        "platform": "github",
                        "token": "ghp_fixture_unhealthy_connection",
                    },
                )
            assert connection_response.status_code == 201, connection_response.text
            connection = connection_response.json()
            assert connection["health_status"] == "unhealthy"
            assert connection["sync_enabled"] is False

            profile_response = client.post(
                "/api/profiles/profiles",
                headers=_manager_headers(),
                json={"name": "Blocked Link Profile", "settings": {"severity_floor": "low"}},
            )
            assert profile_response.status_code == 201, profile_response.text
            profile = profile_response.json()

            link_response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
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
                    json={"token": "ghp_fixture_rotated_unhealthy"},
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
            connection = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Initially Healthy GitHub",
                    "platform": "github",
                    "token": "ghp_fixture_initially_healthy",
                },
            ).json()
            profile = client.post(
                "/api/profiles/profiles",
                headers=_manager_headers(),
                json={"name": "Auto Review Gate", "settings": {"severity_floor": "low"}},
            ).json()
            link = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
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
