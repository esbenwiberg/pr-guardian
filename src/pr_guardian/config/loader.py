from __future__ import annotations

import os
from pathlib import Path

import structlog
import yaml

from pr_guardian.config.schema import GuardianConfig, LLMProviderConfig


log = structlog.get_logger()

_SERVICE_DEFAULTS_PATH = Path(__file__).parent / "defaults.yml"


def load_service_defaults() -> dict:
    if _SERVICE_DEFAULTS_PATH.exists():
        return yaml.safe_load(_SERVICE_DEFAULTS_PATH.read_text()) or {}
    return {}


def load_repo_config(repo_path: Path) -> GuardianConfig:
    """Load review.yml from repo root, merge with service defaults."""
    base = load_service_defaults()

    repo_config_path = repo_path / "review.yml"
    if repo_config_path.exists():
        repo_overrides = yaml.safe_load(repo_config_path.read_text()) or {}
    else:
        repo_overrides = {}

    merged = _deep_merge(base, repo_overrides)
    return GuardianConfig(**merged)


async def apply_global_settings(config: GuardianConfig) -> GuardianConfig:
    """Apply DB-stored settings (provider choice, API keys, endpoint) on top of config.

    Fails gracefully if the database is unavailable.
    """
    try:
        from pr_guardian.persistence.storage import get_global_config
        settings = await get_global_config()
    except Exception:
        return config

    if not settings:
        return config

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
