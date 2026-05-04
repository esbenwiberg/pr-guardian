from __future__ import annotations

import os

from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.models.pr import PlatformPR


def create_adapter(platform: str, *, token_override: str | None = None) -> PlatformAdapter:
    """Create the appropriate platform adapter.

    For GitHub, pass token_override to use a specific token instead of GITHUB_TOKEN.
    Use create_github_adapter() when you need async PAT resolution from the database.
    """
    if platform == "github":
        token = token_override if token_override is not None else os.environ.get("GITHUB_TOKEN", "")
        return GitHubAdapter(token=token)

    if platform == "ado":
        pat = os.environ.get("ADO_PAT", "")
        org_url = os.environ.get("ADO_ORG_URL", "")
        return ADOAdapter(pat=pat, org_url=org_url)

    raise ValueError(f"Unknown platform: {platform}")


async def create_github_adapter(pat_name: str | None = None) -> GitHubAdapter:
    """Create a GitHubAdapter, resolving the token from DB or env var.

    Priority: named PAT by pat_name > default PAT in DB > GITHUB_TOKEN env var.
    """
    from pr_guardian.persistence import storage

    token = await storage.resolve_github_token(pat_name)
    return GitHubAdapter(token=token)


def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
    """Normalize webhook payload to PlatformPR using the right adapter."""
    if payload.platform == "github":
        return GitHubAdapter.normalize_webhook(payload)
    if payload.platform == "ado":
        return ADOAdapter.normalize_webhook(payload)
    return None
