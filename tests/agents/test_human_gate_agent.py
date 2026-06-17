"""Tests for HumanGateAgent — parse, blind-to-findings, and fail-closed contracts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pr_guardian.agents.human_gate import HumanGateAgent, _build_gate_context
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.protocol import LLMResponse
from pr_guardian.models.findings import GateResult
from pr_guardian.persistence import storage
from tests.fixtures.gate_contexts import hub_destructive_context, leaf_safe_context


class _FakeLLM:
    """Minimal LLM stub that returns a fixed JSON string."""

    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.1,
        response_format: str | None = None,
    ) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "response_format": response_format})
        return LLMResponse(
            content=self.content, model=model or "fake-model", input_tokens=10, output_tokens=5
        )


class _ErrorLLM:
    """LLM stub that always raises on completion."""

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(self, *args, **kwargs) -> LLMResponse:  # type: ignore[override]
        raise RuntimeError("LLM timeout")


# ---------------------------------------------------------------------------
# parse-graded-level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level,expected_gated",
    [
        ("none", False),
        ("low", False),
        ("medium", True),
        ("high", True),
    ],
)
async def test_parse_graded_level(monkeypatch, level, expected_gated):
    """Agent parses level + reason from a valid JSON LLM response."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    response_json = json.dumps({"level": level, "reason": f"Change is {level} risk."})
    agent = HumanGateAgent(GuardianConfig(), llm_client=_FakeLLM(response_json))

    result = await agent.review(leaf_safe_context())

    assert isinstance(result, GateResult)
    assert result.level == level
    assert result.reason == f"Change is {level} risk."
    assert result.gated is expected_gated
    assert result.error is None


async def test_parse_high_level_from_hub_context(monkeypatch):
    """Hub-context PR with a high-danger response yields gated=True."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    response_json = json.dumps(
        {
            "level": "high",
            "reason": "Drops user_tokens table via an irreversible migration on a hub file.",
        }
    )
    agent = HumanGateAgent(GuardianConfig(), llm_client=_FakeLLM(response_json))

    result = await agent.review(hub_destructive_context())

    assert result.level == "high"
    assert result.gated is True
    assert "irreversible" in result.reason


# ---------------------------------------------------------------------------
# blind-to-findings
# ---------------------------------------------------------------------------


async def test_blind_to_findings_context_contains_diff_and_archmap(monkeypatch):
    """The gate context contains diff, changed files, and archmap data."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    llm = _FakeLLM(json.dumps({"level": "low", "reason": "Safe CI change."}))
    agent = HumanGateAgent(GuardianConfig(), llm_client=llm)
    ctx = leaf_safe_context()

    await agent.review(ctx)

    assert llm.calls, "expected at least one LLM call"
    user_message = llm.calls[0]["user"]

    # Must contain diff content
    assert "ubuntu-22.04" in user_message, "diff patch should be in user message"
    # Must contain changed-file list
    assert ".github/workflows/ci.yml" in user_message
    # Must contain archmap classification
    assert "leaf" in user_message


async def test_blind_to_findings_hub_context_includes_hub_marker(monkeypatch):
    """Archmap hub classification renders in the user message."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    llm = _FakeLLM(json.dumps({"level": "high", "reason": "Hub file + destructive migration."}))
    agent = HumanGateAgent(GuardianConfig(), llm_client=llm)
    ctx = hub_destructive_context()

    await agent.review(ctx)

    user_message = llm.calls[0]["user"]
    assert "hub" in user_message.lower()
    assert "HUB" in user_message
    assert "orchestrator.py" in user_message


async def test_blind_to_findings_no_findings_in_context():
    """_build_gate_context never mentions findings or agent results."""
    ctx = hub_destructive_context()
    rendered = _build_gate_context(ctx)

    # The rendered context must not contain findings-related vocabulary that
    # would allow finding-certainty to re-enter as a human gate.
    assert "finding" not in rendered.lower()
    assert "AgentResult" not in rendered
    assert "verdict" not in rendered.lower()
    assert "severity" not in rendered.lower()
    assert "certainty" not in rendered.lower()


# ---------------------------------------------------------------------------
# fail-closed-on-error
# ---------------------------------------------------------------------------


async def test_fail_closed_on_llm_exception(monkeypatch):
    """LLM exception → GateResult(level='high', gated=True, error set)."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    agent = HumanGateAgent(GuardianConfig(), llm_client=_ErrorLLM())
    result = await agent.review(leaf_safe_context())

    assert result.level == "high"
    assert result.gated is True
    assert result.error is not None
    assert "LLM timeout" in result.error


async def test_fail_closed_on_invalid_json(monkeypatch):
    """Invalid JSON from LLM → fail-closed GateResult, never a passing level."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    agent = HumanGateAgent(GuardianConfig(), llm_client=_FakeLLM("not json at all"))
    result = await agent.review(leaf_safe_context())

    assert result.level == "high"
    assert result.gated is True
    assert result.error is not None


async def test_fail_closed_on_unknown_level(monkeypatch):
    """Unknown level string in JSON → treated as high, never a silent pass."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    agent = HumanGateAgent(
        GuardianConfig(),
        llm_client=_FakeLLM(json.dumps({"level": "???", "reason": "oops"})),
    )
    result = await agent.review(leaf_safe_context())

    assert result.level == "high"
    assert result.gated is True


async def test_fail_closed_never_returns_none_level_on_error(monkeypatch):
    """Fail path must never return level='none' (which would be a silent pass)."""
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))

    agent = HumanGateAgent(GuardianConfig(), llm_client=_ErrorLLM())
    result = await agent.review(hub_destructive_context())

    assert result.level != "none", "fail-closed must never return 'none' level"
    assert result.gated is True
