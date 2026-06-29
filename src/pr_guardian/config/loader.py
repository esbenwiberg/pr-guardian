from __future__ import annotations

import os
from pathlib import Path

import structlog
import yaml

from pr_guardian.config.schema import GuardianConfig, LLMProviderConfig


log = structlog.get_logger()

_SERVICE_DEFAULTS_PATH = Path(__file__).parent / "defaults.yml"


def _ensure_env_provider_dict(base: dict) -> dict:
    provider = os.environ.get("GUARDIAN_LLM_PROVIDER", "").strip()
    if not provider:
        return base
    llm = base.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    llm["default_provider"] = provider
    if provider == "fake" and "fake" not in providers:
        providers["fake"] = {
            "type": "fake",
            "default_model": "fake-deterministic-v1",
            "models": ["fake-deterministic-v1"],
        }
    if provider == "claude-cli" and "claude-cli" not in providers:
        providers["claude-cli"] = {
            "type": "claude-cli",
            "default_model": "",
            "models": [],
        }
    return base


def _ensure_env_provider_config(config: GuardianConfig) -> GuardianConfig:
    provider = os.environ.get("GUARDIAN_LLM_PROVIDER", "").strip()
    if not provider:
        return config
    config.llm.default_provider = provider
    if provider == "fake" and "fake" not in config.llm.providers:
        config.llm.providers["fake"] = LLMProviderConfig(
            type="fake",
            default_model="fake-deterministic-v1",
            models=["fake-deterministic-v1"],
        )
    if provider == "claude-cli" and "claude-cli" not in config.llm.providers:
        config.llm.providers["claude-cli"] = LLMProviderConfig(
            type="claude-cli",
            default_model="",
            models=[],
        )
    return config


def load_service_defaults() -> dict:
    if _SERVICE_DEFAULTS_PATH.exists():
        return _ensure_env_provider_dict(yaml.safe_load(_SERVICE_DEFAULTS_PATH.read_text()) or {})
    return _ensure_env_provider_dict({})


def load_repo_config(repo_path: Path) -> GuardianConfig:
    """Return service defaults for legacy callers.

    Product review and scan paths resolve Profile policy instead. This function
    deliberately ignores repo-local review.yml files so there is no hidden
    compatibility path that can override Guardian-owned Profile policy.
    """
    _ = repo_path
    base = load_service_defaults()
    return GuardianConfig(**base)


async def apply_global_settings(config: GuardianConfig) -> GuardianConfig:
    """Apply DB-stored settings (provider choice, API keys, endpoint) on top of config.

    Fails gracefully if the database is unavailable.
    """
    try:
        from pr_guardian.persistence.storage import get_global_config

        settings = await get_global_config()
    except Exception:
        return _ensure_env_provider_config(config)

    if not settings:
        return _ensure_env_provider_config(config)

    # Active provider
    active = settings.get("llm.active_provider")
    if active:
        config.llm.default_provider = active

    # Azure AI Foundry overrides
    endpoint = settings.get("llm.azure_ai_foundry.endpoint_url", "")
    api_key = settings.get("llm.azure_ai_foundry.api_key", "")
    if endpoint or api_key:
        provider = config.llm.providers.get("azure-ai-foundry")
        if provider:
            if endpoint:
                provider.base_url = endpoint
            if api_key:
                provider.api_key = api_key
        else:
            config.llm.providers["azure-ai-foundry"] = LLMProviderConfig(
                type="azure-ai-foundry",
                api_key_env="AZURE_AI_FOUNDRY_API_KEY",
                base_url=endpoint,
                api_key=api_key,
                default_model="claude-sonnet-4-6",
                models=["claude-sonnet-4-6", "claude-haiku-4-5"],
            )

    # Anthropic API key override (from UI)
    anthropic_key = settings.get("llm.anthropic.api_key", "")
    if anthropic_key:
        provider = config.llm.providers.get("anthropic")
        if provider:
            provider.api_key = anthropic_key

    # Default model override (applies to the active provider)
    default_model = settings.get("llm.default_model", "")
    if default_model:
        active_provider = config.llm.providers.get(config.llm.default_provider)
        if active_provider:
            active_provider.default_model = default_model

    config = _ensure_env_provider_config(config)
    log.debug("global_settings_applied", active_provider=config.llm.default_provider)
    return config


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base. Overrides win for scalars."""
    result = base.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
