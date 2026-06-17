from __future__ import annotations

import copy
import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pr_guardian.config.loader import _deep_merge, apply_global_settings, load_service_defaults
from pr_guardian.config.schema import GuardianConfig

ResolutionSource = Literal["linked", "default", "snapshot"]

ACTIVE_PROFILE_KEYS = {
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
    "escalation_policy",
}

_DORMANT_THRESHOLD_KEYS = {"human_review_min_score"}

# Active profile keys whose GuardianConfig field is a nested model (must be a dict
# in settings). The remainder (repo_risk_class, guardian_clearance,
# platform_approval_enabled) are scalars and are left untouched.
_STRUCTURED_PROFILE_KEYS = ACTIVE_PROFILE_KEYS - {
    "repo_risk_class",
    "guardian_clearance",
    "platform_approval_enabled",
}


class ProfileResolutionError(RuntimeError):
    """Raised when a run needs a Profile/Connection decision from the caller."""


@dataclass(frozen=True)
class ResolvedProfileConfig:
    config: GuardianConfig
    profile_id: uuid.UUID | None
    profile_snapshot: dict[str, Any] | None
    connection_id: uuid.UUID | None
    connection_snapshot: dict[str, Any] | None
    repo_link_id: uuid.UUID | None
    source: ResolutionSource

    @property
    def linked(self) -> bool:
        return self.source == "linked"

    def review_provenance(self, *, review_source: str) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_snapshot": self.profile_snapshot,
            "connection_id": self.connection_id,
            "connection_snapshot": self.connection_snapshot,
            "repo_link_id": self.repo_link_id,
            "review_source": review_source,
        }

    def scan_provenance(self, *, scan_source: str = "scan") -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_snapshot": self.profile_snapshot,
            "connection_id": self.connection_id,
            "connection_snapshot": self.connection_snapshot,
            "repo_link_id": self.repo_link_id,
            "scan_source": scan_source,
        }


def sanitize_profile_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Return only active Profile-owned policy fields.

    Profiles intentionally exclude LLM/provider/runtime knobs and dormant legacy
    policy fields even if old rows still contain them.
    """
    clean: dict[str, Any] = {}
    for key, value in (settings or {}).items():
        if key not in ACTIVE_PROFILE_KEYS:
            continue
        # Heal legacy rows where a structured field was persisted as a scalar
        # (e.g. severity_floor saved as the level string "medium" by an old UI).
        # Dropping it lets the model default apply instead of crashing config build.
        if key in _STRUCTURED_PROFILE_KEYS and not isinstance(value, dict):
            continue
        copied = copy.deepcopy(value)
        if key == "thresholds" and isinstance(copied, dict):
            for dormant_key in _DORMANT_THRESHOLD_KEYS:
                copied.pop(dormant_key, None)
        clean[key] = copied
    return clean


def profile_settings_to_config(settings: dict[str, Any] | None) -> GuardianConfig:
    base = load_service_defaults()
    merged = _deep_merge(base, sanitize_profile_settings(settings))
    return GuardianConfig(**merged)


async def resolve_profile_config(
    *,
    platform: str | None,
    repo: str | None = None,
    org_url: str = "",
    project: str = "",
    connection_name: str | None = None,
    connection_id: uuid.UUID | None = None,
    require_linked: bool = False,
    require_connection: bool = False,
    allow_db_failure: bool = True,
) -> ResolvedProfileConfig:
    """Resolve a linked repo Profile/Connection or the default/noop Profile."""
    try:
        from pr_guardian.persistence import storage

        if not _profile_db_enabled(storage):
            return await resolve_default_profile_config()

        link = None
        if platform and repo:
            link = await _find_repo_link(
                storage, platform=platform, repo=repo, org_url=org_url, project=project
            )
        if link:
            profile = await storage.get_profile(uuid.UUID(link["profile_id"]))
            connection = await storage.get_connection(uuid.UUID(link["connection_id"]))
            if not profile:
                raise ProfileResolutionError("Linked Profile is missing or archived")
            if not connection:
                raise ProfileResolutionError("Linked Connection is missing or archived")
            return await _resolved_from_rows(
                profile=profile,
                connection=connection,
                repo_link=link,
                source="linked",
            )

        if require_linked:
            raise ProfileResolutionError(
                "This repository is not linked to a Profile and Connection."
            )

        profile = await storage.ensure_default_profile()
        connection = await _select_connection(
            storage,
            platform=platform,
            connection_name=connection_name,
            connection_id=connection_id,
        )
        if require_connection and platform and connection is None:
            raise ProfileResolutionError(
                "A Connection selection is required for this unlinked repository."
            )
        return await _resolved_from_rows(
            profile=profile,
            connection=connection,
            repo_link=None,
            source="default",
        )
    except ProfileResolutionError:
        raise
    except Exception:
        if not allow_db_failure:
            raise
        return await resolve_default_profile_config()


async def resolve_default_profile_config() -> ResolvedProfileConfig:
    config = await _apply_global_settings_if_available(profile_settings_to_config({}))
    return ResolvedProfileConfig(
        config=config,
        profile_id=None,
        profile_snapshot=None,
        connection_id=None,
        connection_snapshot=None,
        repo_link_id=None,
        source="default",
    )


async def resolve_profile_snapshot_config(
    profile_snapshot: dict[str, Any] | None,
    connection_snapshot: dict[str, Any] | None = None,
) -> ResolvedProfileConfig:
    settings = profile_snapshot.get("settings", {}) if isinstance(profile_snapshot, dict) else {}
    config = await _apply_global_settings_if_available(profile_settings_to_config(settings))
    return ResolvedProfileConfig(
        config=config,
        profile_id=_uuid_or_none(profile_snapshot.get("id") if profile_snapshot else None),
        profile_snapshot=_sanitize_profile_snapshot(profile_snapshot)
        if profile_snapshot
        else None,
        connection_id=_uuid_or_none(
            connection_snapshot.get("id") if connection_snapshot else None
        ),
        connection_snapshot=copy.deepcopy(connection_snapshot) if connection_snapshot else None,
        repo_link_id=None,
        source="snapshot",
    )


def profile_allows_side_effect(
    profile_snapshot: dict[str, Any] | None,
    side_effect: str,
    *,
    legacy_default: bool = True,
) -> bool:
    if not profile_snapshot:
        return legacy_default
    settings = profile_snapshot.get("settings")
    if not isinstance(settings, dict):
        return False
    side_effects = settings.get("side_effects")
    if not isinstance(side_effects, dict):
        return False
    return bool(side_effects.get(side_effect, False))


async def _resolved_from_rows(
    *,
    profile: dict[str, Any],
    connection: dict[str, Any] | None,
    repo_link: dict[str, Any] | None,
    source: ResolutionSource,
) -> ResolvedProfileConfig:
    profile_snapshot = _sanitize_profile_snapshot(profile)
    config = await _apply_global_settings_if_available(
        profile_settings_to_config(profile_snapshot["settings"])
    )
    return ResolvedProfileConfig(
        config=config,
        profile_id=_uuid_or_none(profile.get("id")),
        profile_snapshot=profile_snapshot,
        connection_id=_uuid_or_none(connection.get("id") if connection else None),
        connection_snapshot=copy.deepcopy(connection) if connection else None,
        repo_link_id=_uuid_or_none(repo_link.get("id") if repo_link else None),
        source=source,
    )


def _sanitize_profile_snapshot(profile: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = copy.deepcopy(profile or {})
    snapshot["settings"] = sanitize_profile_settings(snapshot.get("settings"))
    return snapshot


async def _find_repo_link(storage, *, platform: str, repo: str, org_url: str, project: str):
    wanted = _canonical_repo_key(platform, repo=repo, org_url=org_url, project=project)
    for link in await storage.list_repo_links():
        if (
            link.get("platform", "").lower() == platform.lower()
            and link.get("canonical_repo_key") == wanted
        ):
            return link
    return None


async def _select_connection(
    storage,
    *,
    platform: str | None,
    connection_name: str | None,
    connection_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    if not platform:
        return None
    if connection_id:
        return await storage.get_connection(connection_id)
    connections = [
        c
        for c in await storage.list_connections()
        if c.get("platform", "").lower() == platform.lower()
    ]
    if connection_name:
        for connection in connections:
            if connection.get("name") == connection_name:
                return connection
        raise ProfileResolutionError(f"Connection not found: {connection_name!r}")
    if len(connections) == 1:
        return connections[0]
    return None


def _canonical_repo_key(platform: str, *, repo: str, org_url: str = "", project: str = "") -> str:
    platform = platform.lower().strip()
    if platform == "github":
        owner, name = _split_repo(repo)
        return f"github:{owner.lower()}/{name.lower()}"
    if platform == "ado":
        parsed_project, name = _split_repo(repo)
        project = project or parsed_project
        return (
            f"ado:{org_url.lower().rstrip('/')}:{project.lower().strip()}/{name.lower().strip()}"
        )
    owner, name = _split_repo(repo)
    return f"{platform}:{owner.lower()}/{name.lower()}"


def _split_repo(repo: str) -> tuple[str, str]:
    parts = [part for part in repo.split("/") if part]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", repo


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _profile_db_enabled(storage) -> bool:
    if os.environ.get("DATABASE_URL") or os.environ.get("GUARDIAN_DB_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    async_session_module = getattr(storage.async_session, "__module__", "")
    return async_session_module != "pr_guardian.persistence.database"


async def _apply_global_settings_if_available(config: GuardianConfig) -> GuardianConfig:
    try:
        from pr_guardian.persistence import storage
    except Exception:
        return config
    if not _profile_db_enabled(storage):
        return config
    return await apply_global_settings(config)
