"""Verify update_profile stores and returns the escalation_policy block.

Contract fact: fact-profiles-api-roundtrip
Scenario: api-roundtrips-block
  Given: an update_profile call carrying an escalation_policy block
  When: the profile is saved and read back
  Then: Profile.settings contains the escalation_policy block and it is returned
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence import models, storage


def _manager_headers() -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL-NAME": "manager@example.test"}


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.fixture
def db_client():
    engine, factory = asyncio.run(_make_db())

    async def seed():
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await storage.add_profile_manager("manager@example.test")

    asyncio.run(seed())
    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        yield client
    asyncio.run(engine.dispose())


def test_escalation_policy_roundtrip(db_client):
    """Full structural_only block persists and is returned on read."""
    created = db_client.post(
        "/api/profiles/profiles",
        headers=_manager_headers(),
        json={"name": "EscalationTest"},
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    updated = db_client.patch(
        f"/api/profiles/profiles/{profile_id}",
        headers=_manager_headers(),
        json={
            "settings": {
                "escalation_policy": {
                    "mode": "structural_only",
                    "gate_threshold": "high",
                    "reject_threshold": "medium_plus",
                }
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["escalation_policy"] == {
        "mode": "structural_only",
        "gate_threshold": "high",
        "reject_threshold": "medium_plus",
    }

    read = db_client.get(f"/api/profiles/profiles/{profile_id}", headers=_manager_headers())
    assert read.status_code == 200, read.text
    assert read.json()["settings"]["escalation_policy"] == {
        "mode": "structural_only",
        "gate_threshold": "high",
        "reject_threshold": "medium_plus",
    }


def test_escalation_policy_defaults_standard(db_client):
    """Standard mode is accepted and round-trips."""
    created = db_client.post(
        "/api/profiles/profiles",
        headers=_manager_headers(),
        json={
            "name": "StandardEscalation",
            "settings": {"escalation_policy": {"mode": "standard"}},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["settings"]["escalation_policy"]["mode"] == "standard"


def test_escalation_policy_invalid_mode_rejected(db_client):
    """Unknown mode value is rejected with 422."""
    created = db_client.post(
        "/api/profiles/profiles",
        headers=_manager_headers(),
        json={"name": "BadMode"},
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    resp = db_client.patch(
        f"/api/profiles/profiles/{profile_id}",
        headers=_manager_headers(),
        json={"settings": {"escalation_policy": {"mode": "unknown_mode"}}},
    )
    assert resp.status_code == 422, resp.text


def test_escalation_policy_invalid_gate_threshold_rejected(db_client):
    """Invalid gate_threshold is rejected with 422."""
    created = db_client.post(
        "/api/profiles/profiles",
        headers=_manager_headers(),
        json={"name": "BadThreshold"},
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    resp = db_client.patch(
        f"/api/profiles/profiles/{profile_id}",
        headers=_manager_headers(),
        json={
            "settings": {
                "escalation_policy": {
                    "mode": "structural_only",
                    "gate_threshold": "extreme",
                }
            }
        },
    )
    assert resp.status_code == 422, resp.text
