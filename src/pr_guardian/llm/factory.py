from __future__ import annotations

import os

import structlog

from pr_guardian.config.schema import GuardianConfig, LLMProviderConfig
from pr_guardian.llm.anthropic import AnthropicClient
from pr_guardian.llm.azure_foundry import AzureFoundryClient
from pr_guardian.llm.openai_compat import OpenAICompatClient
from pr_guardian.llm.protocol import LLMClient

log = structlog.get_logger()


def create_llm_client(
    config: GuardianConfig,
    provider_name: str | None = None,
) -> LLMClient:
    """Create an LLM client from config. Falls back to default_provider."""
    provider_name = provider_name or config.llm.default_provider
    provider_cfg = config.llm.providers.get(provider_name)

    if not provider_cfg:
        log.warning("unknown_provider", provider=provider_name, fallback=config.llm.default_provider)
        provider_cfg = config.llm.providers.get(config.llm.default_provider)
        if not provider_cfg:
            raise ValueError(f"No LLM provider configured: {config.llm.default_provider}")

    return _build_client(provider_cfg)


def _resolve_api_key(cfg: LLMProviderConfig) -> str:
    """Resolve API key: cfg.api_key (DB/config) wins over env var."""
    if cfg.api_key:
        return cfg.api_key
    if cfg.api_key_env:
        return os.environ.get(cfg.api_key_env, "")
    return ""


def _build_client(cfg: LLMProviderConfig) -> LLMClient:
    if cfg.type == "anthropic":
        return AnthropicClient(api_key=_resolve_api_key(cfg), default_model=cfg.default_model)

    if cfg.type == "azure-openai":
        endpoint = os.environ.get(cfg.endpoint_env, "") if cfg.endpoint_env else ""
        return AzureFoundryClient(endpoint=endpoint, api_key=_resolve_api_key(cfg), default_model=cfg.default_model)

    if cfg.type == "azure-ai-foundry":
        return AnthropicClient(
            api_key=_resolve_api_key(cfg), default_model=cfg.default_model, base_url=cfg.base_url,
        )

    if cfg.type == "openai-compatible":
        return OpenAICompatClient(
            base_url=cfg.base_url,
            api_key=cfg.api_key or "not-needed",
            default_model=cfg.default_model,
        )

    raise ValueError(f"Unknown LLM provider type: {cfg.type}")


def resolve_model(
    config: GuardianConfig,
    agent_name: str,
    provider_name: str | None = None,
) -> str:
    """Resolve the model to use for a specific agent.

    Resolution order:
    1. repo agent_overrides[agent].model (from repo review.yml, stored in config)
    2. service agent_overrides[agent].model
    3. provider.default_model
    """
    # Check agent overrides
    override = config.llm.agent_overrides.get(agent_name)
    if override and override.model:
        return override.model

    # Fall back to provider default
    prov_name = provider_name or config.llm.default_provider
    provider = config.llm.providers.get(prov_name)
    if provider:
        return provider.default_model

    return "claude-sonnet-4-6"
