from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pr_guardian.api.scans import RecentChangesScanRequest, trigger_recent_scan
from pr_guardian.main import app
from pr_guardian.persistence import storage
from pr_guardian.persistence.storage import (
    DEFAULT_PROFILE_ID,
    create_connection,
    create_profile,
    create_repo_link,
    ensure_default_profile,
)
from tests.test_readiness_storage import _make_session_factory
from tests.test_scan_issues import FINDING_ID, SCAN_ID, _MOCK_SCAN


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.asyncio
async def test_linked_and_unlinked_scans_store_profile_and_connection_snapshots():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await ensure_default_profile()
            profile = await create_profile(
                "Scan Profile",
                settings={"recent_changes": {"time_window_days": 14}},
            )
            connection = await create_connection(
                "GitHub Scan",
                platform="github",
                token="fixture-value-scan",
                health_status="healthy",
            )
            await create_repo_link(
                platform="github",
                repo_owner="octo",
                repo_name="service",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
            )
            adapter = MagicMock()
            adapter.close = AsyncMock()

            with (
                patch("pr_guardian.api.scans.create_adapter", return_value=adapter),
                patch("pr_guardian.api.scans.run_recent_changes_scan", new_callable=AsyncMock),
            ):
                linked = await trigger_recent_scan(
                    RecentChangesScanRequest(repo="octo/service", platform="github")
                )
                unlinked = await trigger_recent_scan(
                    RecentChangesScanRequest(repo="octo/unlinked", platform="github")
                )
                await asyncio.sleep(0)

            linked_scan = await storage.get_scan(uuid.UUID(linked.scan_id))
            unlinked_scan = await storage.get_scan(uuid.UUID(unlinked.scan_id))

            assert linked_scan is not None
            assert linked_scan["profile_id"] == profile["id"]
            assert linked_scan["profile_snapshot"]["name"] == "Scan Profile"
            assert linked_scan["connection_id"] == connection["id"]
            assert linked_scan["connection_snapshot"]["name"] == "GitHub Scan"
            assert linked_scan["repo_link_id"] is not None

            assert unlinked_scan is not None
            assert unlinked_scan["profile_id"] == str(DEFAULT_PROFILE_ID)
            assert unlinked_scan["profile_snapshot"]["name"] == "Default / noop"
            assert unlinked_scan["connection_id"] == connection["id"]
            assert unlinked_scan["repo_link_id"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_github_app_connection_scan_uses_app_auth_not_static_token():
    """A GitHub App connection must authenticate via create_github_adapter (App
    auth), NOT the static get_connection_token path — App connections store no
    static token, so the static path builds an unauthenticated client that 404s
    on private repos. Regression for the scan/review auth-path divergence."""
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await ensure_default_profile()
            profile = await create_profile("App Scan Profile", settings={})
            connection = await create_connection(
                "GitHub App",
                platform="github",
                auth_kind="github_app",
                app_id="123",
                installation_id="456",
                private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
                health_status="healthy",
            )
            await create_repo_link(
                platform="github",
                repo_owner="context-and",
                repo_name="portfolio-simulation",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
            )
            adapter = MagicMock()
            adapter.close = AsyncMock()

            app_auth_mock = AsyncMock(return_value=adapter)
            with (
                patch("pr_guardian.api.scans.create_github_adapter", app_auth_mock),
                patch(
                    "pr_guardian.api.scans.create_adapter",
                    side_effect=AssertionError("must not use static-token adapter for App conn"),
                ),
                patch(
                    "pr_guardian.api.scans.storage.get_connection_token",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("must not fetch a static token for App conn"),
                ),
                patch("pr_guardian.api.scans.run_recent_changes_scan", new_callable=AsyncMock),
            ):
                await trigger_recent_scan(
                    RecentChangesScanRequest(
                        repo="context-and/portfolio-simulation", platform="github"
                    )
                )
                await asyncio.sleep(0)

            app_auth_mock.assert_awaited_once_with(connection["id"])
    finally:
        await engine.dispose()


def test_scan_issue_creation_respects_profile_side_effect_switch(client):
    disabled_scan = {
        **_MOCK_SCAN,
        "profile_snapshot": {"settings": {"side_effects": {"scan_issues": False}}},
    }
    with patch(
        "pr_guardian.api.scans.storage.get_scan",
        new_callable=AsyncMock,
        return_value=disabled_scan,
    ):
        resp = client.post(
            f"/api/scans/{SCAN_ID}/create-issues",
            json={"mode": "single", "finding_ids": [FINDING_ID]},
        )
    assert resp.status_code == 403

    enabled_scan = {
        **_MOCK_SCAN,
        "profile_snapshot": {"settings": {"side_effects": {"scan_issues": True}}},
    }
    adapter = MagicMock()
    adapter.create_issue = AsyncMock(
        return_value={"number": 99, "url": "https://github.com/org/repo/issues/99"}
    )
    adapter.close = AsyncMock()
    with (
        patch(
            "pr_guardian.api.scans.storage.get_scan",
            new_callable=AsyncMock,
            return_value=enabled_scan,
        ),
        patch("pr_guardian.api.scans.create_adapter", return_value=adapter),
        patch(
            "pr_guardian.api.scans.storage.create_scan_issue",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
    ):
        resp = client.post(
            f"/api/scans/{SCAN_ID}/create-issues",
            json={"mode": "single", "finding_ids": [FINDING_ID]},
        )
    assert resp.status_code == 200
    assert resp.json()["created"][0]["issue_number"] == "99"
