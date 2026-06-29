"""The claude-cli LLM provider is dev-only and must refuse in a deployed env."""

from __future__ import annotations

import pytest

from pr_guardian.config.schema import GuardianConfig, LLMConfig, LLMProviderConfig
from pr_guardian.llm.claude_cli import ClaudeCLIClient
from pr_guardian.llm.factory import _claude_cli_allowed, create_llm_client


def _cfg() -> GuardianConfig:
    cfg = GuardianConfig()
    cfg.llm = LLMConfig(
        default_provider="claude-cli",
        providers={"claude-cli": LLMProviderConfig(type="claude-cli", default_model="")},
    )
    return cfg


def test_allowed_when_dev_admin(monkeypatch):
    monkeypatch.setenv("GUARDIAN_DEV_ADMIN", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _claude_cli_allowed() is True
    client = create_llm_client(_cfg())
    assert isinstance(client, ClaudeCLIClient)
    assert client.provider_name == "claude-cli"


def test_allowed_when_no_db(monkeypatch):
    monkeypatch.delenv("GUARDIAN_DEV_ADMIN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GUARDIAN_DB_ENABLED", raising=False)
    assert _claude_cli_allowed() is True


def test_refused_in_deployed_env(monkeypatch):
    # Real DB configured + no dev flag = production-like → must refuse.
    monkeypatch.delenv("GUARDIAN_DEV_ADMIN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod/db")
    assert _claude_cli_allowed() is False
    with pytest.raises(ValueError, match="dev-only"):
        create_llm_client(_cfg())


def test_refused_when_db_enabled_flag(monkeypatch):
    monkeypatch.delenv("GUARDIAN_DEV_ADMIN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GUARDIAN_DB_ENABLED", "1")
    assert _claude_cli_allowed() is False
