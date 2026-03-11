from __future__ import annotations

import os

import structlog

from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.models.pr import PlatformPR

log = structlog.get_logger()


def create_adapter(platform: str) -> PlatformAdapter:
    """Create the appropriate platform adapter with best available auth.

    Auth priority:
    - GitHub: App auth (GITHUB_APP_*) > PAT (GITHUB_TOKEN)
    - ADO: Service principal (ADO_CLIENT_ID + ADO_TENANT_ID + ADO_CLIENT_SECRET) > PAT (ADO_PAT)
    """
    if platform == "github":
        return _create_github_adapter()

    if platform == "ado":
        return _create_ado_adapter()

    raise ValueError(f"Unknown platform: {platform}")


def _create_github_adapter() -> GitHubAdapter:
    """Create GitHub adapter with App auth or PAT fallback."""
    from pr_guardian.auth.keyvault import get_secret

    app_id = os.environ.get("GITHUB_APP_ID", "")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
    private_key = get_secret("github-app-private-key", "GITHUB_APP_PRIVATE_KEY")

    # Try key file if env var is a path or GITHUB_APP_KEY_FILE is set
    if not private_key:
        key_file = os.environ.get("GITHUB_APP_KEY_FILE", "")
        if key_file and os.path.isfile(key_file):
            with open(key_file) as f:
                private_key = f.read()

    if app_id and private_key and installation_id:
        from pr_guardian.auth.github_app import GitHubAppAuth

        app_auth = GitHubAppAuth(
            app_id=app_id,
            private_key=private_key,
            installation_id=installation_id,
        )
        log.info("github_adapter_mode", mode="app", app_id=app_id)
        return GitHubAdapter(app_auth=app_auth)

    # Fallback: PAT
    token = get_secret("github-pat", "GITHUB_TOKEN")
    if not token:
        log.warning("github_adapter_no_credentials")
    else:
        log.info("github_adapter_mode", mode="pat")
    return GitHubAdapter(token=token)


def _create_ado_adapter() -> ADOAdapter:
    """Create ADO adapter with service principal or PAT fallback."""
    from pr_guardian.auth.keyvault import get_secret

    org_url = os.environ.get("ADO_ORG_URL", "")
    client_id = os.environ.get("ADO_CLIENT_ID", "")
    tenant_id = os.environ.get("ADO_TENANT_ID", "")
    client_secret = get_secret("ado-client-secret", "ADO_CLIENT_SECRET")

    if client_id and tenant_id and client_secret:
        from pr_guardian.auth.ado_sp import ADOServicePrincipalAuth

        sp_auth = ADOServicePrincipalAuth(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
        )
        log.info("ado_adapter_mode", mode="service_principal", client_id=client_id)
        return ADOAdapter(org_url=org_url, sp_auth=sp_auth)

    # Fallback: PAT
    pat = get_secret("ado-pat", "ADO_PAT")
    if not pat:
        log.warning("ado_adapter_no_credentials")
    else:
        log.info("ado_adapter_mode", mode="pat")
    return ADOAdapter(pat=pat, org_url=org_url)


def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
    """Normalize webhook payload to PlatformPR using the right adapter."""
    if payload.platform == "github":
        return GitHubAdapter.normalize_webhook(payload)
    if payload.platform == "ado":
        return ADOAdapter.normalize_webhook(payload)
    return None
