"""Service layer: save review results and query for the dashboard."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pr_guardian.models.output import ReviewResult
from pr_guardian.models.pr import PlatformPR
from pr_guardian.persistence import exclusions as _exclusions
from pr_guardian.persistence.database import _get_engine, async_session
from pr_guardian.persistence.models import (
    AdminRow,
    AgentResultRow,
    ApiKeyRow,
    ChatOpsCommandRow,
    ConnectionRow,
    ExcludedRepoRow,
    FindingDismissalRow,
    FindingRow,
    GlobalConfigRow,
    GuidanceCommentRow,
    MechanicalResultRow,
    PostedInlineCommentRow,
    ProfileAuditEventRow,
    ProfileManagerRow,
    ProfileRow,
    ReadinessCandidateRow,
    ReadinessCandidateTransitionRow,
    RepoLinkRow,
    PromptOverrideRow,
    ReviewRow,
    ScanAgentResultRow,
    ScanFindingRow,
    ScanIssueRow,
    ScanRow,
    SyncSourceRow,
    SyncedPRRow,
    UserIdentityRow,
)

log = structlog.get_logger()

DEFAULT_PROFILE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
READINESS_STATES = frozenset(
    {"waiting", "blocked", "reviewing", "reviewed", "superseded", "error"}
)
TERMINAL_READINESS_STATES = frozenset({"reviewing", "reviewed", "superseded"})
DEFAULT_REVIEWING_STALE_MINUTES = 15
# How far back a reviewed candidate stays eligible for a readiness re-assert.
# Bounds the reconciler scan to recently-completed reviews so a stranded
# readiness check self-heals without trawling the entire reviewed backlog.
DEFAULT_REVIEWED_SYNC_WINDOW_MINUTES = 360

add_excluded_repo = _exclusions.add_excluded_repo
add_exclusion_rule = _exclusions.add_exclusion_rule
get_pr_filter_options = _exclusions.get_pr_filter_options
list_excluded_repos = _exclusions.list_excluded_repos
list_exclusion_rules = _exclusions.list_exclusion_rules
remove_excluded_repo = _exclusions.remove_excluded_repo
remove_exclusion_rule = _exclusions.remove_exclusion_rule
repo_matches_rules = _exclusions.repo_matches_rules


class ArchiveBlockedError(RuntimeError):
    """Raised when an archive operation would strand an active repo link."""


class HealthGateError(RuntimeError):
    """Raised when a management write requires a healthy Connection."""


def _token_prefix(token: str) -> str:
    if len(token) <= 8:
        return "****"
    return token[:8] + "..."


def _safe_token_prefix(prefix: str | None) -> str:
    """Return a display-only token prefix that cannot contain a full short secret."""
    if not prefix:
        return ""
    if prefix == "****" or prefix.endswith("..."):
        return prefix
    if len(prefix) <= 8:
        return "****"
    return prefix[:8] + "..."


def _private_key_fingerprint(pem: str) -> str:
    """Compute a stable sha256 fingerprint of a PEM private key for display."""
    digest = hashlib.sha256(pem.strip().encode()).hexdigest()
    return f"sha256:{digest}"


def _secretish_key(key: str) -> bool:
    lowered = key.lower()
    if any(
        marker in lowered
        for marker in ("token", "secret", "password", "api_key", "encrypted_private")
    ):
        return True
    return lowered in {"pat", "authorization"} or lowered.endswith("_pat")


def _redact_for_audit(value: Any, *, key: str = "") -> Any:
    if key and _secretish_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _redact_for_audit(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_audit(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("bearer ", "token ")):
        return "[redacted]"
    return value


def _audit_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Build a compact field-level diff from redacted DTOs."""
    before = before or {}
    after = after or {}
    fields = sorted(set(before) | set(after))
    return {
        key: {
            "before": _redact_for_audit(before.get(key), key=key),
            "after": _redact_for_audit(after.get(key), key=key),
        }
        for key in fields
        if before.get(key) != after.get(key)
    }


def _audit_before_after(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    diff = _audit_diff(before, after)
    return (
        {"fields": {key: value["before"] for key, value in diff.items()}} if before else None,
        {
            "fields": {key: value["after"] for key, value in diff.items()},
            "diff": diff,
        },
    )


def _audit_event_to_dict(row: ProfileAuditEventRow) -> dict[str, Any]:
    before = row.before
    after = row.after
    diff = after.get("diff") if isinstance(after, dict) else None
    if not isinstance(diff, dict):
        diff = _audit_diff(before, after)
    return {
        "id": str(row.id),
        "actor": row.actor,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": str(row.target_id) if row.target_id else None,
        "before": before,
        "after": after,
        "diff": diff,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _default_profile_settings() -> dict[str, Any]:
    return {
        "guardian_clearance": False,
        "platform_approval_enabled": False,
        "side_effects": {
            "comments": False,
            "labels": False,
            "reviewers": False,
            "formal_approve": False,
            "formal_request_changes": False,
            "scan_issues": False,
        },
        "readiness": {
            "quiet_period_seconds": 10,
            "max_wait_minutes": 30,
            "archmap_max_wait_minutes": 10,
            "ignored_statuses": [],
            "ignored_checks": [],
            "archmap_expected": False,
        },
    }


def _normalize_ado_project(project: str, org_url: str = "") -> str:
    """Extract the short project name from a full ADO project URL if needed.

    Users sometimes paste the browser URL (https://dev.azure.com/org/ProjectName)
    into the project field instead of just "ProjectName". The ADO REST API always
    returns the short name, so we normalise here so both forms hash to the same key.
    """
    from urllib.parse import unquote

    stripped = project.strip()
    if not stripped.lower().startswith(("http://", "https://")):
        return stripped
    decoded = unquote(stripped)
    clean_org = org_url.lower().rstrip("/")
    if clean_org and decoded.lower().startswith(clean_org + "/"):
        return decoded[len(clean_org) + 1 :].strip("/")
    return decoded.rstrip("/").split("/")[-1]


def _canonical_repo_key(
    platform: str,
    *,
    org_url: str = "",
    project: str = "",
    repo_owner: str = "",
    repo_name: str,
) -> str:
    normalized_platform = platform.lower().strip()
    if normalized_platform == "github":
        return f"github:{repo_owner.lower().strip()}/{repo_name.lower().strip()}"
    if normalized_platform == "ado":
        norm_project = _normalize_ado_project(project, org_url)
        return (
            "ado:"
            f"{org_url.lower().rstrip('/')}:"
            f"{norm_project.lower().strip()}/{repo_name.lower().strip()}"
        )
    return f"{normalized_platform}:{repo_owner.lower().strip()}/{repo_name.lower().strip()}"


def _profile_to_dict(row: ProfileRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "settings": row.settings or {},
        "is_system": row.is_system,
        "is_default": row.is_default,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _connection_to_dict(row: ConnectionRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "platform": row.platform,
        "auth_kind": row.auth_kind,
        "org_url": row.org_url,
        "token_prefix": _safe_token_prefix(row.token_prefix),
        # GitHub App identity fields — no secret material
        "app_id": row.app_id,
        "app_slug": row.app_slug,
        "installation_id": row.installation_id,
        "installation_account": row.installation_account,
        "installation_target_type": row.installation_target_type,
        "private_key_fingerprint": row.private_key_fingerprint,
        "app_permissions": row.app_permissions,
        # encrypted_private_key is intentionally excluded
        "health_status": row.health_status,
        "health_message": row.health_message,
        "health_checked_at": row.health_checked_at.isoformat() if row.health_checked_at else None,
        "sync_enabled": row.sync_enabled,
        "is_default": row.is_default,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _repo_link_to_dict(row: RepoLinkRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "platform": row.platform,
        "org_url": row.org_url,
        "project": row.project,
        "repo_owner": row.repo_owner,
        "repo_name": row.repo_name,
        "repo_url": row.repo_url,
        "canonical_repo_key": row.canonical_repo_key,
        "profile_id": str(row.profile_id),
        "connection_id": str(row.connection_id),
        "auto_review_enabled": row.auto_review_enabled,
        "paused": row.paused,
        "require_review_check": row.require_review_check,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _candidate_to_dict(row: ReadinessCandidateRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "repo_link_id": str(row.repo_link_id),
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "connection_id": str(row.connection_id) if row.connection_id else None,
        "platform": row.platform,
        "org_url": row.org_url,
        "project": row.project,
        "repo_owner": row.repo_owner,
        "repo_name": row.repo_name,
        "repo": row.repo,
        "canonical_repo_key": row.canonical_repo_key,
        "pr_id": row.pr_id,
        "pr_url": row.pr_url,
        "head_sha": row.head_sha,
        "state": row.state,
        "reason": row.reason,
        "readiness_synced": row.readiness_synced,
        "readiness_snapshot": row.readiness_snapshot or {},
        "profile_snapshot": row.profile_snapshot,
        "connection_snapshot": row.connection_snapshot,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _transition_to_dict(row: ReadinessCandidateTransitionRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "candidate_id": str(row.candidate_id),
        "from_state": row.from_state,
        "to_state": row.to_state,
        "source": row.source,
        "actor": row.actor,
        "reason": row.reason,
        "readiness_snapshot": row.readiness_snapshot or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


async def ensure_default_profile() -> dict[str, Any]:
    """Create the system default/noop Profile if it is missing."""
    async with async_session() as session:
        row = await session.get(ProfileRow, DEFAULT_PROFILE_ID)
        if row is None:
            row = ProfileRow(
                id=DEFAULT_PROFILE_ID,
                name="Default / noop",
                description="System default profile for unlinked manual work.",
                settings=_default_profile_settings(),
                is_system=True,
                is_default=True,
                created_by="system",
                updated_by="system",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _profile_to_dict(row)


async def create_profile(
    name: str,
    *,
    description: str = "",
    settings: dict[str, Any] | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    row = ProfileRow(
        name=name,
        description=description,
        settings=settings or {},
        is_system=False,
        is_default=False,
        created_by=actor,
        updated_by=actor,
    )
    async with async_session() as session:
        session.add(row)
        await session.flush()
        audit_before, audit_after = _audit_before_after(None, _profile_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="profile.created",
                target_type="profile",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _profile_to_dict(row)


async def list_profiles(*, include_archived: bool = False) -> list[dict[str, Any]]:
    async with async_session() as session:
        query = select(ProfileRow).order_by(ProfileRow.is_default.desc(), ProfileRow.name)
        if not include_archived:
            query = query.where(ProfileRow.archived_at.is_(None))
        rows = (await session.scalars(query)).all()
        return [_profile_to_dict(row) for row in rows]


async def get_profile(profile_id: uuid.UUID) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(ProfileRow, profile_id)
        return _profile_to_dict(row) if row else None


async def update_profile(
    profile_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    settings: dict[str, Any] | None = None,
    actor: str = "system",
) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(ProfileRow, profile_id)
        if row is None:
            return None
        before = _profile_to_dict(row)
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if settings is not None:
            row.settings = settings
        row.updated_by = actor
        row.updated_at = _now()
        after = _profile_to_dict(row)
        audit_before, audit_after = _audit_before_after(before, after)
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="profile.updated",
                target_type="profile",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _profile_to_dict(row)


async def archive_profile(profile_id: uuid.UUID, *, actor: str = "system") -> bool:
    async with async_session() as session:
        row = await session.get(ProfileRow, profile_id)
        if row is None:
            return False
        if row.is_default:
            raise ArchiveBlockedError("Default/noop Profile cannot be archived")
        active_link = await session.scalar(
            select(RepoLinkRow.id)
            .where(RepoLinkRow.profile_id == profile_id)
            .where(RepoLinkRow.archived_at.is_(None))
            .where(RepoLinkRow.paused.is_(False))
            .where(RepoLinkRow.auto_review_enabled.is_(True))
            .limit(1)
        )
        if active_link:
            raise ArchiveBlockedError(
                "Profile is used by an active repo link; move, pause, or disable the link first"
            )
        before = _profile_to_dict(row)
        row.archived_at = _now()
        row.updated_by = actor
        row.updated_at = _now()
        audit_before, audit_after = _audit_before_after(before, _profile_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="profile.archived",
                target_type="profile",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        return True


async def create_connection(
    name: str,
    *,
    platform: str,
    auth_kind: str | None = None,
    token: str | None = None,
    org_url: str | None = None,
    description: str = "",
    sync_enabled: bool = False,
    health_status: str = "unknown",
    health_message: str = "",
    is_default: bool = False,
    actor: str = "system",
    # GitHub App fields
    app_id: str | None = None,
    app_slug: str | None = None,
    installation_id: str | None = None,
    installation_account: str | None = None,
    installation_target_type: str | None = None,
    private_key: str | None = None,
    app_permissions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from pr_guardian.persistence.crypto import encrypt

    async with async_session() as session:
        if is_default and platform == "github":
            await session.execute(
                sa_update(ConnectionRow)
                .where(ConnectionRow.platform == "github")
                .where(ConnectionRow.id.isnot(None))
                .values(is_default=False)
            )
        row = ConnectionRow(
            name=name,
            description=description,
            platform=platform,
            auth_kind=auth_kind,
            org_url=org_url,
            encrypted_token=encrypt(token) if token else None,
            token_prefix=_token_prefix(token) if token else "",
            app_id=app_id,
            app_slug=app_slug,
            installation_id=installation_id,
            installation_account=installation_account,
            installation_target_type=installation_target_type,
            encrypted_private_key=encrypt(private_key) if private_key else None,
            private_key_fingerprint=_private_key_fingerprint(private_key) if private_key else None,
            app_permissions=app_permissions,
            health_status=health_status,
            health_message=health_message,
            sync_enabled=sync_enabled,
            is_default=is_default,
            created_by=actor,
            updated_by=actor,
        )
        session.add(row)
        await session.flush()
        audit_before, audit_after = _audit_before_after(None, _connection_to_dict(row))
        if private_key is not None and audit_after is not None:
            diff = audit_after.setdefault("diff", {})
            fields = audit_after.setdefault("fields", {})
            fields["private_key_secret"] = "set"
            diff["private_key_secret"] = {"before": None, "after": "set"}
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="connection.created",
                target_type="connection",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _connection_to_dict(row)


async def list_connections(*, include_archived: bool = False) -> list[dict[str, Any]]:
    async with async_session() as session:
        query = select(ConnectionRow).order_by(ConnectionRow.platform, ConnectionRow.name)
        if not include_archived:
            query = query.where(ConnectionRow.archived_at.is_(None))
        rows = (await session.scalars(query)).all()
        return [_connection_to_dict(row) for row in rows]


async def list_broad_sync_connections() -> list[dict[str, Any]]:
    """Return active Connections eligible for broad pull-request browse sync."""
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(ConnectionRow)
                .where(ConnectionRow.archived_at.is_(None))
                .where(ConnectionRow.sync_enabled.is_(True))
                .where(ConnectionRow.health_status == "healthy")
                .order_by(ConnectionRow.platform, ConnectionRow.name)
            )
        ).all()
        return [_connection_to_dict(row) for row in rows]


async def get_connection(connection_id: uuid.UUID) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(ConnectionRow, connection_id)
        return _connection_to_dict(row) if row else None


async def get_connection_token(connection_id: uuid.UUID) -> str:
    from pr_guardian.persistence.crypto import decrypt

    async with async_session() as session:
        row = await session.get(ConnectionRow, connection_id)
        if row is None or row.encrypted_token is None:
            return ""
        return decrypt(row.encrypted_token)


async def get_connection_private_key(connection_id: uuid.UUID) -> str:
    """Decrypt and return the GitHub App private key PEM. Returns empty string if absent."""
    from pr_guardian.persistence.crypto import decrypt

    async with async_session() as session:
        row = await session.get(ConnectionRow, connection_id)
        if row is None or row.encrypted_private_key is None:
            return ""
        return decrypt(row.encrypted_private_key)


async def update_connection(
    connection_id: uuid.UUID,
    *,
    name: str | None = None,
    platform: str | None = None,
    token: str | None = None,
    org_url: str | None = None,
    description: str | None = None,
    sync_enabled: bool | None = None,
    health_status: str | None = None,
    health_message: str | None = None,
    health_checked_at: datetime | None = None,
    is_default: bool | None = None,
    actor: str = "system",
    # GitHub App fields
    app_id: str | None = None,
    app_slug: str | None = None,
    installation_id: str | None = None,
    installation_account: str | None = None,
    installation_target_type: str | None = None,
    private_key: str | None = None,
    app_permissions: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from pr_guardian.persistence.crypto import encrypt

    async with async_session() as session:
        row = await session.get(ConnectionRow, connection_id)
        if row is None:
            return None
        before = _connection_to_dict(row)
        if sync_enabled is True and health_status != "healthy" and row.health_status != "healthy":
            raise HealthGateError("Connection must validate healthy before sync can be enabled")
        new_platform = platform.strip().lower() if platform is not None else row.platform
        if is_default is True and new_platform == "github":
            await session.execute(
                sa_update(ConnectionRow)
                .where(ConnectionRow.platform == "github")
                .where(ConnectionRow.id != connection_id)
                .values(is_default=False)
            )
        if name is not None:
            row.name = name
        if platform is not None:
            row.platform = new_platform
        if token is not None:
            row.encrypted_token = encrypt(token)
            row.token_prefix = _token_prefix(token)
        if org_url is not None:
            row.org_url = org_url
        if description is not None:
            row.description = description
        if sync_enabled is not None:
            row.sync_enabled = sync_enabled
        if health_status is not None:
            row.health_status = health_status
        if health_message is not None:
            row.health_message = health_message
        if health_checked_at is not None:
            row.health_checked_at = health_checked_at
        if is_default is not None:
            row.is_default = is_default
        if app_id is not None:
            row.app_id = app_id
        if app_slug is not None:
            row.app_slug = app_slug
        if installation_id is not None:
            row.installation_id = installation_id
        if installation_account is not None:
            row.installation_account = installation_account
        if installation_target_type is not None:
            row.installation_target_type = installation_target_type
        if private_key is not None:
            row.encrypted_private_key = encrypt(private_key)
            row.private_key_fingerprint = _private_key_fingerprint(private_key)
        if app_permissions is not None:
            row.app_permissions = app_permissions
        row.updated_by = actor
        row.updated_at = _now()
        after = _connection_to_dict(row)
        audit_before, audit_after = _audit_before_after(before, after)
        if token is not None and audit_after is not None:
            diff = audit_after.setdefault("diff", {})
            fields = audit_after.setdefault("fields", {})
            before_fields = audit_before.setdefault("fields", {}) if audit_before else {}
            before_fields["token_secret"] = "changed"
            fields["token_secret"] = "changed"
            diff["token_secret"] = {"before": "changed", "after": "changed"}
        if private_key is not None and audit_after is not None:
            diff = audit_after.setdefault("diff", {})
            fields = audit_after.setdefault("fields", {})
            before_fields = audit_before.setdefault("fields", {}) if audit_before else {}
            before_fields["private_key_secret"] = "changed"
            fields["private_key_secret"] = "changed"
            diff["private_key_secret"] = {"before": "changed", "after": "changed"}
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="connection.updated",
                target_type="connection",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _connection_to_dict(row)


async def archive_connection(connection_id: uuid.UUID, *, actor: str = "system") -> bool:
    async with async_session() as session:
        row = await session.get(ConnectionRow, connection_id)
        if row is None:
            return False
        active_link = await session.scalar(
            select(RepoLinkRow.id)
            .where(RepoLinkRow.connection_id == connection_id)
            .where(RepoLinkRow.archived_at.is_(None))
            .where(RepoLinkRow.paused.is_(False))
            .where(RepoLinkRow.auto_review_enabled.is_(True))
            .limit(1)
        )
        if active_link:
            raise ArchiveBlockedError(
                "Connection is used by an active repo link; move, pause, or disable the link first"
            )
        before = _connection_to_dict(row)
        row.archived_at = _now()
        row.updated_by = actor
        row.updated_at = _now()
        audit_before, audit_after = _audit_before_after(before, _connection_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="connection.archived",
                target_type="connection",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        return True


async def create_repo_link(
    *,
    platform: str,
    repo_name: str,
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
    org_url: str = "",
    project: str = "",
    repo_owner: str = "",
    repo_url: str = "",
    auto_review_enabled: bool = False,
    paused: bool = False,
    require_review_check: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    canonical = _canonical_repo_key(
        platform,
        org_url=org_url,
        project=project,
        repo_owner=repo_owner,
        repo_name=repo_name,
    )
    async with async_session() as session:
        profile = await session.get(ProfileRow, profile_id)
        if profile is None:
            raise LookupError(f"Profile not found: {profile_id}")
        if profile.archived_at is not None:
            raise ArchiveBlockedError("Cannot link a repository to an archived Profile")
        connection = await session.get(ConnectionRow, connection_id)
        if connection is None:
            raise LookupError(f"Connection not found: {connection_id}")
        if connection.archived_at is not None:
            raise ArchiveBlockedError("Cannot link a repository to an archived Connection")
        if connection.platform.lower().strip() != platform.lower().strip():
            raise ValueError(
                "Repo link platform must match the selected Connection platform "
                f"({platform!r} != {connection.platform!r})"
            )
        if connection.health_status != "healthy":
            raise HealthGateError(
                "Connection must validate healthy before it can be used for repo links"
            )

        row = RepoLinkRow(
            platform=platform,
            org_url=org_url,
            project=project,
            repo_owner=repo_owner,
            repo_name=repo_name,
            repo_url=repo_url,
            canonical_repo_key=canonical,
            profile_id=profile_id,
            connection_id=connection_id,
            auto_review_enabled=auto_review_enabled,
            paused=paused,
            require_review_check=require_review_check,
            created_by=actor,
            updated_by=actor,
        )
        session.add(row)
        await session.flush()
        audit_before, audit_after = _audit_before_after(None, _repo_link_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="repo_link.created",
                target_type="repo_link",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _repo_link_to_dict(row)


async def update_repo_link_state(
    repo_link_id: uuid.UUID,
    *,
    auto_review_enabled: bool | None = None,
    paused: bool | None = None,
    actor: str = "system",
) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(RepoLinkRow, repo_link_id)
        if row is None:
            return None
        before = _repo_link_to_dict(row)
        if auto_review_enabled is not None:
            row.auto_review_enabled = auto_review_enabled
        if paused is not None:
            row.paused = paused
        if row.auto_review_enabled and not row.paused:
            profile = await session.get(ProfileRow, row.profile_id)
            connection = await session.get(ConnectionRow, row.connection_id)
            if profile is None or profile.archived_at is not None:
                raise ArchiveBlockedError(
                    "Cannot activate repo link while its Profile is archived or missing"
                )
            if connection is None or connection.archived_at is not None:
                raise ArchiveBlockedError(
                    "Cannot activate repo link while its Connection is archived or missing"
                )
            if connection.health_status != "healthy":
                raise HealthGateError(
                    "Connection must validate healthy before auto-review can be enabled"
                )
        row.updated_by = actor
        row.updated_at = _now()
        audit_before, audit_after = _audit_before_after(before, _repo_link_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="repo_link.updated",
                target_type="repo_link",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _repo_link_to_dict(row)


async def update_repo_link(
    repo_link_id: uuid.UUID,
    *,
    profile_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    repo_owner: str | None = None,
    org_url: str | None = None,
    project: str | None = None,
    repo_name: str | None = None,
    repo_url: str | None = None,
    auto_review_enabled: bool | None = None,
    paused: bool | None = None,
    require_review_check: bool | None = None,
    actor: str = "system",
) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(RepoLinkRow, repo_link_id)
        if row is None:
            return None
        before = _repo_link_to_dict(row)
        if profile_id is not None:
            profile = await session.get(ProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"Profile not found: {profile_id}")
            if profile.archived_at is not None:
                raise ArchiveBlockedError("Cannot link a repository to an archived Profile")
            row.profile_id = profile_id
        if connection_id is not None:
            connection = await session.get(ConnectionRow, connection_id)
            if connection is None:
                raise LookupError(f"Connection not found: {connection_id}")
            if connection.archived_at is not None:
                raise ArchiveBlockedError("Cannot link a repository to an archived Connection")
            if connection.health_status != "healthy":
                raise HealthGateError(
                    "Connection must validate healthy before it can be used for repo links"
                )
            if connection.platform.lower().strip() != row.platform.lower().strip():
                raise ValueError(
                    "Repo link platform must match the selected Connection platform "
                    f"({row.platform!r} != {connection.platform!r})"
                )
            row.connection_id = connection_id
        if repo_owner is not None:
            row.repo_owner = repo_owner.strip()
        if org_url is not None:
            row.org_url = org_url.strip().rstrip("/")
        if project is not None:
            row.project = project.strip()
        if repo_name is not None:
            row.repo_name = repo_name.strip()
        if any(f is not None for f in [repo_owner, org_url, project, repo_name]):
            row.canonical_repo_key = _canonical_repo_key(
                row.platform,
                org_url=row.org_url,
                project=row.project,
                repo_owner=row.repo_owner,
                repo_name=row.repo_name,
            )
        if repo_url is not None:
            row.repo_url = repo_url
        if auto_review_enabled is not None:
            row.auto_review_enabled = auto_review_enabled
        if paused is not None:
            row.paused = paused
        if require_review_check is not None:
            row.require_review_check = require_review_check
        if row.auto_review_enabled and not row.paused:
            profile = await session.get(ProfileRow, row.profile_id)
            connection = await session.get(ConnectionRow, row.connection_id)
            if profile is None or profile.archived_at is not None:
                raise ArchiveBlockedError(
                    "Cannot activate repo link while its Profile is archived or missing"
                )
            if connection is None or connection.archived_at is not None:
                raise ArchiveBlockedError(
                    "Cannot activate repo link while its Connection is archived or missing"
                )
            if connection.health_status != "healthy":
                raise HealthGateError(
                    "Connection must validate healthy before auto-review can be enabled"
                )
        row.updated_by = actor
        row.updated_at = _now()
        after = _repo_link_to_dict(row)
        audit_before, audit_after = _audit_before_after(before, after)
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="repo_link.updated",
                target_type="repo_link",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        await session.refresh(row)
        return _repo_link_to_dict(row)


async def get_repo_link(repo_link_id: uuid.UUID) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(RepoLinkRow, repo_link_id)
        return _repo_link_to_dict(row) if row else None


async def get_active_repo_link_for_repo(
    *,
    platform: str,
    repo: str,
    org_url: str = "",
    project: str = "",
    require_auto_review: bool = False,
) -> dict[str, Any] | None:
    """Return the active exact repo link for a platform repository identity."""
    normalized_platform = platform.lower().strip()
    owner, _, name = repo.partition("/")
    if not name:
        owner, name = "", owner
    if normalized_platform == "github":
        canonical = _canonical_repo_key(platform, repo_owner=owner, repo_name=name)
    else:
        repo_name = name or owner
        repo_project = project or (owner if name else "")
        canonical = _canonical_repo_key(
            platform,
            org_url=org_url,
            project=repo_project,
            repo_name=repo_name,
        )
    async with async_session() as session:
        query = (
            select(RepoLinkRow)
            .where(func.lower(RepoLinkRow.platform) == normalized_platform)
            .where(RepoLinkRow.canonical_repo_key == canonical)
            .where(RepoLinkRow.archived_at.is_(None))
        )
        if require_auto_review:
            query = query.where(RepoLinkRow.auto_review_enabled.is_(True))
            query = query.where(RepoLinkRow.paused.is_(False))
        row = await session.scalar(query)
        return _repo_link_to_dict(row) if row else None


async def list_repo_links(*, include_archived: bool = False) -> list[dict[str, Any]]:
    async with async_session() as session:
        query = select(RepoLinkRow).order_by(RepoLinkRow.platform, RepoLinkRow.canonical_repo_key)
        if not include_archived:
            query = query.where(RepoLinkRow.archived_at.is_(None))
        rows = (await session.scalars(query)).all()
        return [_repo_link_to_dict(row) for row in rows]


async def archive_repo_link(repo_link_id: uuid.UUID, *, actor: str = "system") -> bool:
    async with async_session() as session:
        row = await session.get(RepoLinkRow, repo_link_id)
        if row is None:
            return False
        before = _repo_link_to_dict(row)
        row.archived_at = _now()
        row.updated_by = actor
        row.updated_at = _now()
        audit_before, audit_after = _audit_before_after(before, _repo_link_to_dict(row))
        session.add(
            ProfileAuditEventRow(
                actor=actor,
                action="repo_link.archived",
                target_type="repo_link",
                target_id=row.id,
                before=audit_before,
                after=audit_after,
            )
        )
        await session.commit()
        return True


async def add_profile_manager(email: str, *, added_by: str = "system") -> bool:
    async with async_session() as session:
        key = email.lower().strip()
        if await session.get(ProfileManagerRow, key):
            return False
        session.add(ProfileManagerRow(email=key, added_by=added_by))
        await session.commit()
        return True


async def is_profile_manager(email: str) -> bool:
    key = email.lower().strip()
    if not key:
        return False
    async with async_session() as session:
        return await session.get(ProfileManagerRow, key) is not None


async def list_profile_managers() -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (
            await session.scalars(select(ProfileManagerRow).order_by(ProfileManagerRow.created_at))
        ).all()
        return [
            {
                "email": row.email,
                "added_by": row.added_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


async def remove_profile_manager(email: str) -> bool:
    key = email.lower().strip()
    async with async_session() as session:
        row = await session.get(ProfileManagerRow, key)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def list_profile_audit_events(
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with async_session() as session:
        query = select(ProfileAuditEventRow).order_by(ProfileAuditEventRow.created_at.desc())
        if target_type:
            query = query.where(ProfileAuditEventRow.target_type == target_type)
        if target_id:
            query = query.where(ProfileAuditEventRow.target_id == target_id)
        rows = (await session.scalars(query.limit(limit))).all()
        return [_audit_event_to_dict(row) for row in rows]


async def create_readiness_candidate(
    *,
    repo_link_id: uuid.UUID,
    pr_id: str,
    head_sha: str,
    pr_url: str = "",
    state: str = "waiting",
    reason: str = "",
    readiness_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in READINESS_STATES:
        raise ValueError(f"Invalid readiness candidate state: {state}")
    async with async_session() as session:
        link = await session.get(RepoLinkRow, repo_link_id)
        if link is None:
            raise LookupError(f"Repo link not found: {repo_link_id}")
        profile = await session.get(ProfileRow, link.profile_id)
        connection = await session.get(ConnectionRow, link.connection_id)
        row = ReadinessCandidateRow(
            repo_link_id=repo_link_id,
            profile_id=link.profile_id,
            connection_id=link.connection_id,
            platform=link.platform,
            org_url=link.org_url,
            project=link.project,
            repo_owner=link.repo_owner,
            repo_name=link.repo_name,
            repo=f"{link.repo_owner}/{link.repo_name}" if link.repo_owner else link.repo_name,
            canonical_repo_key=link.canonical_repo_key,
            pr_id=pr_id,
            pr_url=pr_url,
            head_sha=head_sha,
            state=state,
            reason=reason,
            readiness_snapshot=readiness_snapshot or {},
            profile_snapshot=_profile_to_dict(profile) if profile else None,
            connection_snapshot=_connection_to_dict(connection) if connection else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _candidate_to_dict(row)


async def get_readiness_candidate(
    *,
    platform: str,
    repo: str,
    pr_id: str,
    head_sha: str,
    org_url: str = "",
    project: str = "",
) -> dict[str, Any] | None:
    normalized_platform = platform.lower().strip()
    owner, _, name = repo.partition("/")
    if not name:
        owner, name = "", owner
    base_query = (
        select(ReadinessCandidateRow)
        .where(func.lower(ReadinessCandidateRow.platform) == normalized_platform)
        .where(ReadinessCandidateRow.pr_id == pr_id)
        .where(ReadinessCandidateRow.head_sha == head_sha)
    )
    if normalized_platform == "github":
        canonical = _canonical_repo_key(platform, repo_owner=owner, repo_name=name)
        query = base_query.where(ReadinessCandidateRow.canonical_repo_key == canonical)
    else:
        repo_name = name or owner
        repo_project = project or (owner if name else "")
        query = base_query.where(func.lower(ReadinessCandidateRow.repo_name) == repo_name.lower())
        if repo_project:
            query = query.where(func.lower(ReadinessCandidateRow.project) == repo_project.lower())
        if org_url:
            query = query.where(
                func.lower(func.rtrim(ReadinessCandidateRow.org_url, "/"))
                == org_url.lower().rstrip("/")
            )
    async with async_session() as session:
        row = await session.scalar(query)
        return _candidate_to_dict(row) if row else None


async def get_readiness_candidate_by_id(candidate_id: uuid.UUID) -> dict[str, Any] | None:
    async with async_session() as session:
        row = await session.get(ReadinessCandidateRow, candidate_id)
        return _candidate_to_dict(row) if row else None


async def list_active_readiness_candidates(
    *,
    platform: str | None = None,
    repo: str | None = None,
    pr_id: str | None = None,
    head_sha: str | None = None,
    states: list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List non-terminal readiness candidates for webhook routing and reconciliation."""
    async with async_session() as session:
        query = select(ReadinessCandidateRow).order_by(ReadinessCandidateRow.updated_at.asc())
        if platform:
            query = query.where(func.lower(ReadinessCandidateRow.platform) == platform.lower())
        if repo:
            owner, _, name = repo.partition("/")
            if platform and platform.lower() == "github" and name:
                canonical = _canonical_repo_key(platform, repo_owner=owner, repo_name=name)
                query = query.where(ReadinessCandidateRow.canonical_repo_key == canonical)
            else:
                query = query.where(func.lower(ReadinessCandidateRow.repo) == repo.lower())
        if pr_id:
            query = query.where(ReadinessCandidateRow.pr_id == pr_id)
        if head_sha:
            query = query.where(ReadinessCandidateRow.head_sha == head_sha)
        if states:
            query = query.where(ReadinessCandidateRow.state.in_(tuple(states)))
        else:
            query = query.where(ReadinessCandidateRow.state.in_(("waiting", "blocked", "error")))
        rows = (await session.scalars(query.limit(limit))).all()
        return [_candidate_to_dict(row) for row in rows]


async def list_recoverable_readiness_candidates(
    *,
    limit: int = 100,
    reviewing_stale_minutes: int = DEFAULT_REVIEWING_STALE_MINUTES,
    reviewed_sync_window_minutes: int = DEFAULT_REVIEWED_SYNC_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    recoverable_reasons = (
        "",
        "quiet_period",
        "draft",
        "checks_pending",
        "checks_failed",
        "checks_timeout",
        "archmap_wait",
        "platform_error",
        "platform_access_error",
        "status_write_failed",
        "review_worker_stale",
    )
    stale_cutoff = _now() - timedelta(minutes=reviewing_stale_minutes)
    reviewed_window_cutoff = _now() - timedelta(minutes=reviewed_sync_window_minutes)
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(ReadinessCandidateRow)
                .where(
                    (
                        ReadinessCandidateRow.state.in_(("waiting", "blocked", "error"))
                        & ReadinessCandidateRow.reason.in_(recoverable_reasons)
                    )
                    | (
                        (ReadinessCandidateRow.state == "reviewing")
                        & (ReadinessCandidateRow.updated_at < stale_cutoff)
                    )
                    | (
                        # Reviewed but never confirmed readiness=success — re-assert
                        # the stranded check once (the flag then excludes it).
                        (ReadinessCandidateRow.state == "reviewed")
                        & ReadinessCandidateRow.readiness_synced.is_(False)
                        & (ReadinessCandidateRow.updated_at >= reviewed_window_cutoff)
                    )
                )
                .order_by(ReadinessCandidateRow.updated_at.asc())
                .limit(limit)
            )
        ).all()
        return [_candidate_to_dict(row) for row in rows]


async def recover_stale_reviewing_candidate(
    candidate_id: uuid.UUID,
    *,
    source: str,
    actor: str = "guardian",
    stale_after_minutes: int = DEFAULT_REVIEWING_STALE_MINUTES,
) -> dict[str, Any] | None:
    """Move an abandoned reviewing candidate back into the recoverable pool.

    Active reviews heartbeat by touching the linked candidate whenever their
    stage advances. If that heartbeat expires, the process probably died; mark
    the abandoned review row failed, then let the reconciler evaluate and claim
    the candidate again.
    """
    now = _now()
    cutoff = now - timedelta(minutes=stale_after_minutes)
    async with async_session() as session:
        candidate = await session.get(ReadinessCandidateRow, candidate_id)
        if candidate is None:
            return None
        candidate_updated_at = _ensure_aware(candidate.updated_at)
        if candidate.state != "reviewing" or candidate_updated_at >= cutoff:
            return _candidate_to_dict(candidate)

        error = f"Review worker heartbeat expired after {stale_after_minutes} minutes"
        open_reviews = (
            await session.scalars(
                select(ReviewRow)
                .where(ReviewRow.candidate_id == candidate_id)
                .where(ReviewRow.finished_at.is_(None))
            )
        ).all()
        for review in open_reviews:
            review.stage = "error"
            review.stage_detail = error
            review.decision = "error"
            review.finished_at = now
            if review.started_at:
                review.duration_ms = int(
                    (now - _ensure_aware(review.started_at)).total_seconds() * 1000
                )

        snapshot = {
            **(candidate.readiness_snapshot or {}),
            "stale_review": {
                "detected_at": now.isoformat(),
                "stale_after_minutes": stale_after_minutes,
                "previous_updated_at": candidate_updated_at.isoformat(),
                "failed_review_ids": [str(review.id) for review in open_reviews],
            },
        }
        transition = ReadinessCandidateTransitionRow(
            candidate_id=candidate_id,
            from_state=candidate.state,
            to_state="error",
            source=source,
            actor=actor,
            reason="review_worker_stale",
            readiness_snapshot=snapshot,
        )
        candidate.state = "error"
        candidate.reason = "review_worker_stale"
        candidate.readiness_snapshot = snapshot
        candidate.updated_at = now
        session.add(transition)
        await session.commit()
        await session.refresh(candidate)
        return _candidate_to_dict(candidate)


async def record_candidate_transition(
    candidate_id: uuid.UUID,
    *,
    to_state: str,
    source: str,
    actor: str = "",
    reason: str = "",
    readiness_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if to_state not in READINESS_STATES:
        raise ValueError(f"Invalid readiness candidate state: {to_state}")
    async with async_session() as session:
        candidate = await session.get(ReadinessCandidateRow, candidate_id)
        if candidate is None:
            raise LookupError(f"Readiness candidate not found: {candidate_id}")
        row = ReadinessCandidateTransitionRow(
            candidate_id=candidate_id,
            from_state=candidate.state,
            to_state=to_state,
            source=source,
            actor=actor,
            reason=reason,
            readiness_snapshot=readiness_snapshot or {},
        )
        candidate.state = to_state
        candidate.reason = reason
        candidate.readiness_snapshot = readiness_snapshot or candidate.readiness_snapshot or {}
        candidate.updated_at = _now()
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _transition_to_dict(row)


async def try_start_candidate_review(
    candidate_id: uuid.UUID,
    pr: PlatformPR,
    *,
    source: str = "automatic",
    actor: str = "system",
    reason: str = "ready",
    readiness_snapshot: dict[str, Any] | None = None,
    comment_mode: str = "summary",
    review_source: str | None = None,
    audit_event: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, dict[str, Any]] | None:
    """Atomically move a ready candidate to reviewing and create its review row.

    Returns ``None`` when another worker already claimed the candidate.
    """
    if reason != "ready" and source == "automatic":
        raise ValueError("Automatic candidate reviews can only start from ready candidates")

    async with async_session() as session:
        existing = await session.get(ReadinessCandidateRow, candidate_id)
        previous_state = existing.state if existing else None
        result = await session.execute(
            sa_update(ReadinessCandidateRow)
            .where(ReadinessCandidateRow.id == candidate_id)
            .where(ReadinessCandidateRow.state.not_in(tuple(TERMINAL_READINESS_STATES)))
            .values(state="reviewing", reason=reason, updated_at=_now())
        )
        if result.rowcount != 1:
            await session.rollback()
            return None

        candidate = await session.get(ReadinessCandidateRow, candidate_id)
        if candidate is None:
            await session.rollback()
            return None
        await session.refresh(candidate)
        candidate.state = "reviewing"
        candidate.reason = reason
        candidate.readiness_snapshot = readiness_snapshot or candidate.readiness_snapshot or {}

        snapshot = candidate.readiness_snapshot or {}
        transition = ReadinessCandidateTransitionRow(
            candidate_id=candidate_id,
            from_state=previous_state,
            to_state="reviewing",
            source=source,
            actor=actor,
            reason=reason,
            readiness_snapshot=snapshot,
        )
        session.add(transition)
        review = ReviewRow(
            pr_id=pr.pr_id,
            repo=pr.repo,
            platform=pr.platform.value,
            author=pr.author,
            title=pr.title,
            source_branch=pr.source_branch,
            target_branch=pr.target_branch,
            head_commit_sha=pr.head_commit_sha or candidate.head_sha,
            pr_url=pr.pr_url or candidate.pr_url,
            stage="queued",
            comment_mode=comment_mode,
            profile_id=candidate.profile_id,
            profile_snapshot=candidate.profile_snapshot,
            connection_id=candidate.connection_id,
            connection_snapshot=candidate.connection_snapshot,
            repo_link_id=candidate.repo_link_id,
            candidate_id=candidate.id,
            review_source=review_source or source,
        )
        session.add(review)
        if audit_event is not None:
            session.add(
                ProfileAuditEventRow(
                    actor=str(audit_event.get("actor") or actor),
                    action=str(audit_event["action"]),
                    target_type=str(audit_event["target_type"]),
                    target_id=audit_event.get("target_id"),
                    before=audit_event.get("before"),
                    after=audit_event.get("after") or {},
                )
            )
        await session.commit()
        await session.refresh(review)
        await session.refresh(candidate)
        return review.id, _candidate_to_dict(candidate)


async def mark_candidate_reviewed_for_review(review_id: uuid.UUID) -> bool:
    """Mark the linked candidate reviewed when the completed review still matches its SHA."""
    async with async_session() as session:
        review = await session.get(ReviewRow, review_id)
        if review is None or review.candidate_id is None:
            return False
        candidate = await session.get(ReadinessCandidateRow, review.candidate_id)
        if candidate is None or candidate.state != "reviewing":
            return False
        if candidate.head_sha != review.head_commit_sha:
            return False
        transition = ReadinessCandidateTransitionRow(
            candidate_id=candidate.id,
            from_state=candidate.state,
            to_state="reviewed",
            source="review_completion",
            actor="guardian",
            reason="review_completed",
            readiness_snapshot=candidate.readiness_snapshot or {},
        )
        candidate.state = "reviewed"
        candidate.reason = "review_completed"
        candidate.updated_at = _now()
        session.add(transition)
        await session.commit()
        return True


async def mark_readiness_synced(candidate_id: uuid.UUID) -> bool:
    """Record that a reviewed candidate's guardian/readiness=success is confirmed.

    Only applies to terminal `reviewed` candidates: it stops the reconciler from
    re-asserting the readiness check again. Leaves `updated_at` untouched so the
    flag, not the timestamp, is what excludes the row from future scans.
    """
    async with async_session() as session:
        candidate = await session.get(ReadinessCandidateRow, candidate_id)
        if candidate is None or candidate.state != "reviewed":
            return False
        if candidate.readiness_synced:
            return True
        candidate.readiness_synced = True
        await session.commit()
        return True


async def record_profile_audit_event(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> uuid.UUID:
    async with async_session() as session:
        row = ProfileAuditEventRow(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after or {},
        )
        session.add(row)
        await session.commit()
        return row.id


async def list_candidate_transitions(candidate_id: uuid.UUID) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(ReadinessCandidateTransitionRow)
                .where(ReadinessCandidateTransitionRow.candidate_id == candidate_id)
                .order_by(ReadinessCandidateTransitionRow.created_at)
            )
        ).all()
        return [_transition_to_dict(r) for r in rows]


async def set_review_provenance(
    review_id: uuid.UUID,
    *,
    profile_id: uuid.UUID | None = None,
    profile_snapshot: dict[str, Any] | None = None,
    connection_id: uuid.UUID | None = None,
    connection_snapshot: dict[str, Any] | None = None,
    repo_link_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    review_source: str | None = None,
) -> bool:
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if row is None:
            return False
        row.profile_id = profile_id
        row.profile_snapshot = profile_snapshot
        row.connection_id = connection_id
        row.connection_snapshot = connection_snapshot
        row.repo_link_id = repo_link_id
        row.candidate_id = candidate_id
        if review_source is not None:
            row.review_source = review_source
        await session.commit()
        return True


async def set_scan_provenance(
    scan_id: uuid.UUID,
    *,
    profile_id: uuid.UUID | None = None,
    profile_snapshot: dict[str, Any] | None = None,
    connection_id: uuid.UUID | None = None,
    connection_snapshot: dict[str, Any] | None = None,
    repo_link_id: uuid.UUID | None = None,
    scan_source: str | None = None,
) -> bool:
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if row is None:
            return False
        row.profile_id = profile_id
        row.profile_snapshot = profile_snapshot
        row.connection_id = connection_id
        row.connection_snapshot = connection_snapshot
        row.repo_link_id = repo_link_id
        if scan_source is not None:
            row.scan_source = scan_source
        await session.commit()
        return True


async def create_review_record(
    pr: PlatformPR, *, comment_mode: str = "none", pat_name: str | None = None
) -> uuid.UUID:
    """Insert a pending review row when a review starts. Returns the row id."""
    row = ReviewRow(
        pr_id=pr.pr_id,
        repo=pr.repo,
        platform=pr.platform.value,
        author=pr.author,
        title=pr.title,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        head_commit_sha=pr.head_commit_sha,
        pr_url=pr.pr_url,
        stage="discovery",
        comment_mode=comment_mode,
        connection_snapshot={"legacy_pat_name": pat_name} if pat_name else None,
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        log.debug("review_record_created", review_id=str(row.id), pr_id=pr.pr_id)
        return row.id


async def update_review_stage(review_id: uuid.UUID, stage: str, detail: str = "") -> None:
    """Update the pipeline stage for live-progress tracking."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if row:
            now = _now()
            row.stage = stage
            row.stage_detail = detail
            if row.candidate_id is not None and row.finished_at is None:
                candidate = await session.get(ReadinessCandidateRow, row.candidate_id)
                if candidate is not None and candidate.state == "reviewing":
                    candidate.updated_at = now
            await session.commit()


async def update_review_pr_metadata(review_id: uuid.UUID, pr: PlatformPR) -> None:
    """Persist hydrated PR metadata (title, author, branches, head SHA) onto an existing review row.

    Manual reviews create the row from a URL stub before hydration, so the
    initial insert has empty title/author/branch fields. Call this once the
    real PR has been fetched so the dashboard queue can show the real title
    instead of falling back to the PR number.
    """
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            return
        if pr.title:
            row.title = pr.title
        if pr.author:
            row.author = pr.author
        if pr.source_branch:
            row.source_branch = pr.source_branch
        if pr.target_branch:
            row.target_branch = pr.target_branch
        if pr.head_commit_sha:
            row.head_commit_sha = pr.head_commit_sha
        await session.commit()


async def append_review_log_entry(review_id: uuid.UUID, entry: dict[str, Any]) -> bool:
    """Append a structured event onto a review's pipeline_log. Returns True on success."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            return False
        existing = list(row.pipeline_log or [])
        existing.append(entry)
        row.pipeline_log = existing
        await session.commit()
        return True


async def mark_review_failed(
    review_id: uuid.UUID,
    error: str,
    pipeline_log: list[dict] | None = None,
) -> None:
    """Mark a review as failed so it no longer appears as active."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if row:
            now = _now()
            row.stage = "error"
            row.stage_detail = error[:500]
            row.decision = "error"
            row.finished_at = now
            if pipeline_log is not None:
                row.pipeline_log = pipeline_log
            if row.started_at:
                row.duration_ms = int((now - _ensure_aware(row.started_at)).total_seconds() * 1000)
            if row.candidate_id is not None:
                candidate = await session.get(ReadinessCandidateRow, row.candidate_id)
                if (
                    candidate is not None
                    and candidate.state == "reviewing"
                    and (
                        not candidate.head_sha
                        or not row.head_commit_sha
                        or candidate.head_sha == row.head_commit_sha
                    )
                ):
                    snapshot = {
                        **(candidate.readiness_snapshot or {}),
                        "review_failure": {
                            "review_id": str(review_id),
                            "failed_at": now.isoformat(),
                            "error": error[:500],
                        },
                    }
                    transition = ReadinessCandidateTransitionRow(
                        candidate_id=candidate.id,
                        from_state=candidate.state,
                        to_state="error",
                        source="review_failure",
                        actor="guardian",
                        reason="review_failed",
                        readiness_snapshot=snapshot,
                    )
                    candidate.state = "error"
                    candidate.reason = "review_failed"
                    candidate.readiness_snapshot = snapshot
                    candidate.updated_at = now
                    session.add(transition)
            await session.commit()


async def save_review_result(review_id: uuid.UUID, result: ReviewResult) -> None:
    """Persist the full review result once the pipeline finishes."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            log.warning("review_row_not_found", review_id=str(review_id))
            return

        now = datetime.now(timezone.utc)
        row.risk_tier = result.risk_tier.value
        row.repo_risk_class = result.repo_risk_class.value
        row.trust_tier = result.trust_tier.value if result.trust_tier else ""
        row.trust_tier_details = (
            {
                "files": result.trust_tier_files,
                "reviewer_group_override": result.reviewer_group_override,
                "escalated_from": result.escalated_from,
            }
            if result.trust_tier
            else None
        )
        row.combined_score = result.combined_score
        row.decision = result.decision.value
        row.mechanical_passed = result.mechanical_passed
        row.override_reasons = {
            "sticky_triggers": [asdict(t) for t in result.sticky_triggers],
            "finding_reasons": result.finding_reasons,
            "gate_read": result.gate_read,
            "auto_approve_unlocked": result.auto_approve_unlocked,
        }
        row.summary = result.summary
        row.pipeline_log = result.pipeline_log
        row.total_input_tokens = result.total_input_tokens
        row.total_output_tokens = result.total_output_tokens
        row.cost_usd = result.cost_usd
        row.stage = "complete"
        row.finished_at = now
        if result.postback_meta:
            row.postback_meta = result.postback_meta
        if row.started_at:
            row.duration_ms = int((now - _ensure_aware(row.started_at)).total_seconds() * 1000)

        # Mechanical results
        for mech in result.mechanical_results:
            session.add(
                MechanicalResultRow(
                    review_id=review_id,
                    tool=mech.tool,
                    passed=mech.passed,
                    severity=mech.severity,
                    findings=mech.findings,
                    error=mech.error,
                )
            )

        # Agent results + findings
        for ar in result.agent_results:
            ar_row = AgentResultRow(
                review_id=review_id,
                agent_name=ar.agent_name[:64],
                verdict=ar.verdict.value[:16],
                languages_reviewed=ar.languages_reviewed,
                error=ar.error,
                verdict_explanation=ar.verdict_explanation,
            )
            session.add(ar_row)
            await session.flush()  # get the id

            for f in ar.findings:
                session.add(
                    FindingRow(
                        agent_result_id=ar_row.id,
                        severity=f.severity.value[:16],
                        certainty=f.certainty.value[:16],
                        category=f.category[:128],
                        language=f.language[:32],
                        file=f.file,
                        line=f.line,
                        description=f.description,
                        suggestion=f.suggestion,
                        cwe=f.cwe[:32] if f.cwe else None,
                    )
                )

        await session.commit()
        log.info("review_result_saved", review_id=str(review_id), decision=result.decision.value)


# ---------------------------------------------------------------------------
# Read operations (dashboard queries)
# ---------------------------------------------------------------------------


async def get_review(review_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a single review with all nested data."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            return None
        return _review_to_dict(row)


async def list_reviews(
    limit: int = 50,
    offset: int = 0,
    repo: str | None = None,
    decision: str | None = None,
    author: str | None = None,
) -> list[dict[str, Any]]:
    """List reviews with optional filters, newest first."""
    async with async_session() as session:
        q = select(ReviewRow).order_by(ReviewRow.started_at.desc())
        if repo:
            q = q.where(ReviewRow.repo == repo)
        if decision:
            q = q.where(ReviewRow.decision == decision)
        if author:
            q = q.where(ReviewRow.author == author)
        q = q.offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_review_to_dict(r) for r in rows]


async def find_review_by_pr_url(pr_url: str) -> dict[str, Any] | None:
    """Find the most recent completed review for a given PR URL."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(ReviewRow.pr_url == pr_url)
            .where(ReviewRow.finished_at.isnot(None))
            .order_by(ReviewRow.finished_at.desc())
        )
        row = (await session.scalars(q)).first()
        return _review_to_dict(row) if row else None


async def find_latest_review_for_pr(platform: str, repo: str, pr_id: str) -> dict[str, Any] | None:
    """Find the most recent completed review for a platform PR key."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(ReviewRow.platform == platform)
            .where(ReviewRow.repo == repo)
            .where(ReviewRow.pr_id == str(pr_id))
            .where(ReviewRow.finished_at.isnot(None))
            .order_by(ReviewRow.finished_at.desc())
        )
        row = (await session.scalars(q)).first()
        return _review_to_dict(row) if row else None


async def get_active_reviews() -> list[dict[str, Any]]:
    """Get reviews that haven't finished yet (live progress)."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(ReviewRow.finished_at.is_(None))
            .order_by(ReviewRow.started_at.desc())
        )
        rows = (await session.scalars(q)).all()
        return [_review_to_dict(r) for r in rows]


async def get_stats() -> dict[str, Any]:
    """Aggregate stats for the dashboard."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(ReviewRow.id)))

        decision_counts: dict[str, int] = {}
        for decision_val in ("auto_approve", "human_review", "reject", "hard_block"):
            c = await session.scalar(
                select(func.count(ReviewRow.id)).where(ReviewRow.decision == decision_val)
            )
            decision_counts[decision_val] = c or 0

        risk_tier_counts: dict[str, int] = {}
        for tier in ("trivial", "low", "medium", "high"):
            c = await session.scalar(
                select(func.count(ReviewRow.id)).where(ReviewRow.risk_tier == tier)
            )
            risk_tier_counts[tier] = c or 0

        avg_score = await session.scalar(
            select(func.avg(ReviewRow.combined_score)).where(ReviewRow.decision != "pending")
        )
        avg_duration = await session.scalar(
            select(func.avg(ReviewRow.duration_ms)).where(ReviewRow.duration_ms.isnot(None))
        )
        avg_cost = await session.scalar(
            select(func.avg(ReviewRow.cost_usd)).where(ReviewRow.decision != "pending")
        )
        total_cost = await session.scalar(
            select(func.sum(ReviewRow.cost_usd)).where(ReviewRow.decision != "pending")
        )

        # Top repos by review count
        top_repos_q = (
            select(ReviewRow.repo, func.count(ReviewRow.id).label("cnt"))
            .group_by(ReviewRow.repo)
            .order_by(func.count(ReviewRow.id).desc())
            .limit(10)
        )
        top_repos = (await session.execute(top_repos_q)).all()

        # Finding severity distribution
        severity_counts: dict[str, int] = {}
        for sev in ("low", "medium", "high", "critical"):
            c = await session.scalar(
                select(func.count(FindingRow.id)).where(FindingRow.severity == sev)
            )
            severity_counts[sev] = c or 0

        pending = await session.scalar(
            select(func.count(ReviewRow.id)).where(ReviewRow.finished_at.is_(None))
        )

        # Per-day cost for the last 30 days (Brief 07 — cost-over-time chart).
        # Returned as a list of {date: "YYYY-MM-DD", cost: <usd>}, oldest first,
        # with zero-filled days so the client can render a continuous strip.
        cost_per_day: list[dict] = []
        try:
            from datetime import date, timedelta

            today = date.today()
            window_start = today - timedelta(days=29)
            day_col = func.date(ReviewRow.started_at).label("d")
            rows = (
                await session.execute(
                    select(day_col, func.coalesce(func.sum(ReviewRow.cost_usd), 0.0))
                    .where(ReviewRow.started_at.isnot(None))
                    .where(func.date(ReviewRow.started_at) >= window_start)
                    .group_by(day_col)
                )
            ).all()
            day_map = {str(r[0]): float(r[1] or 0.0) for r in rows}
            for i in range(30):
                d = window_start + timedelta(days=i)
                cost_per_day.append(
                    {"date": d.isoformat(), "cost": round(day_map.get(d.isoformat(), 0.0), 4)}
                )
        except Exception:
            cost_per_day = []

        return {
            "total_reviews": total or 0,
            "active_reviews": pending or 0,
            "decision_counts": decision_counts,
            "risk_tier_counts": risk_tier_counts,
            "severity_counts": severity_counts,
            "avg_score": round(avg_score, 2) if avg_score else 0.0,
            "avg_duration_ms": int(avg_duration) if avg_duration else 0,
            "avg_cost_usd": round(avg_cost, 4) if avg_cost else 0.0,
            "total_cost_usd": round(total_cost, 4) if total_cost else 0.0,
            "top_repos": [{"repo": r[0], "count": r[1]} for r in top_repos],
            "cost_per_day": cost_per_day,
        }


# ---------------------------------------------------------------------------
# Prompt overrides
# ---------------------------------------------------------------------------


async def get_prompt_override(agent_name: str) -> str | None:
    """Return the override content for an agent, or None if no override exists."""
    try:
        async with async_session() as session:
            row = await session.get(PromptOverrideRow, agent_name)
            return row.content if row else None
    except Exception:
        return None


# Known agent names — used as fallback when prompts dir is missing (e.g. in Docker)
_KNOWN_AGENTS = [
    "architecture_intent",
    "code_quality_observability",
    "hotspot",
    "performance",
    "security_privacy",
    "test_quality",
]


async def get_all_prompts() -> list[dict[str, Any]]:
    """Return all agent prompts with override status and file defaults."""
    from pr_guardian.agents.prompt_composer import PROMPTS_DIR, load_prompt

    # Discover agents from prompt files, fall back to known list
    discovered = sorted(p.parent.name for p in PROMPTS_DIR.glob("*/base.md"))
    agents = discovered or _KNOWN_AGENTS

    overrides: dict[str, PromptOverrideRow] = {}
    try:
        async with async_session() as session:
            overrides = {
                r.agent_name: r for r in (await session.scalars(select(PromptOverrideRow))).all()
            }
    except Exception:
        log.warning("prompt_overrides_table_missing")

    result = []
    for name in agents:
        default_content = load_prompt(f"{name}/base.md") or ""
        ovr = overrides.get(name)
        result.append(
            {
                "agent_name": name,
                "content": ovr.content if ovr else default_content,
                "default_content": default_content,
                "is_override": ovr is not None,
                "updated_at": ovr.updated_at.isoformat() if ovr else None,
            }
        )
    return result


async def set_prompt_override(agent_name: str, content: str) -> None:
    """Create or update a prompt override for an agent."""
    async with async_session() as session:
        row = await session.get(PromptOverrideRow, agent_name)
        if row:
            row.content = content
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(PromptOverrideRow(agent_name=agent_name, content=content))
        await session.commit()


async def delete_prompt_override(agent_name: str) -> bool:
    """Delete a prompt override, reverting to the file default. Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(PromptOverrideRow, agent_name)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# Global config (dashboard settings)
# ---------------------------------------------------------------------------


async def get_global_config() -> dict[str, str]:
    """Return all global config key-value pairs (secrets are decrypted)."""
    from pr_guardian.persistence.crypto import SECRET_KEYS, decrypt

    try:
        async with async_session() as session:
            rows = (await session.scalars(select(GlobalConfigRow))).all()
            result: dict[str, str] = {}
            for r in rows:
                result[r.key] = decrypt(r.value) if r.key in SECRET_KEYS else r.value
            return result
    except Exception:
        return {}


async def set_global_config(key: str, value: str) -> None:
    """Create or update a global config entry (secrets are encrypted)."""
    from pr_guardian.persistence.crypto import SECRET_KEYS, encrypt

    stored = encrypt(value) if key in SECRET_KEYS else value

    async with async_session() as session:
        row = await session.get(GlobalConfigRow, key)
        if row:
            row.value = stored
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(GlobalConfigRow(key=key, value=stored))
        await session.commit()


async def delete_global_config(key: str) -> bool:
    """Delete a global config entry. Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(GlobalConfigRow, key)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def resolve_github_token(pat_name: str | None = None) -> str:
    """Deprecated: GitHub runtime auth uses App Connections.

    Use ``build_github_adapter_from_connection()`` from
    ``pr_guardian.platform.github_auth`` instead.  The GITHUB_TOKEN env
    fallback has been removed — this function now raises ``LookupError`` when
    no stored token is found.
    """
    from pr_guardian.persistence.crypto import decrypt

    try:
        async with async_session() as session:
            if pat_name:
                row = (
                    await session.scalars(
                        select(ConnectionRow)
                        .where(ConnectionRow.platform == "github")
                        .where(ConnectionRow.name == pat_name)
                        .where(ConnectionRow.archived_at.is_(None))
                    )
                ).first()
                if not row:
                    raise LookupError(f"GitHub PAT not found: {pat_name!r}")
                decrypted = decrypt(row.encrypted_token or "")
                if not decrypted:
                    raise LookupError(f"GitHub PAT {pat_name!r} has a corrupted token")
                return decrypted
            else:
                row = (
                    await session.scalars(
                        select(ConnectionRow)
                        .where(ConnectionRow.platform == "github")
                        .where(ConnectionRow.is_default.is_(True))
                        .where(ConnectionRow.archived_at.is_(None))
                    )
                ).first()
                if row and row.encrypted_token:
                    decrypted = decrypt(row.encrypted_token)
                    if decrypted:
                        return decrypted
    except LookupError:
        raise
    except Exception:
        log.warning(
            "resolve_github_token_failed",
            hint="DB unavailable or decrypt error",
        )
    raise LookupError(
        "No GitHub token found in stored Connections. "
        "GITHUB_TOKEN env fallback has been removed — "
        "add a GitHub App Connection via the Connections UI."
    )


# ---------------------------------------------------------------------------
# Finding dismissals (feedback loop)
# ---------------------------------------------------------------------------


def _hash16(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def finding_signature(file: str, category: str, agent_name: str) -> str:
    """Stable hash that survives line-number shifts."""
    return _hash16(f"{file}::{category}::{agent_name}")


async def upsert_dismissal(
    pr_id: str,
    repo: str,
    platform: str,
    finding: dict,
    agent_name: str,
    status: str,
    comment: str,
) -> uuid.UUID:
    """Create or update a dismissal. Computes signature from finding fields."""
    sig = finding_signature(finding["file"], finding["category"], agent_name)
    source = {
        "file": finding.get("file", ""),
        "line": finding.get("line"),
        "category": finding.get("category", ""),
        "agent_name": agent_name,
        "severity": finding.get("severity", ""),
        "certainty": finding.get("certainty", ""),
        "description": (finding.get("description", "") or "")[:500],
    }
    async with async_session() as session:
        # Check for existing active dismissal with same signature
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.signature == sig)
            .where(FindingDismissalRow.active.is_(True))
        )
        existing = (await session.scalars(q)).first()
        if existing:
            existing.status = status
            existing.comment = comment
            existing.source_finding = source
            existing.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return existing.id

        row = FindingDismissalRow(
            pr_id=pr_id,
            repo=repo,
            platform=platform,
            signature=sig,
            status=status,
            comment=comment,
            source_finding=source,
            active=True,
        )
        session.add(row)
        await session.commit()
        return row.id


async def remove_dismissal(dismissal_id: uuid.UUID) -> bool:
    """Delete a dismissal (un-dismiss). Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(FindingDismissalRow, dismissal_id)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def get_active_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
) -> list[dict[str, Any]]:
    """All active dismissals for a PR."""
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(True))
        )
        rows = (await session.scalars(q)).all()
        return [_dismissal_to_dict(r) for r in rows]


async def get_archived_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
) -> list[dict[str, Any]]:
    """Inactive (archived) dismissals for a PR — findings resolved in later reviews."""
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(False))
            .order_by(FindingDismissalRow.updated_at.desc())
        )
        rows = (await session.scalars(q)).all()
        return [_dismissal_to_dict(r) for r in rows]


async def match_dismissals_to_findings(
    pr_id: str,
    repo: str,
    platform: str,
    findings_with_agent: list[dict],
) -> dict[str, dict]:
    """Returns {signature: dismissal_dict} for findings that match an active dismissal."""
    dismissals = await get_active_dismissals(pr_id, repo, platform)
    sig_map = {d["signature"]: d for d in dismissals}

    matched: dict[str, dict] = {}
    for f in findings_with_agent:
        sig = finding_signature(f["file"], f["category"], f["agent_name"])
        if sig in sig_map:
            matched[sig] = sig_map[sig]
    return matched


async def archive_stale_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
    active_signatures: set[str],
) -> int:
    """Mark dismissals as inactive if their signature didn't appear in the latest review."""
    count = 0
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(True))
        )
        rows = (await session.scalars(q)).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.signature not in active_signatures:
                row.active = False
                row.updated_at = now
                count += 1
        if count:
            await session.commit()
    return count


def _dismissal_to_dict(row: FindingDismissalRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pr_id": row.pr_id,
        "repo": row.repo,
        "platform": row.platform,
        "signature": row.signature,
        "status": row.status,
        "comment": row.comment,
        "source_finding": row.source_finding,
        "active": row.active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Finding lifecycle
# ---------------------------------------------------------------------------


class FindingState(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    FIXED = "fixed"
    REGRESSED = "regressed"
    VERIFIED = "verified"


def _row_to_finding_state(row: FindingDismissalRow) -> FindingState:
    rk = row.resolution_kind
    if rk == FindingState.VERIFIED:
        return FindingState.VERIFIED
    if rk == FindingState.REGRESSED:
        return FindingState.REGRESSED
    if rk == FindingState.FIXED:
        return FindingState.FIXED
    return FindingState.DISMISSED


async def _get_or_create_dismissal_row(
    session: AsyncSession,
    pr_id: str,
    signature: str,
) -> FindingDismissalRow:
    q = (
        select(FindingDismissalRow)
        .where(FindingDismissalRow.pr_id == pr_id)
        .where(FindingDismissalRow.signature == signature)
    )
    row = (await session.scalars(q)).first()
    if row is None:
        row = FindingDismissalRow(
            pr_id=pr_id,
            repo="",
            platform="",
            signature=signature,
            status="",
        )
        session.add(row)
    return row


async def _update_finding_row(
    pr_id: str,
    signature: str,
    update_fn: Callable[[FindingDismissalRow, datetime], None],
    warning_key: str,
) -> None:
    try:
        async with async_session() as session:
            row = await _get_or_create_dismissal_row(session, pr_id, signature)
            if row.resolution_kind == FindingState.VERIFIED:
                return
            now = datetime.now(timezone.utc)
            update_fn(row, now)
            row.updated_at = now
            await session.commit()
    except Exception:
        log.warning(warning_key, hint="DB unavailable")


async def mark_fixed(pr_id: str, signature: str, fixed_by_sha: str) -> None:
    def _apply(row: FindingDismissalRow, now: datetime) -> None:
        row.resolution_kind = FindingState.FIXED
        row.fixed_by_sha = fixed_by_sha
        row.fixed_at = now

    await _update_finding_row(pr_id, signature, _apply, "mark_fixed_failed")


async def mark_regressed(pr_id: str, signature: str, sha: str, prev_sha: str) -> None:
    def _apply(row: FindingDismissalRow, now: datetime) -> None:
        row.resolution_kind = FindingState.REGRESSED
        row.regressed_at = now
        row.regressed_from_sha = prev_sha

    await _update_finding_row(pr_id, signature, _apply, "mark_regressed_failed")


async def mark_verified(pr_id: str, signature: str, user: str) -> None:
    def _apply(row: FindingDismissalRow, now: datetime) -> None:
        row.resolution_kind = FindingState.VERIFIED
        row.verified_by = user
        row.verified_at = now

    await _update_finding_row(pr_id, signature, _apply, "mark_verified_failed")


async def get_finding_states(pr_id: str) -> dict[str, FindingState]:
    try:
        async with async_session() as session:
            q = select(FindingDismissalRow).where(FindingDismissalRow.pr_id == pr_id)
            rows = (await session.scalars(q)).all()
        return {r.signature: _row_to_finding_state(r) for r in rows}
    except Exception:
        log.warning("get_finding_states_failed", hint="DB unavailable")
        return {}


async def infer_fixes(
    pr_id: str,
    prev_sigs: set[str],
    current_sigs: set[str],
    current_sha: str,
) -> tuple[set[str], set[str]]:
    try:
        async with async_session() as session:
            q = select(FindingDismissalRow).where(FindingDismissalRow.pr_id == pr_id)
            rows = (await session.scalars(q)).all()

        state_map = {r.signature: _row_to_finding_state(r) for r in rows}
        sha_map = {r.signature: r.fixed_by_sha for r in rows}

        previously_fixed = {sig for sig, s in state_map.items() if s == FindingState.FIXED}
        verified_sigs = {sig for sig, s in state_map.items() if s == FindingState.VERIFIED}

        newly_fixed = (prev_sigs - current_sigs) - verified_sigs
        regressed = previously_fixed & current_sigs

        for sig in newly_fixed:
            await mark_fixed(pr_id, sig, current_sha)
        for sig in regressed:
            await mark_regressed(pr_id, sig, current_sha, sha_map.get(sig) or current_sha)

        return newly_fixed, regressed
    except Exception:
        log.warning("infer_fixes_failed", hint="DB unavailable")
        return set(), set()


async def verify_sticky_trigger(
    pr_id: str,
    trigger_kind: str,
    trigger_source: str,
    user: str,
) -> None:
    sig = _hash16(f"{pr_id}::{trigger_kind}::{trigger_source}")
    await mark_verified(pr_id, sig, user)


# ---------------------------------------------------------------------------
# Scan operations
# ---------------------------------------------------------------------------


async def create_scan_record(
    scan_type: str,
    repo: str,
    platform: str,
    time_window_days: int = 7,
    staleness_months: int = 6,
    base_sha: str = "",
    head_sha: str = "",
) -> uuid.UUID:
    """Insert a pending scan row when a scan starts."""
    row = ScanRow(
        scan_type=scan_type,
        repo=repo,
        platform=platform,
        time_window_days=time_window_days,
        staleness_months=staleness_months,
        base_sha=base_sha,
        head_sha=head_sha,
        stage="discovery",
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        log.debug("scan_record_created", scan_id=str(row.id), scan_type=scan_type)
        return row.id


async def update_scan_stage(scan_id: uuid.UUID, stage: str, detail: str = "") -> None:
    """Update the pipeline stage for live-progress tracking."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if row:
            row.stage = stage
            row.stage_detail = detail
            await session.commit()


async def mark_scan_failed(
    scan_id: uuid.UUID,
    error: str,
    pipeline_log: list[dict] | None = None,
) -> None:
    """Mark a scan as failed."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if row:
            now = datetime.now(timezone.utc)
            row.stage = "error"
            row.stage_detail = error[:500]
            row.finished_at = now
            if pipeline_log is not None:
                row.pipeline_log = pipeline_log
            if row.started_at:
                row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
            await session.commit()


async def save_scan_result(scan_id: uuid.UUID, result) -> None:
    """Persist the full scan result once the pipeline finishes.

    Accepts a ScanResult dataclass from models.scan.
    """
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if not row:
            log.warning("scan_row_not_found", scan_id=str(scan_id))
            return

        now = datetime.now(timezone.utc)
        row.total_findings = result.total_findings
        row.summary = result.summary
        if getattr(result, "base_sha", ""):
            row.base_sha = result.base_sha
        if getattr(result, "head_sha", ""):
            row.head_sha = result.head_sha
        row.pipeline_log = result.pipeline_log
        row.total_input_tokens = result.total_input_tokens
        row.total_output_tokens = result.total_output_tokens
        row.cost_usd = result.cost_usd
        row.stage = "complete"
        row.finished_at = now
        if row.started_at:
            row.duration_ms = int((now - row.started_at).total_seconds() * 1000)

        for ar in result.agent_results:
            ar_row = ScanAgentResultRow(
                scan_id=scan_id,
                agent_name=ar.agent_name,
                verdict=ar.verdict.value,
                summary=ar.summary,
                error=ar.error,
            )
            session.add(ar_row)
            await session.flush()

            for f in ar.findings:
                session.add(
                    ScanFindingRow(
                        agent_result_id=ar_row.id,
                        severity=f.severity.value,
                        certainty=f.certainty.value,
                        category=f.category,
                        file=f.file,
                        line=f.line,
                        description=f.description,
                        suggestion=f.suggestion,
                        priority=f.priority,
                        last_modified=f.last_modified,
                        effort_estimate=f.effort_estimate,
                    )
                )

        await session.commit()
        log.info("scan_result_saved", scan_id=str(scan_id), scan_type=result.scan_type.value)


async def get_scan(scan_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a single scan with all nested data."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if not row:
            return None
        return _scan_to_dict(row)


async def list_scans(
    limit: int = 50,
    offset: int = 0,
    repo: str | None = None,
    scan_type: str | None = None,
) -> list[dict[str, Any]]:
    """List scans with optional filters, newest first."""
    async with async_session() as session:
        q = select(ScanRow).order_by(ScanRow.started_at.desc())
        if repo:
            q = q.where(ScanRow.repo == repo)
        if scan_type:
            q = q.where(ScanRow.scan_type == scan_type)
        q = q.offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_scan_to_dict(r) for r in rows]


async def get_scan_stats() -> dict[str, Any]:
    """Aggregate stats for scans."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(ScanRow.id))) or 0

        type_counts: dict[str, int] = {}
        for st in ("recent_changes", "recent_changes_deep", "maintenance"):
            c = await session.scalar(select(func.count(ScanRow.id)).where(ScanRow.scan_type == st))
            type_counts[st] = c or 0

        total_cost = await session.scalar(
            select(func.sum(ScanRow.cost_usd)).where(ScanRow.stage == "complete")
        )
        avg_cost = await session.scalar(
            select(func.avg(ScanRow.cost_usd)).where(ScanRow.stage == "complete")
        )

        severity_counts: dict[str, int] = {}
        for sev in ("low", "medium", "high", "critical"):
            c = await session.scalar(
                select(func.count(ScanFindingRow.id)).where(ScanFindingRow.severity == sev)
            )
            severity_counts[sev] = c or 0

        return {
            "total_scans": total,
            "type_counts": type_counts,
            "severity_counts": severity_counts,
            "total_cost_usd": round(total_cost, 4) if total_cost else 0.0,
            "avg_cost_usd": round(avg_cost, 4) if avg_cost else 0.0,
        }


async def create_scan_issue(
    scan_id: uuid.UUID,
    finding_ids: list[str],
    issue_url: str,
    issue_number: str,
    title: str,
    platform: str,
    repo: str,
) -> uuid.UUID:
    """Persist a platform issue that was created from scan findings."""
    row = ScanIssueRow(
        scan_id=scan_id,
        finding_ids=finding_ids,
        issue_url=issue_url,
        issue_number=str(issue_number),
        title=title,
        platform=platform,
        repo=repo,
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        return row.id


async def get_scan_issues(scan_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return all issues created for a given scan."""
    async with async_session() as session:
        q = select(ScanIssueRow).where(ScanIssueRow.scan_id == scan_id)
        rows = (await session.scalars(q)).all()
        return [
            {
                "id": str(r.id),
                "scan_id": str(r.scan_id),
                "finding_ids": r.finding_ids or [],
                "issue_url": r.issue_url,
                "issue_number": r.issue_number,
                "title": r.title,
                "platform": r.platform,
                "repo": r.repo,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def _scan_to_dict(row: ScanRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scan_type": row.scan_type,
        "repo": row.repo,
        "platform": row.platform,
        "time_window_days": row.time_window_days,
        "staleness_months": row.staleness_months,
        "base_sha": row.base_sha,
        "head_sha": row.head_sha,
        "total_findings": row.total_findings,
        "summary": row.summary,
        "stage": row.stage,
        "stage_detail": row.stage_detail,
        "pipeline_log": row.pipeline_log or [],
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "cost_usd": row.cost_usd,
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "profile_snapshot": row.profile_snapshot,
        "connection_id": str(row.connection_id) if row.connection_id else None,
        "connection_snapshot": row.connection_snapshot,
        "repo_link_id": str(row.repo_link_id) if row.repo_link_id else None,
        "scan_source": row.scan_source,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": row.duration_ms,
        "agent_results": [
            {
                "agent_name": a.agent_name,
                "verdict": a.verdict,
                "summary": a.summary,
                "error": a.error,
                "findings": [
                    {
                        "id": str(f.id),
                        "severity": f.severity,
                        "certainty": f.certainty,
                        "category": f.category,
                        "file": f.file,
                        "line": f.line,
                        "description": f.description,
                        "suggestion": f.suggestion,
                        "priority": f.priority,
                        "last_modified": f.last_modified,
                        "effort_estimate": f.effort_estimate,
                    }
                    for f in a.findings
                ],
            }
            for a in row.agent_results
        ],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unpack_override_reasons(raw: dict | list | None) -> dict:
    """Convert the stored override_reasons blob to the new split-field shape.

    New rows store a dict with sticky_triggers + finding_reasons.
    Legacy rows may store a plain list; treat those as finding_reasons.
    """
    if isinstance(raw, dict):
        return {
            "sticky_triggers": raw.get("sticky_triggers", []),
            "finding_reasons": raw.get("finding_reasons", []),
            "gate_read": raw.get("gate_read"),
            # Legacy rows predate the gate → default locked (safe: re-review of an
            # unconfigured repo stays human rather than newly auto-approving).
            "auto_approve_unlocked": raw.get("auto_approve_unlocked", False),
        }
    if isinstance(raw, list):
        return {
            "sticky_triggers": [],
            "finding_reasons": raw,
            "gate_read": None,
            "auto_approve_unlocked": False,
        }
    return {
        "sticky_triggers": [],
        "finding_reasons": [],
        "gate_read": None,
        "auto_approve_unlocked": False,
    }


def _review_to_dict(row: ReviewRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pr_id": row.pr_id,
        "repo": row.repo,
        "platform": row.platform,
        "author": row.author,
        "title": row.title,
        "source_branch": row.source_branch,
        "target_branch": row.target_branch,
        "head_commit_sha": row.head_commit_sha,
        "pr_url": row.pr_url,
        "risk_tier": row.risk_tier,
        "repo_risk_class": row.repo_risk_class,
        "trust_tier": row.trust_tier,
        "trust_tier_details": row.trust_tier_details,
        "combined_score": row.combined_score,
        "decision": row.decision,
        "mechanical_passed": row.mechanical_passed,
        **_unpack_override_reasons(row.override_reasons),
        "summary": row.summary,
        "stage": row.stage,
        "stage_detail": row.stage_detail,
        "pipeline_log": row.pipeline_log or [],
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "cost_usd": row.cost_usd,
        "comment_mode": row.comment_mode,
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "profile_snapshot": row.profile_snapshot,
        "connection_id": str(row.connection_id) if row.connection_id else None,
        "connection_snapshot": row.connection_snapshot,
        "repo_link_id": str(row.repo_link_id) if row.repo_link_id else None,
        "candidate_id": str(row.candidate_id) if row.candidate_id else None,
        "review_source": row.review_source,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": row.duration_ms,
        "postback_meta": row.postback_meta or {},
        "mechanical_results": [
            {
                "tool": m.tool,
                "passed": m.passed,
                "severity": m.severity,
                "findings": m.findings,
                "error": m.error,
            }
            for m in row.mechanical_results
        ],
        "agent_results": [
            {
                "agent_name": a.agent_name,
                "verdict": a.verdict,
                "languages_reviewed": a.languages_reviewed,
                "error": a.error,
                "verdict_explanation": a.verdict_explanation,
                "findings": [
                    {
                        "id": str(f.id),
                        "severity": f.severity,
                        "certainty": f.certainty,
                        "category": f.category,
                        "language": f.language,
                        "file": f.file,
                        "line": f.line,
                        "description": f.description,
                        "suggestion": f.suggestion,
                        "cwe": f.cwe,
                    }
                    for f in a.findings
                ],
            }
            for a in row.agent_results
        ],
    }


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------


async def is_admin(email: str) -> bool:
    """Check whether an email is in the admin list."""
    async with async_session() as session:
        row = await session.get(AdminRow, email.lower())
        return row is not None


async def list_admins() -> list[dict[str, Any]]:
    """Return all admin records."""
    async with async_session() as session:
        rows = (await session.scalars(select(AdminRow).order_by(AdminRow.created_at))).all()
        return [
            {"email": r.email, "added_by": r.added_by, "created_at": r.created_at.isoformat()}
            for r in rows
        ]


async def add_admin(email: str, added_by: str = "system") -> bool:
    """Add an admin. Returns False if already exists."""
    email = email.lower().strip()
    async with async_session() as session:
        existing = await session.get(AdminRow, email)
        if existing:
            return False
        session.add(AdminRow(email=email, added_by=added_by))
        await session.commit()
        return True


async def remove_admin(email: str) -> bool:
    """Remove an admin. Returns False if not found."""
    email = email.lower().strip()
    async with async_session() as session:
        row = await session.get(AdminRow, email)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def admin_count() -> int:
    """Return total number of admins."""
    async with async_session() as session:
        return await session.scalar(select(func.count()).select_from(AdminRow)) or 0


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(
    name: str,
    scopes: list[str],
    created_by: str,
    expires_in_days: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate an API key, store the hash, return (full_key, metadata).

    The full key is only returned once — it is never stored.
    """
    raw_key = "prg_" + secrets.token_hex(16)
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:8]
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None
    )

    row = ApiKeyRow(
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=scopes,
        created_by=created_by,
        expires_at=expires_at,
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        return raw_key, _api_key_to_dict(row)


async def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """Validate a raw API key. Returns key metadata or None if invalid.

    Updates last_used_at on success.
    """
    key_hash = _hash_key(raw_key)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        row = await session.scalar(select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash))
        if not row:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at < now:
            return None
        row.last_used_at = now
        await session.commit()
        return _api_key_to_dict(row)


async def list_api_keys() -> list[dict[str, Any]]:
    """List all API keys (hash never exposed)."""
    async with async_session() as session:
        rows = (
            await session.scalars(select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc()))
        ).all()
        return [_api_key_to_dict(r) for r in rows]


async def revoke_api_key(key_id: uuid.UUID) -> bool:
    """Revoke an API key. Returns False if not found."""
    async with async_session() as session:
        row = await session.get(ApiKeyRow, key_id)
        if not row:
            return False
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        return True


async def delete_api_key(key_id: uuid.UUID) -> bool:
    """Permanently delete an API key. Returns False if not found."""
    async with async_session() as session:
        row = await session.get(ApiKeyRow, key_id)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


def _api_key_to_dict(row: ApiKeyRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "key_prefix": row.key_prefix,
        "scopes": row.scopes,
        "created_by": row.created_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "created_at": row.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Inline comment helpers
# ---------------------------------------------------------------------------


async def save_inline_comment_ids(
    review_id: uuid.UUID,
    ids: list[str],
    platform: str,
    pr_id: str,
    repo: str,
    id_to_findings: dict[str, list[dict]] | None = None,
) -> None:
    """Persist platform-native comment IDs for a review.

    When ``id_to_findings`` is supplied, the finding payloads carried by each
    comment are stored too, so a reply to that comment can later be mapped back
    to the finding(s) for dismissal.
    """
    id_to_findings = id_to_findings or {}
    async with async_session() as session:
        for comment_id in ids:
            session.add(
                PostedInlineCommentRow(
                    review_id=review_id,
                    platform_comment_id=comment_id,
                    platform=platform,
                    pr_id=pr_id,
                    repo=repo,
                    findings=id_to_findings.get(comment_id, []),
                )
            )
        await session.commit()


async def find_inline_comment_by_platform_id(
    platform: str,
    repo: str,
    pr_id: str,
    platform_comment_id: str,
) -> dict[str, Any] | None:
    """Find a posted inline comment (and its finding payloads) by platform id.

    Used to resolve a reply's ``in_reply_to_id`` back to the originating Guardian
    comment and the finding(s) it carried.
    """
    async with async_session() as session:
        row = (
            await session.scalars(
                select(PostedInlineCommentRow)
                .where(
                    PostedInlineCommentRow.platform == platform,
                    PostedInlineCommentRow.repo == repo,
                    PostedInlineCommentRow.pr_id == str(pr_id),
                    PostedInlineCommentRow.platform_comment_id == str(platform_comment_id),
                )
                .order_by(PostedInlineCommentRow.created_at.desc())
            )
        ).first()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "review_id": str(row.review_id),
            "platform": row.platform,
            "repo": row.repo,
            "pr_id": row.pr_id,
            "platform_comment_id": row.platform_comment_id,
            "findings": row.findings or [],
        }


async def load_inline_comment_ids(review_id: uuid.UUID) -> list[str]:
    """Return all platform comment IDs previously saved for a review."""
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(PostedInlineCommentRow).where(PostedInlineCommentRow.review_id == review_id)
            )
        ).all()
        return [r.platform_comment_id for r in rows]


# ---------------------------------------------------------------------------
# Guidance comment helpers
# ---------------------------------------------------------------------------


async def load_guidance_comment_id(platform: str, repo: str, pr_id: str) -> str | None:
    """Return the stored platform comment ID for the sticky guidance comment, or None."""
    async with async_session() as session:
        row = (
            await session.scalars(
                select(GuidanceCommentRow).where(
                    GuidanceCommentRow.platform == platform,
                    GuidanceCommentRow.repo == repo,
                    GuidanceCommentRow.pr_id == pr_id,
                )
            )
        ).first()
        return row.comment_id if row else None


async def save_guidance_comment_id(platform: str, repo: str, pr_id: str, comment_id: str) -> None:
    """Upsert the guidance comment ID for a PR.

    Uses an atomic UPDATE-then-INSERT for non-PostgreSQL backends and
    INSERT … ON CONFLICT DO UPDATE for PostgreSQL, avoiding the TOCTOU
    race inherent in a SELECT-then-INSERT pattern.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.now(timezone.utc)
    async with async_session() as session:
        if _get_engine().dialect.name != "postgresql":
            result = await session.execute(
                sa_update(GuidanceCommentRow)
                .where(
                    GuidanceCommentRow.platform == platform,
                    GuidanceCommentRow.repo == repo,
                    GuidanceCommentRow.pr_id == pr_id,
                )
                .values(comment_id=comment_id, updated_at=now)
            )
            if result.rowcount == 0:
                try:
                    session.add(
                        GuidanceCommentRow(
                            platform=platform,
                            repo=repo,
                            pr_id=pr_id,
                            comment_id=comment_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    await session.commit()
                except IntegrityError:
                    # Concurrent INSERT won the race; tolerate — the comment ID is the same.
                    await session.rollback()
            else:
                await session.commit()
        else:
            stmt = pg_insert(GuidanceCommentRow).values(
                id=uuid.uuid4(),
                platform=platform,
                repo=repo,
                pr_id=pr_id,
                comment_id=comment_id,
                created_at=now,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_guidance_comment_pr",
                set_={"comment_id": comment_id, "updated_at": now},
            )
            await session.execute(stmt)
            await session.commit()


# ---------------------------------------------------------------------------
# ChatOps command helpers
# ---------------------------------------------------------------------------


async def claim_chatops_command(
    *,
    platform: str,
    repo: str,
    pr_id: str,
    command: str,
    external_id: str,
    source: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """Claim a platform command exactly once.

    Returns the new row id, or None when the command was already seen through
    another ingress path (webhook redelivery, polling, or both).
    """
    row = ChatOpsCommandRow(
        platform=platform,
        repo=repo,
        pr_id=str(pr_id),
        command=command,
        external_id=str(external_id),
        source=source,
        actor=actor,
        payload=payload or {},
    )
    async with async_session() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
        return row.id


async def update_chatops_command(
    command_id: uuid.UUID,
    *,
    status: str,
    status_detail: str = "",
    review_id: uuid.UUID | str | None = None,
) -> None:
    """Update the audit status for a claimed ChatOps command."""
    async with async_session() as session:
        row = await session.get(ChatOpsCommandRow, command_id)
        if not row:
            return
        row.status = status
        row.status_detail = status_detail
        if review_id:
            row.review_id = uuid.UUID(str(review_id))
        row.updated_at = _now()
        await session.commit()


# ---------------------------------------------------------------------------
# PR Dashboard: user identity, sync sources, cached open PRs
# ---------------------------------------------------------------------------

_STALE_DAYS = 5


async def get_user_identity(email: str) -> dict[str, Any] | None:
    """Return the GitHub handle + ADO UPN for a user, or None if not configured."""
    async with async_session() as session:
        row = await session.get(UserIdentityRow, email.lower())
        if not row:
            return None
        return {
            "email": row.email,
            "github_handle": row.github_handle,
            "ado_upn": row.ado_upn,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


async def upsert_user_identity(
    email: str,
    github_handle: str | None,
    ado_upn: str | None,
) -> None:
    """Create or update the identity mapping for a user."""
    email = email.lower().strip()
    async with async_session() as session:
        row = await session.get(UserIdentityRow, email)
        if row:
            row.github_handle = github_handle or None
            row.ado_upn = ado_upn or None
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(
                UserIdentityRow(
                    email=email,
                    github_handle=github_handle or None,
                    ado_upn=ado_upn or None,
                )
            )
        await session.commit()


async def upsert_sync_source(
    platform: str,
    org: str,
    project: str,
    repo: str,
    repo_url: str,
    connection_id: uuid.UUID | None = None,
    connection_snapshot: dict[str, Any] | None = None,
) -> None:
    """Register a repo as an active sync source."""
    async with async_session() as session:
        q = (
            select(SyncSourceRow)
            .where(SyncSourceRow.platform == platform)
            .where(SyncSourceRow.repo == repo)
            .where(SyncSourceRow.project == project)
        )
        row = (await session.scalars(q)).first()
        if row:
            row.is_active = True
            row.org = org
            row.repo_url = repo_url
            row.connection_id = connection_id
            row.connection_snapshot = connection_snapshot
        else:
            session.add(
                SyncSourceRow(
                    platform=platform,
                    org=org,
                    project=project,
                    repo=repo,
                    repo_url=repo_url,
                    connection_id=connection_id,
                    connection_snapshot=connection_snapshot,
                    is_active=True,
                )
            )
        await session.commit()


async def mark_sync_source_synced(platform: str, repo: str, project: str = "") -> None:
    """Update last_synced_at for a sync source."""
    async with async_session() as session:
        q = (
            select(SyncSourceRow)
            .where(SyncSourceRow.platform == platform)
            .where(SyncSourceRow.repo == repo)
            .where(SyncSourceRow.project == project)
        )
        row = (await session.scalars(q)).first()
        if row:
            row.last_synced_at = datetime.now(timezone.utc)
            await session.commit()


async def upsert_synced_pr(data: dict[str, Any]) -> None:
    """Create or update a cached PR record."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.now(timezone.utc)
    pr_created_at = _parse_dt(data.get("pr_created_at"))
    pr_updated_at = _parse_dt(data.get("pr_updated_at"))

    values = {
        "platform": data["platform"],
        "pr_id": str(data["pr_id"]),
        "org": data.get("org", ""),
        "project": data.get("project", ""),
        "repo": data.get("repo", ""),
        "title": data.get("title", ""),
        "author": data.get("author", ""),
        "author_display": data.get("author_display") or data.get("author", ""),
        "pr_url": data.get("pr_url", ""),
        "source_branch": data.get("source_branch", ""),
        "target_branch": data.get("target_branch", ""),
        "is_draft": bool(data.get("is_draft", False)),
        "has_conflicts": bool(data.get("has_conflicts", False)),
        "approval_status": data.get("approval_status", "pending"),
        "reviewers": data.get("reviewers") or [],
        "assignees": data.get("assignees") or [],
        "comment_count": int(data.get("comment_count", 0)),
        "ci_status": data.get("ci_status", "unknown"),
        "profile_id": data.get("profile_id"),
        "profile_snapshot": data.get("profile_snapshot"),
        "connection_id": data.get("connection_id"),
        "connection_snapshot": data.get("connection_snapshot"),
        "repo_link_id": data.get("repo_link_id"),
        "sync_source": data.get("sync_source", "sync"),
        "pr_created_at": pr_created_at,
        "pr_updated_at": pr_updated_at,
        "synced_at": now,
    }

    async with async_session() as session:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            existing = (
                await session.scalars(
                    select(SyncedPRRow)
                    .where(SyncedPRRow.platform == values["platform"])
                    .where(SyncedPRRow.pr_id == values["pr_id"])
                    .where(SyncedPRRow.repo == values["repo"])
                    .where(SyncedPRRow.project == values["project"])
                )
            ).first()
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                session.add(SyncedPRRow(**values))
            await session.commit()
            return

        stmt = pg_insert(SyncedPRRow).values(id=uuid.uuid4(), **values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_synced_pr",
            set_={
                k: v
                for k, v in values.items()
                if k not in ("platform", "pr_id", "repo", "project")
            },
        )
        await session.execute(stmt)
        await session.commit()


async def delete_closed_prs(
    platform: str, repo: str, project: str, keep_pr_ids: list[str]
) -> None:
    """Remove non-merged PRs that are no longer in the keep list for the given repo.

    Merged PRs are preserved here so a transient failure on the merged-PR fetch
    cannot wipe them; they age out via ``purge_old_merged_prs`` instead.
    """
    from sqlalchemy import delete

    async with async_session() as session:
        q = (
            delete(SyncedPRRow)
            .where(SyncedPRRow.platform == platform)
            .where(SyncedPRRow.repo == repo)
            .where(SyncedPRRow.project == project)
            .where(SyncedPRRow.approval_status != "merged")
        )
        if keep_pr_ids:
            q = q.where(SyncedPRRow.pr_id.notin_(keep_pr_ids))
        await session.execute(q)
        await session.commit()


async def purge_old_merged_prs(retention_days: int) -> int:
    """Delete merged PRs whose pr_updated_at is older than ``retention_days``."""
    from sqlalchemy import delete

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with async_session() as session:
        result = await session.execute(
            delete(SyncedPRRow)
            .where(SyncedPRRow.approval_status == "merged")
            .where(SyncedPRRow.pr_updated_at < cutoff)
        )
        await session.commit()
        return result.rowcount or 0


async def purge_prs_from_inactive_connections() -> int:
    """Delete open PRs linked to connections that are archived or sync-disabled.

    These connections are permanently excluded from sync so their PRs would
    accumulate forever without this cleanup pass.
    """
    from sqlalchemy import delete

    active_conn_subq = (
        select(ConnectionRow.id)
        .where(ConnectionRow.archived_at.is_(None))
        .where(ConnectionRow.sync_enabled.is_(True))
    )
    async with async_session() as session:
        result = await session.execute(
            delete(SyncedPRRow)
            .where(SyncedPRRow.connection_id.isnot(None))
            .where(SyncedPRRow.connection_id.notin_(active_conn_subq))
            .where(SyncedPRRow.approval_status != "merged")
        )
        await session.commit()
        return result.rowcount or 0


async def get_synced_pr(pr_uuid: str) -> dict[str, Any] | None:
    """Fetch a single synced PR by its UUID."""
    async with async_session() as session:
        try:
            row = await session.get(SyncedPRRow, uuid.UUID(pr_uuid))
        except ValueError:
            return None
        if not row:
            return None
        d = _synced_pr_to_dict(row)
        # Enrich with completed review info
        review_row = await session.scalar(
            select(ReviewRow)
            .where(ReviewRow.pr_url == row.pr_url)
            .where(ReviewRow.finished_at.isnot(None))
            .order_by(ReviewRow.finished_at.desc())
            .limit(1)
        )
        if review_row:
            d["has_guardian_review"] = True
            d["guardian_review_id"] = str(review_row.id)
            d["guardian_decision"] = review_row.decision
        # Enrich with active readiness candidate state
        cand_row = await session.scalar(
            select(ReadinessCandidateRow)
            .where(ReadinessCandidateRow.platform == row.platform)
            .where(ReadinessCandidateRow.repo == row.repo)
            .where(ReadinessCandidateRow.pr_id == row.pr_id)
            .where(ReadinessCandidateRow.state != "superseded")
            .order_by(ReadinessCandidateRow.updated_at.desc())
            .limit(1)
        )
        if cand_row:
            d["guardian_readiness_state"] = cand_row.state
            d["guardian_readiness_reason"] = cand_row.reason
        return d


async def get_synced_pr_lookup(
    pr_keys: list[tuple[str, str, str]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Look up cached synced PR data for many (platform, repo, pr_id) tuples.

    Used by the reviews queue to surface platform-side state (merged, approved,
    draft) and to fall back on title/author for review rows where hydration
    didn't run or failed. ADO repos can share a name across projects so we
    collapse duplicates to whichever row sorts latest by synced_at, with
    merged status winning ties because it can't regress.
    """
    if not pr_keys:
        return {}

    from sqlalchemy import tuple_

    unique_keys = list({k for k in pr_keys if all(k)})
    if not unique_keys:
        return {}

    async with async_session() as session:
        q = select(
            SyncedPRRow.platform,
            SyncedPRRow.repo,
            SyncedPRRow.pr_id,
            SyncedPRRow.approval_status,
            SyncedPRRow.title,
            SyncedPRRow.author,
            SyncedPRRow.author_display,
            SyncedPRRow.synced_at,
        ).where(tuple_(SyncedPRRow.platform, SyncedPRRow.repo, SyncedPRRow.pr_id).in_(unique_keys))
        rows = (await session.execute(q)).all()

    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    out_synced: dict[tuple[str, str, str], datetime] = {}
    for platform, repo, pr_id, status, title, author, author_display, synced in rows:
        key = (platform, repo, pr_id)
        prior = out.get(key)
        # Prefer merged status over anything else (it's terminal); otherwise
        # keep the most recently synced row.
        prior_status = prior.get("approval_status") if prior else None
        if prior_status == "merged":
            continue
        if (
            status == "merged"
            or prior is None
            or (synced and synced > out_synced.get(key, datetime.min.replace(tzinfo=timezone.utc)))
        ):
            out[key] = {
                "approval_status": status,
                "title": title,
                "author": author,
                "author_display": author_display,
            }
            out_synced[key] = synced or datetime.min.replace(tzinfo=timezone.utc)
    return out


async def get_synced_pr_statuses(
    pr_keys: list[tuple[str, str, str]],
) -> dict[tuple[str, str, str], str]:
    """Back-compat shim: returns only approval_status. Prefer get_synced_pr_lookup."""
    lookup = await get_synced_pr_lookup(pr_keys)
    return {k: v["approval_status"] for k, v in lookup.items()}


async def list_synced_prs(
    *,
    view: str | None = None,
    github_handle: str | None = None,
    ado_upn: str | None = None,
    platform: str | None = None,
    org: str | None = None,
    project: str | None = None,
    repo: str | None = None,
    author: str | None = None,
    approval_status: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """List cached open PRs with filters. Returns (items, total_count)."""
    import json
    from sqlalchemy import cast, or_
    from sqlalchemy.dialects.postgresql import JSONB

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
    user_handles = [h for h in [github_handle, ado_upn] if h]

    # Subquery: does a completed guardian review exist for this PR?
    review_exists_subq = (
        select(ReviewRow.id)
        # Match on (platform, repo, pr_id) rather than pr_url — the ADO review
        # and synced-PR pr_url strings are built from different sources and don't
        # compare equal. See list_synced_prs' guardian-status enrichment.
        .where(ReviewRow.platform == SyncedPRRow.platform)
        .where(ReviewRow.repo == SyncedPRRow.repo)
        .where(ReviewRow.pr_id == SyncedPRRow.pr_id)
        .where(ReviewRow.finished_at.isnot(None))
        .correlate(SyncedPRRow)
        .exists()
    )

    # Subquery: is this repo excluded?
    excluded_subq = (
        select(ExcludedRepoRow.id)
        .where(ExcludedRepoRow.platform == SyncedPRRow.platform)
        .where(ExcludedRepoRow.org == SyncedPRRow.org)
        .where(ExcludedRepoRow.project == SyncedPRRow.project)
        .where(ExcludedRepoRow.repo == SyncedPRRow.repo)
        .correlate(SyncedPRRow)
        .exists()
    )

    rules = await list_exclusion_rules()

    async with async_session() as session:
        q = select(SyncedPRRow).where(~excluded_subq)

        # Exclude merged PRs unless the caller explicitly asks for a status
        # (merged PRs are kept around for retention but shouldn't pollute open views).
        if approval_status:
            q = q.where(SyncedPRRow.approval_status == approval_status)
        else:
            q = q.where(SyncedPRRow.approval_status != "merged")

        # View filters
        if view == "mine" and user_handles:
            q = q.where(SyncedPRRow.author.in_(user_handles))
        elif view == "queue" and user_handles:
            conditions = [
                SyncedPRRow.reviewers.op("@>")(cast(json.dumps([h]), JSONB)) for h in user_handles
            ]
            q = q.where(or_(*conditions))
        elif view == "stale":
            q = q.where(SyncedPRRow.pr_updated_at < stale_cutoff)
        elif view == "ready":
            q = q.where(SyncedPRRow.is_draft == False)  # noqa: E712
            q = q.where(SyncedPRRow.has_conflicts == False)  # noqa: E712
            q = q.where(SyncedPRRow.ci_status != "failure")
            q = q.where(review_exists_subq)

        # Extra filters
        if platform:
            q = q.where(SyncedPRRow.platform == platform)
        if org:
            q = q.where(SyncedPRRow.org == org)
        if project:
            q = q.where(SyncedPRRow.project == project)
        if repo:
            q = q.where(SyncedPRRow.repo == repo)
        if author:
            q = q.where(SyncedPRRow.author == author)
        if search:
            q = q.where(SyncedPRRow.title.ilike(f"%{search}%"))

        ordered = q.order_by(SyncedPRRow.pr_updated_at.desc().nullslast())

        if rules:
            # Wildcard rules require Python-side fnmatch — fetch rows up to a hard cap,
            # filter, then paginate. Sync-time filtering keeps the persisted set small;
            # cap guards against pathological cases (> 5 000 rows is unexpected).
            _MAX_FETCH = 5_000
            all_rows = (await session.scalars(ordered.limit(_MAX_FETCH))).all()
            if len(all_rows) == _MAX_FETCH:
                log.warning(
                    "list_synced_prs hit fetch cap; results may be truncated",
                    cap=_MAX_FETCH,
                )
            filtered = [
                r
                for r in all_rows
                if not repo_matches_rules(rules, r.platform, r.org, r.project, r.repo)
            ]
            total = len(filtered)
            page = filtered[offset : offset + limit]
            pr_dicts = [_synced_pr_to_dict(r) for r in page]
        else:
            total = await session.scalar(select(func.count()).select_from(q.subquery()))
            paged = ordered.offset(offset).limit(limit)
            rows = (await session.scalars(paged)).all()
            pr_dicts = [_synced_pr_to_dict(r) for r in rows]

        # Batch-fetch guardian review status for the returned PRs.
        # Join on (platform, repo, pr_id) rather than pr_url: ADO builds the
        # review-side pr_url from the API's raw `remoteUrl` while the synced-PR
        # side reconstructs it from org/project/repo, so the two strings are not
        # byte-identical and an exact pr_url match silently misses. The tuple key
        # matches what the readiness-candidate enrichment below already uses.
        if pr_dicts:
            from sqlalchemy import tuple_

            pr_keys = [(d["platform"], d["repo"], d["pr_id"]) for d in pr_dicts]
            review_rows = await session.execute(
                select(
                    ReviewRow.platform,
                    ReviewRow.repo,
                    ReviewRow.pr_id,
                    ReviewRow.id,
                    ReviewRow.decision,
                )
                .where(tuple_(ReviewRow.platform, ReviewRow.repo, ReviewRow.pr_id).in_(pr_keys))
                .where(ReviewRow.finished_at.isnot(None))
                .order_by(ReviewRow.finished_at.desc())
            )
            reviews_map: dict[tuple, dict] = {}
            for r_platform, r_repo, r_pr_id, rid, rdecision in review_rows.fetchall():
                key = (r_platform, r_repo, r_pr_id)
                if key not in reviews_map:  # first = latest (ORDER BY finished_at DESC)
                    reviews_map[key] = {
                        "guardian_review_id": str(rid),
                        "guardian_decision": rdecision,
                    }
            for d in pr_dicts:
                info = reviews_map.get((d["platform"], d["repo"], d["pr_id"]))
                if info:
                    d["has_guardian_review"] = True
                    d["guardian_review_id"] = info["guardian_review_id"]
                    d["guardian_decision"] = info["guardian_decision"]

        # Batch-fetch active readiness candidate state (waiting/reviewing/blocked/error)
        # so the browse view can distinguish "Guardian waiting for CI" from "never run".
        if pr_dicts:
            from sqlalchemy import tuple_

            pr_keys = [(d["platform"], d["repo"], d["pr_id"]) for d in pr_dicts]
            cand_rows = await session.execute(
                select(
                    ReadinessCandidateRow.platform,
                    ReadinessCandidateRow.repo,
                    ReadinessCandidateRow.pr_id,
                    ReadinessCandidateRow.state,
                    ReadinessCandidateRow.reason,
                )
                .where(
                    tuple_(
                        ReadinessCandidateRow.platform,
                        ReadinessCandidateRow.repo,
                        ReadinessCandidateRow.pr_id,
                    ).in_(pr_keys)
                )
                .where(ReadinessCandidateRow.state != "superseded")
                .order_by(ReadinessCandidateRow.updated_at.desc())
            )
            candidates_map: dict[tuple, dict] = {}
            for c_platform, c_repo, c_pr_id, c_state, c_reason in cand_rows.fetchall():
                key = (c_platform, c_repo, c_pr_id)
                if key not in candidates_map:  # first = latest (ORDER BY updated_at DESC)
                    candidates_map[key] = {"state": c_state, "reason": c_reason}
            for d in pr_dicts:
                cand = candidates_map.get((d["platform"], d["repo"], d["pr_id"]))
                if cand:
                    d["guardian_readiness_state"] = cand["state"]
                    d["guardian_readiness_reason"] = cand["reason"]

        return pr_dicts, int(total or 0)


async def get_pr_dashboard_summary(
    github_handle: str | None = None,
    ado_upn: str | None = None,
) -> dict[str, Any]:
    """Compute counts for the dashboard summary cards."""
    import json
    from sqlalchemy import cast, or_
    from sqlalchemy.dialects.postgresql import JSONB

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
    user_handles = [h for h in [github_handle, ado_upn] if h]

    review_exists_subq = (
        select(ReviewRow.id)
        # Match on (platform, repo, pr_id) rather than pr_url — the ADO review
        # and synced-PR pr_url strings are built from different sources and don't
        # compare equal. See list_synced_prs' guardian-status enrichment.
        .where(ReviewRow.platform == SyncedPRRow.platform)
        .where(ReviewRow.repo == SyncedPRRow.repo)
        .where(ReviewRow.pr_id == SyncedPRRow.pr_id)
        .where(ReviewRow.finished_at.isnot(None))
        .correlate(SyncedPRRow)
        .exists()
    )

    open_only = SyncedPRRow.approval_status != "merged"

    async with async_session() as session:
        total_open = await session.scalar(select(func.count(SyncedPRRow.id)).where(open_only)) or 0

        if user_handles:
            mine_q = (
                select(func.count(SyncedPRRow.id))
                .where(SyncedPRRow.author.in_(user_handles))
                .where(open_only)
            )
            mine_total = await session.scalar(mine_q) or 0

            attention_q = (
                select(func.count(SyncedPRRow.id))
                .where(SyncedPRRow.author.in_(user_handles))
                .where(open_only)
                .where(
                    or_(
                        SyncedPRRow.approval_status == "changes_requested",
                        SyncedPRRow.pr_updated_at < stale_cutoff,
                        SyncedPRRow.approval_status == "approved",
                    )
                )
            )
            mine_attention = await session.scalar(attention_q) or 0

            queue_conditions = [
                SyncedPRRow.reviewers.op("@>")(cast(json.dumps([h]), JSONB)) for h in user_handles
            ]
            queue_q = (
                select(func.count(SyncedPRRow.id)).where(or_(*queue_conditions)).where(open_only)
            )
            queue_total = await session.scalar(queue_q) or 0
        else:
            mine_total = mine_attention = queue_total = 0

        stale_total = (
            await session.scalar(
                select(func.count(SyncedPRRow.id))
                .where(SyncedPRRow.pr_updated_at < stale_cutoff)
                .where(open_only)
            )
            or 0
        )

        ready_total = (
            await session.scalar(
                select(func.count(SyncedPRRow.id))
                .where(SyncedPRRow.is_draft == False)  # noqa: E712
                .where(SyncedPRRow.has_conflicts == False)  # noqa: E712
                .where(SyncedPRRow.ci_status != "failure")
                .where(open_only)
                .where(review_exists_subq)
            )
            or 0
        )

        repo_count = (
            await session.scalar(
                select(func.count(func.distinct(SyncedPRRow.repo))).where(open_only)
            )
            or 0
        )

        oldest_stale = await session.scalar(
            select(func.min(SyncedPRRow.pr_updated_at))
            .where(SyncedPRRow.pr_updated_at < stale_cutoff)
            .where(open_only)
        )
        oldest_days = None
        if oldest_stale:
            oldest_days = (datetime.now(timezone.utc) - oldest_stale).days

        return {
            "mine": {"total": mine_total, "needs_attention": mine_attention},
            "queue": {"total": queue_total},
            "stale": {"total": stale_total, "oldest_days": oldest_days},
            "all": {"total": total_open, "repo_count": repo_count},
            "ready": {"total": ready_total},
        }


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _synced_pr_to_dict(row: SyncedPRRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "platform": row.platform,
        "pr_id": row.pr_id,
        "org": row.org,
        "project": row.project,
        "repo": row.repo,
        "title": row.title,
        "author": row.author,
        "author_display": row.author_display,
        "pr_url": row.pr_url,
        "source_branch": row.source_branch,
        "target_branch": row.target_branch,
        "is_draft": row.is_draft,
        "has_conflicts": row.has_conflicts,
        "approval_status": row.approval_status,
        "reviewers": row.reviewers or [],
        "assignees": row.assignees or [],
        "comment_count": row.comment_count,
        "ci_status": row.ci_status or "unknown",
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "profile_snapshot": row.profile_snapshot,
        "connection_id": str(row.connection_id) if row.connection_id else None,
        "connection_snapshot": row.connection_snapshot,
        "repo_link_id": str(row.repo_link_id) if row.repo_link_id else None,
        "sync_source": row.sync_source,
        # Guardian review fields populated by list_synced_prs / get_synced_pr lookups
        "has_guardian_review": False,
        "guardian_review_id": None,
        "guardian_decision": None,
        "guardian_readiness_state": None,
        "guardian_readiness_reason": None,
        "pr_created_at": row.pr_created_at.isoformat() if row.pr_created_at else None,
        "pr_updated_at": row.pr_updated_at.isoformat() if row.pr_updated_at else None,
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }
