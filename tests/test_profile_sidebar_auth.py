from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from pr_guardian.auth.identity import Identity
from pr_guardian.main import app


SIDEBAR_JS = Path("src/pr_guardian/dashboard/static/sidebar.js")
COMMAND_PALETTE_JS = Path("src/pr_guardian/dashboard/static/command-palette.js")


def _identity(*, admin: bool = False, manager: bool = False) -> Identity:
    return Identity(
        kind="user",
        email="masked@example.com",
        is_admin=admin,
        can_manage_profiles=manager,
    )


def test_sidebar_shows_profiles_for_managers_and_settings_for_admins():
    sidebar = SIDEBAR_JS.read_text()
    palette = COMMAND_PALETTE_JS.read_text()

    assert "NAV_PROFILES" in sidebar
    assert "canManageProfiles ? navItem(NAV_PROFILES)" in sidebar
    assert "isAdmin ? navItem(NAV_ADMIN)" in sidebar
    assert "requiresProfiles: true" in palette
    assert "requiresAdmin: true" in palette

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(admin=True),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is True
        assert me["can_manage_profiles"] is True
        assert client.get("/profiles").status_code == 200
        assert client.get("/settings").status_code == 200

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(manager=True),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is False
        assert me["can_manage_profiles"] is True
        assert client.get("/profiles").status_code == 200
        settings = client.get("/settings", follow_redirects=False)
        assert settings.status_code == 302
        assert settings.headers["location"] == "/reviews?error=admin_required"

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is False
        assert me["can_manage_profiles"] is False
        profiles = client.get("/profiles", follow_redirects=False)
        assert profiles.status_code == 302
        assert profiles.headers["location"] == "/reviews?error=profile_manager_required"
        settings = client.get("/settings", follow_redirects=False)
        assert settings.status_code == 302
        assert settings.headers["location"] == "/reviews?error=admin_required"
