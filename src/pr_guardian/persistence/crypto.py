"""Encrypt / decrypt secrets stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from
the GUARDIAN_SECRET_KEY environment variable via PBKDF2.

If GUARDIAN_SECRET_KEY is not set, a deterministic fallback key is used
and a warning is logged.  This keeps the dev experience friction-free
while making production deployments secure (just set the env var).
"""
from __future__ import annotations

import base64
import hashlib
import os

import structlog

log = structlog.get_logger()

_SALT = b"pr-guardian-config-encryption-v1"
_ITERATIONS = 480_000

# Keys in global_config whose values must be encrypted at rest.
SECRET_KEYS = frozenset({
    "llm.anthropic.api_key",
    "llm.azure_ai_foundry.api_key",
})

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    from cryptography.fernet import Fernet

    secret = os.environ.get("GUARDIAN_SECRET_KEY", "")
    if not secret:
        log.warning(
            "guardian_secret_key_missing",
            hint="Set GUARDIAN_SECRET_KEY for encrypted secret storage. "
                 "Using insecure fallback — DO NOT use in production.",
        )
        secret = "pr-guardian-dev-fallback-not-for-production"

    # Derive a 32-byte key via PBKDF2 → base64-encode for Fernet
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), _SALT, _ITERATIONS)
    fernet_key = base64.urlsafe_b64encode(dk)
    _fernet = Fernet(fernet_key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns a URL-safe base64 token."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    Returns empty string on failure (corrupt token, wrong key, etc.).
    """
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        log.warning("decrypt_failed", hint="Wrong GUARDIAN_SECRET_KEY or corrupt token")
        return ""
