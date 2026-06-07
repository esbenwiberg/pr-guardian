"""Tests for the deterministic fake LLM provider.

Required fact: fact-fake-llm-provider-deterministic
Command: python -m pytest tests/test_fake_llm_provider.py::test_fake_llm_provider_returns_stable_review_json_without_network
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pr_guardian.llm.fake import E2E_FINDING_MARKER, FakeLLMClient
from pr_guardian.llm.factory import _build_client
from pr_guardian.config.schema import LLMProviderConfig


@pytest.mark.asyncio
async def test_fake_llm_provider_returns_stable_review_json_without_network():
    """Fake provider emits identical JSON on every call with no network traffic."""
    client = FakeLLMClient()

    # Patch socket to guarantee no network access is attempted
    with patch("socket.socket") as mock_socket:
        resp1 = await client.complete(system="you are a reviewer", user="here is the diff")
        resp2 = await client.complete(system="you are a reviewer", user="here is the diff")

    # Responses are identical (deterministic)
    assert resp1.content == resp2.content

    # Parse as valid JSON
    data = json.loads(resp1.content)
    assert data["verdict"] == "pass"
    assert data["findings"] == []

    # socket was never touched
    mock_socket.assert_not_called()


@pytest.mark.asyncio
async def test_fake_llm_provider_returns_finding_when_marker_present():
    """Fake provider returns a warning finding when the E2E marker appears in user message."""
    client = FakeLLMClient()
    user_with_marker = f"diff --git a/e2e_fixture.py\n+# {E2E_FINDING_MARKER}\n"

    resp = await client.complete(system="you are a reviewer", user=user_with_marker)
    data = json.loads(resp.content)

    assert data["verdict"] == "warn"
    assert len(data["findings"]) == 1
    finding = data["findings"][0]
    assert finding["severity"] == "low"
    assert finding["certainty"] == "detected"
    assert finding["category"] == "e2e-fixture"


@pytest.mark.asyncio
async def test_fake_llm_provider_finding_is_stable_across_calls():
    """Finding output is identical across multiple calls — fully deterministic."""
    client = FakeLLMClient()
    user_with_marker = f"+# {E2E_FINDING_MARKER}"

    results = [await client.complete(system="reviewer", user=user_with_marker) for _ in range(3)]

    assert all(r.content == results[0].content for r in results[1:])


@pytest.mark.asyncio
async def test_fake_llm_provider_re_evaluate_mode_with_marker():
    """In re-evaluation mode the fake provider returns kept status when marker present."""
    client = FakeLLMClient()
    system = "You are in RE-EVALUATION MODE."
    user = f"finding 1 involves {E2E_FINDING_MARKER}"

    resp = await client.complete(system=system, user=user)
    data = json.loads(resp.content)

    assert "evaluations" in data
    assert data["evaluations"][0]["status"] == "kept"


@pytest.mark.asyncio
async def test_fake_llm_provider_re_evaluate_mode_clean_diff():
    """In re-evaluation mode without marker, fake provider returns empty evaluations."""
    client = FakeLLMClient()
    system = "You are in RE-EVALUATION MODE."

    resp = await client.complete(system=system, user="no findings here")
    data = json.loads(resp.content)

    assert data["evaluations"] == []


def test_fake_llm_provider_name():
    """Provider name is 'fake' for log/audit traceability."""
    assert FakeLLMClient().provider_name == "fake"


def test_fake_llm_factory_builds_from_config():
    """factory._build_client creates a FakeLLMClient for type='fake'."""
    cfg = LLMProviderConfig(type="fake", default_model="fake-deterministic-v1")
    client = _build_client(cfg)
    assert isinstance(client, FakeLLMClient)
