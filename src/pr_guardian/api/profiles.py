"""Profile, Connection, repo-link, and Profile Manager management APIs."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError

from pr_guardian.auth.dependencies import require_human_admin, require_profile_manager
from pr_guardian.auth.identity import Identity
from pr_guardian.persistence import storage

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

Platform = Literal["github", "ado"]

_PROFILE_SETTING_KEYS = {
    "repo_risk_class",
    "human_review",
    "thresholds",
    "weights",
    "certainty_validation",
    "guardian_clearance",
    "platform_approval_enabled",
    "path_risk",
    "file_roles",
    "security_surface",
    "trust_tiers",
    "severity_floor",
    "validator",
    "recent_changes",
    "maintenance",
    "inline_comments",
    "readiness",
    "side_effects",
}

_SECRET_SETTING_MARKERS = ("api_key", "password", "secret", "token")


def _actor(identity: Identity) -> str:
    return identity.display_name


def _clean_name(value: str, *, field: str = "name") -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(400, f"{field} is required")
    return cleaned


def _normalize_ado_org_url(org_url: str) -> str:
    parsed = urlparse(org_url.strip().rstrip("/"))
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(400, "ADO org_url must be an HTTPS Azure DevOps URL")
    if host == "dev.azure.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1:
            raise HTTPException(400, "ADO org_url must be https://dev.azure.com/{organization}")
        return f"https://dev.azure.com/{parts[0]}"
    elif not host.endswith(".visualstudio.com"):
        raise HTTPException(400, "ADO org_url host must be dev.azure.com or visualstudio.com")
    elif parsed.path not in ("", "/"):
        raise HTTPException(400, "ADO visualstudio.com org_url must not include a path")
    return f"https://{host}"


def _validate_profile_settings(settings: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(settings) - _PROFILE_SETTING_KEYS)
    if unknown:
        raise HTTPException(422, f"Unsupported Profile setting fields: {', '.join(unknown)}")
    secret_fields = sorted(_find_secret_setting_keys(settings))
    if secret_fields:
        raise HTTPException(
            422,
            "Profile settings must not contain secret fields: " + ", ".join(secret_fields),
        )
    return settings


def _find_secret_setting_keys(value: Any, *, path: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            lowered = key_text.lower()
            if (
                any(marker in lowered for marker in _SECRET_SETTING_MARKERS)
                or lowered in {"authorization", "pat"}
                or lowered.endswith("_pat")
            ):
                found.add(child_path)
            found.update(_find_secret_setting_keys(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_find_secret_setting_keys(child, path=f"{path}[{index}]"))
    return found


def _map_storage_error(exc: Exception) -> HTTPException:
    if isinstance(exc, storage.HealthGateError):
        return HTTPException(422, str(exc))
    if isinstance(exc, storage.ArchiveBlockedError):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(422, str(exc))
    if isinstance(exc, IntegrityError):
        return HTTPException(409, "A record with those unique fields already exists")
    return HTTPException(500, "Profile management write failed")


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_settings(self) -> "ProfilePayload":
        self.name = _clean_name(self.name)
        self.description = self.description.strip()
        self.settings = _validate_profile_settings(self.settings)
        return self


class ProfileUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    settings: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_settings(self) -> "ProfileUpdatePayload":
        if self.name is not None:
            self.name = _clean_name(self.name)
        if self.description is not None:
            self.description = self.description.strip()
        if self.settings is not None:
            self.settings = _validate_profile_settings(self.settings)
        return self


class ConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    platform: Platform
    # ADO credentials (token required for ADO; forbidden for GitHub App)
    token: str | None = None
    org_url: str | None = None
    # GitHub App credentials (required for GitHub; forbidden for ADO)
    app_id: str | None = None
    private_key: str | None = None
    app_slug: str | None = None
    installation_id: str | None = None
    installation_account: str | None = None
    installation_target_type: str | None = None
    app_permissions: dict[str, Any] | None = None
    description: str = ""
    sync_enabled: bool = False
    is_default: bool = False

    @model_validator(mode="after")
    def validate_connection(self) -> "ConnectionPayload":
        self.name = _clean_name(self.name)
        self.description = self.description.strip()
        self.org_url = self.org_url.strip().rstrip("/") if self.org_url else None
        if self.platform == "ado":
            if not self.token:
                raise HTTPException(400, "ADO Connections require a token")
            if not self.org_url:
                raise HTTPException(400, "ADO Connections require org_url")
            self.org_url = _normalize_ado_org_url(self.org_url)
            self.token = _clean_name(self.token, field="token")
            if self.app_id or self.private_key:
                raise HTTPException(400, "ADO Connections do not accept GitHub App fields")
        else:
            # GitHub App Connection
            if self.token:
                raise HTTPException(
                    400, "GitHub Connections require app_id and private_key, not a token"
                )
            if not self.app_id:
                raise HTTPException(400, "GitHub Connections require app_id")
            if not self.private_key:
                raise HTTPException(400, "GitHub Connections require private_key")
            self.app_id = self.app_id.strip()
            self.private_key = self.private_key.strip()
        return self


class ConnectionUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    # ADO token update
    token: str | None = None
    org_url: str | None = None
    description: str | None = None
    sync_enabled: bool | None = None
    is_default: bool | None = None
    # GitHub App credential/metadata updates
    app_id: str | None = None
    private_key: str | None = None
    app_slug: str | None = None
    installation_id: str | None = None
    installation_account: str | None = None
    installation_target_type: str | None = None
    app_permissions: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_connection(self) -> "ConnectionUpdatePayload":
        if self.name is not None:
            self.name = _clean_name(self.name)
        if self.token is not None:
            self.token = _clean_name(self.token, field="token")
        if self.description is not None:
            self.description = self.description.strip()
        if self.org_url is not None:
            self.org_url = self.org_url.strip().rstrip("/")
        if self.app_id is not None:
            self.app_id = self.app_id.strip()
        if self.private_key is not None:
            self.private_key = self.private_key.strip()
        return self


class RepoLinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    repo_name: str
    profile_id: uuid.UUID
    connection_id: uuid.UUID
    org_url: str = ""
    project: str = ""
    repo_owner: str = ""
    repo_url: str = ""
    auto_review_enabled: bool = False
    paused: bool = False

    @field_validator("repo_name")
    @classmethod
    def repo_name_required(cls, value: str) -> str:
        return _clean_name(value, field="repo_name")

    @model_validator(mode="after")
    def normalize(self) -> "RepoLinkPayload":
        self.org_url = self.org_url.strip().rstrip("/")
        self.project = self.project.strip()
        self.repo_owner = self.repo_owner.strip()
        self.repo_url = self.repo_url.strip()
        if self.platform == "github" and not self.repo_owner:
            raise HTTPException(400, "GitHub repo links require repo_owner")
        if self.platform == "ado" and not self.org_url:
            raise HTTPException(400, "ADO repo links require org_url")
        return self


class RepoLinkUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    repo_owner: str | None = None
    org_url: str | None = None
    project: str | None = None
    repo_name: str | None = None
    repo_url: str | None = None
    auto_review_enabled: bool | None = None
    paused: bool | None = None

    @field_validator("repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProfileManagerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = value.lower().strip()
        if "@" not in email:
            raise HTTPException(400, "Invalid email address")
        return email


async def _probe_connection(platform: str, token: str, org_url: str | None) -> tuple[str, str]:
    """Validate platform credentials. Tests patch this function to avoid network calls."""
    try:
        if platform == "github":
            async with httpx.AsyncClient(
                base_url="https://api.github.com",
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "Authorization": f"token {token}",
                },
                timeout=10.0,
            ) as client:
                response = await client.get("/user")
        else:
            if not org_url:
                return "unhealthy", "ADO org_url is required"
            async with httpx.AsyncClient(timeout=10.0) as client:
                safe_org_url = _normalize_ado_org_url(org_url)
                response = await client.get(
                    f"{safe_org_url}/_apis/projects",
                    params={"api-version": "7.1", "$top": "1"},
                    auth=("", token),
                )
        if 200 <= response.status_code < 300:
            return "healthy", "Connection validated successfully"
        return "unhealthy", f"Platform validation returned HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        return "unhealthy", f"Platform validation failed: {exc.__class__.__name__}"


async def _validate_and_persist_connection(
    connection_id: uuid.UUID,
    *,
    platform: str,
    token: str,
    org_url: str | None,
    requested_sync_enabled: bool | None,
    actor: str,
) -> dict[str, Any]:
    health_status, health_message = await _probe_connection(platform, token, org_url)
    sync_enabled = bool(requested_sync_enabled) and health_status == "healthy"
    updated = await storage.update_connection(
        connection_id,
        sync_enabled=sync_enabled if requested_sync_enabled is not None else None,
        health_status=health_status,
        health_message=health_message,
        health_checked_at=datetime.now(UTC),
        actor=actor,
    )
    if updated is None:
        raise HTTPException(404, "Connection not found")
    return updated


@router.get("/env-imports")
@router.get("/connections/env-imports")
async def env_imports(identity: Identity = Depends(require_profile_manager)):
    return {
        "ADO_PAT": {"available": bool(os.environ.get("ADO_PAT"))},
        "ADO_ORG_URL": {"available": bool(os.environ.get("ADO_ORG_URL"))},
    }


@router.get("")
@router.get("/profiles")
async def list_profiles(
    include_archived: bool = Query(False), identity: Identity = Depends(require_profile_manager)
):
    return await storage.list_profiles(include_archived=include_archived)


@router.post("", status_code=201)
@router.post("/profiles", status_code=201)
async def create_profile(
    body: ProfilePayload, identity: Identity = Depends(require_profile_manager)
):
    try:
        return await storage.create_profile(
            body.name,
            description=body.description,
            settings=body.settings,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID, identity: Identity = Depends(require_profile_manager)
):
    profile = await storage.get_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return profile


@router.patch("/profiles/{profile_id}")
@router.put("/profiles/{profile_id}")
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdatePayload,
    identity: Identity = Depends(require_profile_manager),
):
    try:
        updated = await storage.update_profile(
            profile_id,
            name=body.name,
            description=body.description,
            settings=body.settings,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if updated is None:
        raise HTTPException(404, "Profile not found")
    return updated


@router.delete("/profiles/{profile_id}")
async def archive_profile(
    profile_id: uuid.UUID, identity: Identity = Depends(require_profile_manager)
):
    try:
        archived = await storage.archive_profile(profile_id, actor=_actor(identity))
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if not archived:
        raise HTTPException(404, "Profile not found")
    return {"status": "archived", "id": str(profile_id)}


@router.get("/connections")
async def list_connections(
    include_archived: bool = Query(False), identity: Identity = Depends(require_profile_manager)
):
    return await storage.list_connections(include_archived=include_archived)


@router.post("/connections", status_code=201)
async def create_connection(
    body: ConnectionPayload, identity: Identity = Depends(require_profile_manager)
):
    try:
        if body.platform == "github":
            if body.sync_enabled:
                raise HTTPException(
                    400,
                    "GitHub App connections must be validated healthy before enabling sync",
                )
            # GitHub App Connection: store encrypted credentials; runtime validation in Brief 02
            return await storage.create_connection(
                body.name,
                platform=body.platform,
                auth_kind="github_app",
                org_url=body.org_url,
                description=body.description,
                sync_enabled=False,
                is_default=body.is_default,
                actor=_actor(identity),
                app_id=body.app_id,
                app_slug=body.app_slug,
                installation_id=body.installation_id,
                installation_account=body.installation_account,
                installation_target_type=body.installation_target_type,
                private_key=body.private_key,
                app_permissions=body.app_permissions,
            )
        # ADO Connection: PAT-based, validate immediately
        connection = await storage.create_connection(
            body.name,
            platform=body.platform,
            token=body.token,
            org_url=body.org_url,
            description=body.description,
            sync_enabled=False,
            is_default=body.is_default,
            actor=_actor(identity),
        )
        return await _validate_and_persist_connection(
            uuid.UUID(connection["id"]),
            platform=body.platform,
            token=body.token or "",
            org_url=body.org_url,
            requested_sync_enabled=body.sync_enabled,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc


@router.patch("/connections/{connection_id}")
@router.put("/connections/{connection_id}")
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdatePayload,
    identity: Identity = Depends(require_profile_manager),
):
    current = await storage.get_connection(connection_id)
    if current is None:
        raise HTTPException(404, "Connection not found")
    platform = current["platform"]
    is_github_app = current.get("auth_kind") == "github_app"

    org_url = body.org_url if body.org_url is not None else current.get("org_url")
    if platform == "ado" and org_url:
        org_url = _normalize_ado_org_url(org_url)
    if platform == "ado" and not org_url:
        raise HTTPException(400, "ADO Connections require org_url")

    ado_credential_changed = not is_github_app and (
        body.token is not None or body.org_url is not None
    )
    github_app_credential_changed = is_github_app and body.private_key is not None
    credential_changed = ado_credential_changed or github_app_credential_changed

    if (
        body.sync_enabled is True
        and current["health_status"] != "healthy"
        and not credential_changed
    ):
        raise HTTPException(422, "Connection must validate healthy before sync can be enabled")
    try:
        updated = await storage.update_connection(
            connection_id,
            name=body.name,
            token=body.token if not is_github_app else None,
            org_url=org_url if body.org_url is not None else None,
            description=body.description,
            is_default=body.is_default,
            actor=_actor(identity),
            app_id=body.app_id,
            app_slug=body.app_slug,
            installation_id=body.installation_id,
            installation_account=body.installation_account,
            installation_target_type=body.installation_target_type,
            private_key=body.private_key if is_github_app else None,
            app_permissions=body.app_permissions,
        )
        if updated is None:
            raise HTTPException(404, "Connection not found")
        if ado_credential_changed:
            token = body.token or await storage.get_connection_token(connection_id)
            updated = await _validate_and_persist_connection(
                connection_id,
                platform=platform,
                token=token,
                org_url=org_url,
                requested_sync_enabled=(
                    body.sync_enabled if body.sync_enabled is not None else current["sync_enabled"]
                ),
                actor=_actor(identity),
            )
        elif body.sync_enabled is not None:
            updated = await storage.update_connection(
                connection_id,
                sync_enabled=body.sync_enabled,
                actor=_actor(identity),
            )
        return updated
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_storage_error(exc) from exc


@router.post("/connections/{connection_id}/validate")
async def validate_connection(
    connection_id: uuid.UUID, identity: Identity = Depends(require_profile_manager)
):
    current = await storage.get_connection(connection_id)
    if current is None:
        raise HTTPException(404, "Connection not found")
    is_github_app = current.get("auth_kind") == "github_app"
    if is_github_app:
        # GitHub App validation requires installation token auth (Brief 02).
        # Return current state; the health probe will be wired up by the auth adapter.
        return current
    token = await storage.get_connection_token(connection_id)
    if not token:
        updated = await storage.update_connection(
            connection_id,
            sync_enabled=False,
            health_status="unhealthy",
            health_message="Connection token is missing or cannot be decrypted",
            health_checked_at=datetime.now(UTC),
            actor=_actor(identity),
        )
        return updated
    return await _validate_and_persist_connection(
        connection_id,
        platform=current["platform"],
        token=token,
        org_url=current.get("org_url"),
        requested_sync_enabled=current.get("sync_enabled"),
        actor=_actor(identity),
    )


@router.delete("/connections/{connection_id}")
async def archive_connection(
    connection_id: uuid.UUID, identity: Identity = Depends(require_profile_manager)
):
    try:
        archived = await storage.archive_connection(connection_id, actor=_actor(identity))
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if not archived:
        raise HTTPException(404, "Connection not found")
    return {"status": "archived", "id": str(connection_id)}


@router.get("/repo-links")
async def list_repo_links(
    include_archived: bool = Query(False), identity: Identity = Depends(require_profile_manager)
):
    return await storage.list_repo_links(include_archived=include_archived)


@router.post("/repo-links", status_code=201)
async def create_repo_link(
    body: RepoLinkPayload, identity: Identity = Depends(require_profile_manager)
):
    try:
        return await storage.create_repo_link(
            platform=body.platform,
            org_url=body.org_url,
            project=body.project,
            repo_owner=body.repo_owner,
            repo_name=body.repo_name,
            repo_url=body.repo_url,
            profile_id=body.profile_id,
            connection_id=body.connection_id,
            auto_review_enabled=body.auto_review_enabled,
            paused=body.paused,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc


@router.patch("/repo-links/{repo_link_id}")
@router.put("/repo-links/{repo_link_id}")
async def update_repo_link(
    repo_link_id: uuid.UUID,
    body: RepoLinkUpdatePayload,
    identity: Identity = Depends(require_profile_manager),
):
    try:
        updated = await storage.update_repo_link(
            repo_link_id,
            profile_id=body.profile_id,
            connection_id=body.connection_id,
            repo_owner=body.repo_owner,
            org_url=body.org_url,
            project=body.project,
            repo_name=body.repo_name,
            repo_url=body.repo_url,
            auto_review_enabled=body.auto_review_enabled,
            paused=body.paused,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if updated is None:
        raise HTTPException(404, "Repo link not found")
    return updated


@router.post("/repo-links/{repo_link_id}/pause")
async def pause_repo_link(
    repo_link_id: uuid.UUID,
    paused: bool = Query(True),
    identity: Identity = Depends(require_profile_manager),
):
    try:
        updated = await storage.update_repo_link_state(
            repo_link_id,
            paused=paused,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if updated is None:
        raise HTTPException(404, "Repo link not found")
    return updated


@router.post("/repo-links/{repo_link_id}/auto-review")
async def set_repo_link_auto_review(
    repo_link_id: uuid.UUID,
    enabled: bool = Query(True),
    identity: Identity = Depends(require_profile_manager),
):
    try:
        updated = await storage.update_repo_link_state(
            repo_link_id,
            auto_review_enabled=enabled,
            actor=_actor(identity),
        )
    except Exception as exc:
        raise _map_storage_error(exc) from exc
    if updated is None:
        raise HTTPException(404, "Repo link not found")
    return updated


@router.delete("/repo-links/{repo_link_id}")
async def archive_repo_link(
    repo_link_id: uuid.UUID, identity: Identity = Depends(require_profile_manager)
):
    archived = await storage.archive_repo_link(repo_link_id, actor=_actor(identity))
    if not archived:
        raise HTTPException(404, "Repo link not found")
    return {"status": "archived", "id": str(repo_link_id)}


@router.get("/managers")
async def list_profile_managers(identity: Identity = Depends(require_human_admin)):
    return await storage.list_profile_managers()


@router.post("/managers", status_code=201)
async def add_profile_manager(
    body: ProfileManagerPayload, identity: Identity = Depends(require_human_admin)
):
    added = await storage.add_profile_manager(body.email, added_by=_actor(identity))
    if not added:
        raise HTTPException(409, f"{body.email} is already a Profile Manager")
    return {"status": "added", "email": body.email}


@router.delete("/managers/{email}")
async def remove_profile_manager(email: str, identity: Identity = Depends(require_human_admin)):
    removed = await storage.remove_profile_manager(email)
    if not removed:
        raise HTTPException(404, "Profile Manager not found")
    return {"status": "removed", "email": email.lower().strip()}


@router.get("/audit")
async def list_audit(
    target_type: str | None = Query(None),
    target_id: uuid.UUID | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    identity: Identity = Depends(require_profile_manager),
):
    return await storage.list_profile_audit_events(
        target_type=target_type,
        target_id=target_id,
        limit=limit,
    )
