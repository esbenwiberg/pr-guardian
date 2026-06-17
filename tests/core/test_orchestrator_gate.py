"""Verify gate agent runs only in structural_only and GateResult reaches decide().

Three layers:
1. decide() integration — gate_result flows through to resolve_decision and
   produces the expected decision, including gate_agent sticky recording.
2. Orchestrator conditional — HumanGateAgent.review() is called once in
   structural_only mode and not at all in standard mode, verified by running
   run_review() with the pipeline's heavy dependencies patched out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from pr_guardian.agents.human_gate import HumanGateAgent
from pr_guardian.config.schema import EscalationPolicyConfig, GuardianConfig
from pr_guardian.decision.engine import decide
from pr_guardian.models.context import (
    ArchmapContext,
    BlastRadius,
    ChangeProfile,
    RiskTier,
    TrustTier,
    TrustTierResult,
)
from pr_guardian.models.findings import AgentResult, GateResult, Verdict
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pr_guardian.triage.classifier import TriageResult
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


def _minimal_pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="99",
        repo="acme/service",
        repo_url="https://github.com/acme/service",
        source_branch="chore/bump-ci",
        target_branch="main",
        author="dev",
        title="chore: bump CI runner",
        head_commit_sha="abc123",
    )


class _FakeAdapter:
    """Minimal platform adapter stub — only fetch_diff is called by the pipeline."""

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        return Diff(files=[])

    async def get_pr_status(self, *a, **kw):
        return None

    async def set_pr_status(self, *a, **kw):
        pass

    async def post_comment(self, *a, **kw):
        return None

    async def get_comments(self, *a, **kw):
        return []

    async def approve_pr(self, *a, **kw):
        pass

    async def request_changes(self, *a, **kw):
        pass

    async def find_existing_comment(self, *a, **kw):
        return None

    async def update_comment(self, *a, **kw):
        pass


def _patch_pipeline(monkeypatch) -> None:
    """Patch all heavy pipeline steps so only the gate-wiring code runs."""
    import pr_guardian.core.orchestrator as orch

    # No storage — skip DB operations entirely
    monkeypatch.setattr(orch, "_try_import_storage", lambda: None)
    # Skip platform side-effects without raising
    monkeypatch.setattr(orch, "_is_stale_automatic_review", AsyncMock(return_value=True))
    # Config passthrough (no global settings to merge)
    monkeypatch.setattr(orch, "apply_global_settings", AsyncMock(side_effect=lambda c: c))
    # Discovery stubs
    monkeypatch.setattr(orch, "detect_languages", lambda files: LanguageMap())
    monkeypatch.setattr(
        orch,
        "build_security_surface",
        lambda cfg, files: __import__(
            "pr_guardian.models.context", fromlist=["SecuritySurface"]
        ).SecuritySurface(),
    )
    monkeypatch.setattr(orch, "build_dep_graph", lambda *a, **kw: None)
    monkeypatch.setattr(orch, "compute_blast_radius", lambda *a, **kw: BlastRadius())
    monkeypatch.setattr(orch, "build_change_profile", lambda *a, **kw: ChangeProfile())
    monkeypatch.setattr(orch, "load_hotspots", AsyncMock(return_value=[]))
    monkeypatch.setattr(orch, "_load_archmap_context", AsyncMock(return_value=ArchmapContext()))
    # Trust tier — trivial safe tier
    trust_result = TrustTierResult(resolved_tier=TrustTier.AI_ONLY)
    monkeypatch.setattr(orch, "classify_trust_tier", lambda *a, **kw: trust_result)
    monkeypatch.setattr(orch, "maybe_escalate_trust", lambda *a, **kw: trust_result)
    # Mechanical checks — all pass, no findings
    monkeypatch.setattr(orch, "run_mechanical_checks", AsyncMock(return_value=[]))
    monkeypatch.setattr(orch, "all_checks_passed", lambda results: True)
    # Triage — TRIVIAL, no agents
    triage = TriageResult(risk_tier=RiskTier.TRIVIAL, agent_set=set())
    monkeypatch.setattr(orch, "classify", lambda *a, **kw: triage)
    # Post-agent steps — correct return types
    monkeypatch.setattr(orch, "validate_findings", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(orch, "filter_findings", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(orch, "_save_result", AsyncMock())


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
    """decide() in standard mode with gate_result=None → matrix-driven AUTO_APPROVE."""
    context = leaf_safe_context()
    config = _standard_config()

    result = decide(context, _safe_agents(), RiskTier.LOW, config, gate_result=None)

    assert result.decision == Decision.AUTO_APPROVE
    assert not any(st.kind == "gate_agent" for st in result.sticky_triggers)


# ---------------------------------------------------------------------------
# Orchestrator pipeline integration: gate agent called only in structural_only
# ---------------------------------------------------------------------------


async def test_pipeline_calls_gate_only_in_structural_only(monkeypatch):
    """run_review() in structural_only calls HumanGateAgent.review(); standard does not."""
    import pr_guardian.core.orchestrator as orch

    _patch_pipeline(monkeypatch)

    gate_calls: list[str] = []  # record which mode triggered the call

    # Capture decide() calls to intercept gate_result without running the full engine
    decide_gate_results: list[GateResult | None] = []
    _real_decide = orch.decide

    def _tracking_decide(
        ctx, agent_results, risk_tier, config, trust_tier_result=None, gate_result=None
    ):
        decide_gate_results.append(gate_result)
        return _real_decide(
            ctx, agent_results, risk_tier, config, trust_tier_result, gate_result=gate_result
        )

    monkeypatch.setattr(orch, "decide", _tracking_decide)

    returned_gate = GateResult(level="none", reason="CI-only safe change", gated=False)

    async def tracking_review(self, ctx):
        gate_calls.append("called")
        return returned_gate

    monkeypatch.setattr(HumanGateAgent, "review", tracking_review)

    pr = _minimal_pr()
    adapter = _FakeAdapter()

    # --- Standard mode: gate agent must NOT run ---
    gate_calls.clear()
    decide_gate_results.clear()
    await orch.run_review(pr, adapter, service_config=_standard_config(), post_comment=False)
    assert gate_calls == [], "Gate agent must not run in standard mode"
    assert decide_gate_results == [None], "decide() called with gate_result=None in standard mode"

    # --- structural_only mode: gate agent MUST run ---
    gate_calls.clear()
    decide_gate_results.clear()
    await orch.run_review(pr, adapter, service_config=_structural_config(), post_comment=False)
    assert gate_calls == ["called"], "Gate agent must run exactly once in structural_only"
    assert decide_gate_results == [returned_gate], (
        "decide() called with GateResult in structural_only"
    )


async def test_pipeline_gate_result_gated_produces_human_review_in_structural_only(monkeypatch):
    """run_review() in structural_only with gated=True gate agent → HUMAN_REVIEW decision."""
    import pr_guardian.core.orchestrator as orch

    _patch_pipeline(monkeypatch)

    gated_result = GateResult(level="high", reason="Dangerous schema migration", gated=True)
    monkeypatch.setattr(HumanGateAgent, "review", AsyncMock(return_value=gated_result))

    pr = _minimal_pr()
    result = await orch.run_review(
        pr, _FakeAdapter(), service_config=_structural_config(), post_comment=False
    )

    assert result.decision == Decision.HUMAN_REVIEW
    assert any(st.kind == "gate_agent" for st in result.sticky_triggers)
    assert result.sticky_triggers[-1].reason == "Dangerous schema migration"
