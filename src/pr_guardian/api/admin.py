"""Admin API: manage admins and API keys."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from pr_guardian.auth.dependencies import require_admin
from pr_guardian.auth.identity import Identity
from pr_guardian.persistence import storage

log = structlog.get_logger()

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@router.get("/me")
async def whoami(identity: Identity = Depends(require_admin)):
    """Return the current caller's resolved identity."""
    return {
        "kind": identity.kind,
        "email": identity.email,
        "key_id": identity.key_id,
        "key_name": identity.key_name,
        "scopes": identity.scopes,
        "is_admin": identity.is_admin,
        "can_manage_profiles": bool(
            identity.kind != "api_key" and (identity.is_admin or identity.can_manage_profiles)
        ),
        "display_name": identity.display_name,
    }


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------


class AddAdminRequest(BaseModel):
    email: str


@router.get("/admins")
async def list_admins(identity: Identity = Depends(require_admin)):
    """List all admin users."""
    return await storage.list_admins()


@router.post("/admins", status_code=201)
async def add_admin(body: AddAdminRequest, identity: Identity = Depends(require_admin)):
    """Add a new admin user."""
    email = body.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address")

    added = await storage.add_admin(email, added_by=identity.display_name)
    if not added:
        raise HTTPException(409, f"{email} is already an admin")

    log.info("admin_added", email=email, by=identity.display_name)
    return {"status": "added", "email": email}


@router.delete("/admins/{email}")
async def remove_admin(email: str, identity: Identity = Depends(require_admin)):
    """Remove an admin user. Cannot remove the last admin."""
    email = email.lower().strip()
    count = await storage.admin_count()
    if count <= 1:
        raise HTTPException(400, "Cannot remove the last admin")

    removed = await storage.remove_admin(email)
    if not removed:
        raise HTTPException(404, f"{email} is not an admin")

    log.info("admin_removed", email=email, by=identity.display_name)
    return {"status": "removed", "email": email}


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    name: str
    scopes: list[str] = ["read"]
    expires_in_days: int | None = None


@router.get("/api-keys")
async def list_api_keys(identity: Identity = Depends(require_admin)):
    """List all API keys (full key is never shown)."""
    return await storage.list_api_keys()


@router.post("/api-keys", status_code=201)
async def create_api_key(body: CreateApiKeyRequest, identity: Identity = Depends(require_admin)):
    """Create a new API key. The full key is returned ONCE in the response."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Key name is required")

    # Validate scopes
    valid_scopes = {"read", "write"}
    if not set(body.scopes).issubset(valid_scopes):
        raise HTTPException(400, f"Invalid scopes. Allowed: {sorted(valid_scopes)}")

    raw_key, metadata = await storage.create_api_key(
        name=name,
        scopes=body.scopes,
        created_by=identity.display_name,
        expires_in_days=body.expires_in_days,
    )

    log.info("api_key_created", name=name, scopes=body.scopes, by=identity.display_name)
    return {
        "key": raw_key,  # Only time the full key is exposed
        **metadata,
    }


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: uuid.UUID, identity: Identity = Depends(require_admin)):
    """Revoke an API key (soft-delete)."""
    revoked = await storage.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(404, "API key not found")

    log.info("api_key_revoked", key_id=str(key_id), by=identity.display_name)
    return {"status": "revoked", "id": str(key_id)}


# ---------------------------------------------------------------------------
# Excluded repos (PR dashboard filtering)
# ---------------------------------------------------------------------------


@router.get("/excluded-repos")
async def list_excluded_repos(identity: Identity = Depends(require_admin)):
    """List all repos excluded from the PR dashboard."""
    return await storage.list_excluded_repos()


@router.delete("/excluded-repos/{exclusion_id}")
async def remove_excluded_repo(exclusion_id: str, identity: Identity = Depends(require_admin)):
    """Restore a previously excluded repo to the PR dashboard."""
    removed = await storage.remove_excluded_repo(exclusion_id)
    if not removed:
        raise HTTPException(404, "Exclusion not found")
    log.info("repo_exclusion_removed", exclusion_id=exclusion_id, by=identity.display_name)
    return {"status": "removed", "id": exclusion_id}


# ---------------------------------------------------------------------------
# Exclusion rules (wildcard repo exclusion)
# ---------------------------------------------------------------------------


_VALID_PLATFORMS = {"github", "ado"}


class AddExclusionRuleRequest(BaseModel):
    platform: str
    org_pattern: str = ""
    project_pattern: str = ""
    repo_pattern: str = ""


@router.get("/exclusion-rules")
async def list_exclusion_rules(identity: Identity = Depends(require_admin)):
    """List all wildcard exclusion rules."""
    return await storage.list_exclusion_rules()


@router.post("/exclusion-rules", status_code=201)
async def add_exclusion_rule(
    body: AddExclusionRuleRequest,
    identity: Identity = Depends(require_admin),
):
    """Add a wildcard exclusion rule. At least one pattern field must be set."""
    platform = body.platform.strip().lower()
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(400, f"platform must be one of {sorted(_VALID_PLATFORMS)}")

    org_pattern = body.org_pattern.strip()
    project_pattern = body.project_pattern.strip()
    repo_pattern = body.repo_pattern.strip()

    if not (org_pattern or project_pattern or repo_pattern):
        raise HTTPException(400, "At least one pattern field must be provided")
    if platform == "github" and project_pattern:
        raise HTTPException(400, "project_pattern is not applicable to github rules")

    rule = await storage.add_exclusion_rule(
        platform=platform,
        org_pattern=org_pattern,
        project_pattern=project_pattern,
        repo_pattern=repo_pattern,
        email=identity.email or "",
    )
    log.info(
        "exclusion_rule_added",
        rule_id=rule["id"],
        platform=platform,
        org=org_pattern,
        project=project_pattern,
        repo=repo_pattern,
        by=identity.display_name,
    )
    return rule


@router.delete("/exclusion-rules/{rule_id}")
async def remove_exclusion_rule(rule_id: str, identity: Identity = Depends(require_admin)):
    """Delete a wildcard exclusion rule."""
    removed = await storage.remove_exclusion_rule(rule_id)
    if not removed:
        raise HTTPException(404, "Rule not found")
    log.info("exclusion_rule_removed", rule_id=rule_id, by=identity.display_name)
    return {"status": "removed", "id": rule_id}
