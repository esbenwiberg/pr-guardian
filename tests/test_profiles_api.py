from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence import models
from pr_guardian.persistence import storage
from pr_guardian.platform.protocol import GateResult


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


def _github_gate_result() -> GateResult:
    return GateResult(
        state="enforced",
        message="guardian/review is required on main",
        repo="octo/service",
        branch="main",
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
            patch(
                "pr_guardian.api.profiles._ensure_github_gate_for_repo",
                AsyncMock(return_value=_github_gate_result()),
            ),
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


def test_github_app_validation_marks_connection_healthy():
    engine, factory = asyncio.run(_make_session_factory())
    try:

        async def seed_manager() -> None:
            with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
                await storage.add_profile_manager("manager@example.com")

        class FakeGitHubAdapter:
            async def list_installation_repositories(self, *, per_page: int = 1) -> dict:
                return {"total_count": 3}

            async def close(self) -> None:
                return None

        asyncio.run(seed_manager())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch(
                "pr_guardian.platform.github_auth.build_github_adapter_from_connection",
                AsyncMock(return_value=FakeGitHubAdapter()),
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            created = client.post(
                "/api/profiles/connections",
                headers=_manager_headers(),
                json={
                    "name": "Valid GitHub App",
                    "platform": "github",
                    "app_id": "12345",
                    "installation_id": "98765",
                    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfixture\n-----END RSA PRIVATE KEY-----",
                },
            )
            assert created.status_code == 201, created.text
            assert created.json()["health_status"] == "unknown"

            validated = client.post(
                f"/api/profiles/connections/{created.json()['id']}/validate",
                headers=_manager_headers(),
            )
            assert validated.status_code == 200, validated.text
            body = validated.json()
            assert body["health_status"] == "healthy"
            assert "installation token validated" in body["health_message"]
    finally:
        asyncio.run(engine.dispose())


async def _seed_manager_and_github_connection(factory) -> tuple[str, str]:
    with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
        await storage.add_profile_manager("manager@example.com")
        profile = await storage.create_profile("Standard", actor="manager@example.com")
        connection = await storage.create_connection(
            "Healthy GitHub App",
            platform="github",
            auth_kind="github_app",
            app_id="12345",
            installation_id="98765",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfixture\n-----END RSA PRIVATE KEY-----",
            health_status="healthy",
            actor="manager@example.com",
        )
        return profile["id"], connection["id"]


def test_github_repo_link_requires_enforced_guardian_review_gate_for_auto_review():
    engine, factory = asyncio.run(_make_session_factory())
    try:
        profile_id, connection_id = asyncio.run(_seed_manager_and_github_connection(factory))
        gate = AsyncMock(return_value=_github_gate_result())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.api.profiles._ensure_github_gate_for_repo", gate),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
                    "repo_name": "service",
                    "profile_id": profile_id,
                    "connection_id": connection_id,
                    "auto_review_enabled": True,
                    "require_review_check": True,
                },
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["auto_review_enabled"] is True
            # The gate must be enforced before auto-review and persisted on the link.
            assert body["require_review_check"] is True
            gate.assert_awaited_once()
            assert gate.await_args.kwargs["repo_owner"] == "octo"
            assert gate.await_args.kwargs["repo_name"] == "service"
    finally:
        asyncio.run(engine.dispose())


def test_github_repo_link_blocks_auto_review_when_app_lacks_admin():
    """When the App can't enforce guardian/review (e.g. no Administration write),
    the repo link is rejected with a clear error and never persisted."""
    engine, factory = asyncio.run(_make_session_factory())
    try:
        profile_id, connection_id = asyncio.run(_seed_manager_and_github_connection(factory))
        gate = AsyncMock(
            side_effect=HTTPException(
                422, "guardian/review is not a required status check on main"
            )
        )
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.api.profiles._ensure_github_gate_for_repo", gate),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
                    "repo_name": "service",
                    "profile_id": profile_id,
                    "connection_id": connection_id,
                    "auto_review_enabled": True,
                    "require_review_check": True,
                },
            )
            assert response.status_code == 422, response.text
            assert "guardian/review" in response.json()["detail"]
            listing = client.get("/api/profiles/repo-links", headers=_manager_headers())
            assert listing.json() == []
    finally:
        asyncio.run(engine.dispose())


def test_github_repo_link_skips_gate_when_require_review_check_disabled():
    """require_review_check=false is an explicit opt-out: the gate is not
    enforced and the choice is persisted on the link."""
    engine, factory = asyncio.run(_make_session_factory())
    try:
        profile_id, connection_id = asyncio.run(_seed_manager_and_github_connection(factory))
        gate = AsyncMock(return_value=_github_gate_result())
        with (
            patch("pr_guardian.auth.identity._db_available", return_value=True),
            patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
            patch("pr_guardian.api.profiles._ensure_github_gate_for_repo", gate),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post(
                "/api/profiles/repo-links",
                headers=_manager_headers(),
                json={
                    "platform": "github",
                    "repo_owner": "octo",
                    "repo_name": "service",
                    "profile_id": profile_id,
                    "connection_id": connection_id,
                    "auto_review_enabled": True,
                    "require_review_check": False,
                },
            )
            assert response.status_code == 201, response.text
            assert response.json()["require_review_check"] is False
            gate.assert_not_awaited()
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


def test_validate_profile_settings_rejects_scalar_for_structured_field():
    import pytest

    from pr_guardian.api.profiles import _validate_profile_settings

    # severity_floor is a structured SeverityFloorConfig; a bare level string must
    # be rejected on write rather than persisted and crashing reviews later.
    with pytest.raises(HTTPException) as exc:
        _validate_profile_settings({"severity_floor": "medium"})
    assert exc.value.status_code == 422
    assert "severity_floor" in exc.value.detail


def test_validate_profile_settings_accepts_valid_structured_field():
    from pr_guardian.api.profiles import _validate_profile_settings

    settings = {"severity_floor": {"enabled": False}, "readiness": {"quiet_period_seconds": 5}}
    assert _validate_profile_settings(settings) == settings


def test_validate_profile_settings_accepts_glob_editors_and_dependency_policy():
    from pr_guardian.api.profiles import _validate_profile_settings

    settings = {
        "trust_tiers": {
            "default_tier": "ai_only",
            "rules": [{"tier": "human_primary", "patterns": ["**/auth/**"], "reason": "auth"}],
        },
        "path_risk": {
            "critical_paths": [{"pattern": "**/infra/**", "min_tier": "mandatory_human"}],
            "safe_paths": [{"pattern": "**/docs/**", "max_tier": "ai_only"}],
        },
        "security_surface": {"security_critical": ["**/secrets/**"]},
        "dependency_policy": {
            "require_human": True,
            "include_lockfiles": False,
            "include_removals": True,
        },
    }
    assert _validate_profile_settings(settings) == settings


def test_config_defaults_endpoint_shape():
    import asyncio

    from pr_guardian.api import profiles

    out = asyncio.run(profiles.config_defaults(identity=None))
    assert out["trust_tiers"]["rules"], "expected built-in trust-tier rules"
    assert {r["tier"] for r in out["trust_tiers"]["rules"]} <= {
        "ai_only",
        "spot_check",
        "mandatory_human",
        "human_primary",
    }
    assert "security_critical" in out["security_surface"]
    assert "critical_paths" in out["path_risk"]


def test_normalize_ado_org_url_canonicalizes_dev_azure():
    from pr_guardian.api.profiles import _normalize_ado_org_url

    assert _normalize_ado_org_url("https://dev.azure.com/xmp") == "https://dev.azure.com/xmp"
    # Trailing slash is stripped before parsing.
    assert _normalize_ado_org_url("https://dev.azure.com/xmp/") == "https://dev.azure.com/xmp"


def test_normalize_ado_org_url_rewrites_legacy_visualstudio_host():
    from pr_guardian.api.profiles import _normalize_ado_org_url

    # Legacy {org}.visualstudio.com must collapse to the canonical dev.azure.com
    # form so sync-built PR URLs parse with the single dev.azure.com regex used
    # by start-wizard / start-review (regression for "Failed to start review.").
    assert _normalize_ado_org_url("https://xmp.visualstudio.com") == "https://dev.azure.com/xmp"
    assert _normalize_ado_org_url("https://xmp.visualstudio.com/") == "https://dev.azure.com/xmp"


def test_normalize_ado_org_url_rejects_bad_hosts_and_paths():
    import pytest

    from pr_guardian.api.profiles import _normalize_ado_org_url

    # Non-ADO host.
    with pytest.raises(HTTPException) as exc:
        _normalize_ado_org_url("https://evil.example.com/xmp")
    assert exc.value.status_code == 400

    # visualstudio.com with a path is rejected (org lives in the subdomain).
    with pytest.raises(HTTPException):
        _normalize_ado_org_url("https://xmp.visualstudio.com/PowerGantt")

    # Non-HTTPS / SSRF-style input.
    with pytest.raises(HTTPException):
        _normalize_ado_org_url("http://169.254.169.254/latest")
