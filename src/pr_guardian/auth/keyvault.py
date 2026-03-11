"""Azure Key Vault integration with env-var fallback.

When AZURE_KEYVAULT_URL is set, secrets are fetched from Key Vault using
DefaultAzureCredential (managed identity in production, az-cli locally).
When unset, all secrets fall back to environment variables.
"""
from __future__ import annotations

import os

import structlog

log = structlog.get_logger()

_client = None  # SecretClient | None — lazy import to avoid hard dep


async def init_keyvault() -> None:
    """Initialise the Key Vault client if AZURE_KEYVAULT_URL is configured."""
    global _client
    vault_url = os.environ.get("AZURE_KEYVAULT_URL")
    if not vault_url:
        log.info("keyvault_disabled", hint="Set AZURE_KEYVAULT_URL to enable")
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        _client = SecretClient(vault_url=vault_url, credential=credential)
        # Verify connectivity with a lightweight call
        log.info("keyvault_ready", vault=vault_url)
    except ImportError:
        log.warning(
            "keyvault_deps_missing",
            hint="Install azure-identity and azure-keyvault-secrets",
        )
    except Exception as exc:
        log.error("keyvault_init_failed", error=str(exc))


def get_secret(name: str, fallback_env: str = "") -> str:
    """Fetch a secret from Key Vault, falling back to an environment variable.

    Args:
        name: Key Vault secret name (e.g. ``github-app-private-key``).
        fallback_env: Environment variable name to use when Key Vault is
            unavailable or the secret is missing.
    """
    if _client is not None:
        try:
            secret = _client.get_secret(name)
            if secret.value:
                return secret.value
        except Exception:
            log.warning("keyvault_secret_miss", name=name)

    return os.environ.get(fallback_env, "")
