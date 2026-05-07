"""Admin API: manage admins and API keys."""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

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
# GitHub PAT management
# ---------------------------------------------------------------------------


def _raise_pat_integrity_error(exc: "IntegrityError") -> None:
    """Translate a DB IntegrityError into the correct 409 detail message."""
    err = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
    if "uq_github_pats_single_default" in err:
        raise HTTPException(409, "Another GitHub PAT is already set as the default")
    raise HTTPException(409, "A GitHub PAT with that name already exists")


class CreateGithubPatRequest(BaseModel):
    name: str
    token: str
    description: str = ""
    is_default: bool = False


class UpdateGithubPatRequest(BaseModel):
    name: str | None = None
    token: str | None = None
    description: str | None = None
    is_default: bool | None = None


@router.get("/github-pats")
async def list_github_pats(identity: Identity = Depends(require_admin)):
    """List all configured GitHub PATs (token is never returned)."""
    return await storage.list_github_pats()


@router.post("/github-pats", status_code=201)
async def create_github_pat(body: CreateGithubPatRequest, identity: Identity = Depends(require_admin)):
    """Store a new named GitHub PAT."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "PAT name is required")
    if not body.token.strip():
        raise HTTPException(400, "Token is required")

    try:
        pat = await storage.create_github_pat(
            name=name,
            token=body.token.strip(),
            description=body.description.strip(),
            is_default=body.is_default,
        )
    except IntegrityError as exc:
        _raise_pat_integrity_error(exc)
    log.info("github_pat_created", name=name, by=identity.display_name)
    return pat


@router.put("/github-pats/{pat_id}")
async def update_github_pat(
    pat_id: uuid.UUID,
    body: UpdateGithubPatRequest,
    identity: Identity = Depends(require_admin),
):
    """Update a GitHub PAT (name, description, token, or default flag)."""
    name = body.name.strip() if body.name is not None else None
    if name is not None and not name:
        raise HTTPException(400, "PAT name cannot be empty")
    token = body.token.strip() if body.token is not None else None
    if token is not None and not token:
        raise HTTPException(400, "Token cannot be empty")
    try:
        updated = await storage.update_github_pat(
            pat_id,
            name=name,
            token=token,
            description=body.description.strip() if body.description is not None else None,
            is_default=body.is_default,
        )
    except IntegrityError as exc:
        _raise_pat_integrity_error(exc)
    if not updated:
        raise HTTPException(404, "GitHub PAT not found")

    log.info("github_pat_updated", pat_id=str(pat_id), by=identity.display_name)
    return updated


@router.delete("/github-pats/{pat_id}")
async def delete_github_pat(pat_id: uuid.UUID, identity: Identity = Depends(require_admin)):
    """Delete a GitHub PAT."""
    deleted = await storage.delete_github_pat(pat_id)
    if not deleted:
        raise HTTPException(404, "GitHub PAT not found")

    log.info("github_pat_deleted", pat_id=str(pat_id), by=identity.display_name)
    return {"status": "deleted", "id": str(pat_id)}


# ---------------------------------------------------------------------------
# Excluded repos (PR dashboard filtering)
# ---------------------------------------------------------------------------


@router.get("/excluded-repos")
async def list_excluded_repos(identity: Identity = Depends(require_admin)):
    """List all repos excluded from the PR dashboard."""
    return await storage.list_excluded_repos()


@router.delete("/excluded-repos/{exclusion_id}")
async def remove_excluded_repo(
    exclusion_id: str, identity: Identity = Depends(require_admin)
):
    """Restore a previously excluded repo to the PR dashboard."""
    removed = await storage.remove_excluded_repo(exclusion_id)
    if not removed:
        raise HTTPException(404, "Exclusion not found")
    log.info("repo_exclusion_removed", exclusion_id=exclusion_id, by=identity.display_name)
    return {"status": "removed", "id": exclusion_id}
