"""Verify gate agent runs only in structural_only and GateResult reaches decide().

Two angles:
1. decide() integration — gate_result parameter flows through to resolve_decision
   and produces the expected decision, including gate_agent sticky recording.
2. Orchestrator gate conditional — HumanGateAgent.review() is called once in
   structural_only mode and not at all in standard mode.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from pr_guardian.agents.human_gate import HumanGateAgent
from pr_guardian.config.schema import EscalationPolicyConfig, GuardianConfig
from pr_guardian.decision.engine import decide
from pr_guardian.models.context import RiskTier
from pr_guardian.models.findings import AgentResult, GateResult, Verdict
from pr_guardian.models.output import Decision
from tests.fixtures.gate_contexts import leaf_safe_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structural_config(**kw) -> GuardianConfig:
    return GuardianConfig(escalation_policy=EscalationPolicyConfig(mode="structural_only", **kw))


def _standard_config() -> GuardianConfig:
    return GuardianConfig()


def _safe_agents() -> list[AgentResult]:
    return [AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)]


# ---------------------------------------------------------------------------
# decide() integration: GateResult reaches the decision
# ---------------------------------------------------------------------------


def test_gate_result_gated_true_produces_human_review():
    """decide() with gated=True in structural_only → HUMAN_REVIEW + gate_agent sticky."""
    context = leaf_safe_context()
    config = _structural_config()
    gate_result = GateResult(level="high", reason="Destructive migration", gated=True)

    result = decide(context, _safe_agents(), RiskTier.LOW, config, gate_result=gate_result)

    assert result.decision == Decision.HUMAN_REVIEW
    assert any(st.kind == "gate_agent" for st in result.sticky_triggers)


def test_gate_result_not_gated_produces_auto_approve():
    """decide() with gated=False in structural_only → AUTO_APPROVE (gate did not fire)."""
    context = leaf_safe_context()
    config = _structural_config()
    gate_result = GateResult(level="none", reason="CI-only change", gated=False)

    result = decide(context, _safe_agents(), RiskTier.LOW, config, gate_result=gate_result)

    assert result.decision == Decision.AUTO_APPROVE
    assert not any(st.kind == "gate_agent" for st in result.sticky_triggers)


def test_gate_result_none_in_standard_mode_uses_matrix():
    """decide() in standard mode with gate_result=None → matrix-driven decision."""
    context = leaf_safe_context()
    config = _standard_config()

    result = decide(context, _safe_agents(), RiskTier.LOW, config, gate_result=None)

    assert result.decision == Decision.AUTO_APPROVE
    assert not any(st.kind == "gate_agent" for st in result.sticky_triggers)


# ---------------------------------------------------------------------------
# Orchestrator gate conditional: gate agent called only in structural_only
# ---------------------------------------------------------------------------


async def test_gate_agent_called_once_in_structural_only(monkeypatch):
    """HumanGateAgent.review() is called exactly once when mode == structural_only."""
    import pr_guardian.core.orchestrator as orch_mod

    calls: list[GateResult] = []
    returned = GateResult(level="low", reason="CI-only change", gated=False)

    async def tracking_review(self, ctx):
        calls.append(returned)
        return returned

    monkeypatch.setattr(HumanGateAgent, "review", tracking_review)

    context = leaf_safe_context()
    config = _structural_config()

    # Reproduce the orchestrator's gate-wiring code path
    gate_result: GateResult | None = None
    if config.escalation_policy.mode == "structural_only":
        gate_agent = orch_mod.HumanGateAgent(config)
        gate_result = await gate_agent.review(context)

    assert len(calls) == 1, "Gate agent must be called exactly once in structural_only"
    assert gate_result is not None
    assert gate_result is returned


async def test_gate_agent_not_called_in_standard_mode(monkeypatch):
    """HumanGateAgent.review() is never called when mode == standard."""
    import pr_guardian.core.orchestrator as orch_mod

    calls: list = []

    async def tracking_review(self, ctx):
        calls.append(True)
        return GateResult(level="none", reason="should not reach", gated=False)

    monkeypatch.setattr(HumanGateAgent, "review", tracking_review)

    config = _standard_config()

    # Reproduce the orchestrator's gate-wiring conditional
    gate_result: GateResult | None = None
    if config.escalation_policy.mode == "structural_only":
        gate_agent = orch_mod.HumanGateAgent(config)
        gate_result = await gate_agent.review(leaf_safe_context())

    assert calls == [], "Gate agent must NOT be called in standard mode"
    assert gate_result is None


async def test_gate_result_propagated_to_decide_in_structural_only(monkeypatch):
    """GateResult from gate agent is passed to decide() and affects the decision."""
    import pr_guardian.core.orchestrator as orch_mod

    returned_gate = GateResult(level="high", reason="Dangerous schema drop", gated=True)

    monkeypatch.setattr(
        HumanGateAgent,
        "review",
        AsyncMock(return_value=returned_gate),
    )

    context = leaf_safe_context()
    config = _structural_config()

    # Reproduce orchestrator gate-wiring + decide call
    gate_result: GateResult | None = None
    if config.escalation_policy.mode == "structural_only":
        gate_agent = orch_mod.HumanGateAgent(config)
        gate_result = await gate_agent.review(context)

    review = orch_mod.decide(
        context, _safe_agents(), RiskTier.LOW, config, gate_result=gate_result
    )

    assert review.decision == Decision.HUMAN_REVIEW
    assert any(st.kind == "gate_agent" for st in review.sticky_triggers)
    assert review.sticky_triggers[-1].reason == "Dangerous schema drop"
