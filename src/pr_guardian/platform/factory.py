from __future__ import annotations

import os

from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.models.pr import PlatformPR


def create_adapter(platform: str) -> PlatformAdapter:
    """Create the appropriate platform adapter."""
    if platform == "github":
        token = os.environ.get("GITHUB_TOKEN", "")
        return GitHubAdapter(token=token)

    if platform == "ado":
        pat = os.environ.get("ADO_PAT", "")
        org_url = os.environ.get("ADO_ORG_URL", "")
        return ADOAdapter(pat=pat, org_url=org_url)

    raise ValueError(f"Unknown platform: {platform}")


def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
    """Normalize webhook payload to PlatformPR using the right adapter."""
    if payload.platform == "github":
        return GitHubAdapter.normalize_webhook(payload)
    if payload.platform == "ado":
        return ADOAdapter.normalize_webhook(payload)
    return None
