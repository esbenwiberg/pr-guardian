"""Decision matrix tests for the structural_only escalation branch.

Covers every scenario in the contract:
- safe paths + finding noise → AUTO_APPROVE
- safe paths + confident finding (confident_only) → REJECT
- archmap hub sticky → HUMAN_REVIEW
- gate_result.gated=True → HUMAN_REVIEW + gate_agent sticky recorded
- gate error (fail-closed, gated=True) → HUMAN_REVIEW
- trust tier mandatory_human → HUMAN_REVIEW
- score >= hard_block_score → HARD_BLOCK
- standard mode regression: identical to pre-feature behavior
"""

from __future__ import annotations

from pr_guardian.config.schema import EscalationPolicyConfig, GuardianConfig
from pr_guardian.decision.engine import resolve_decision
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import RepoRiskClass, RiskTier, TrustTier
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    GateResult,
    Severity,
    Verdict,
)
from pr_guardian.models.output import Decision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    mode: str = "structural_only", reject_threshold: str = "confident_only"
) -> GuardianConfig:
    return GuardianConfig(
        escalation_policy=EscalationPolicyConfig(mode=mode, reject_threshold=reject_threshold)
    )


def _safe_result() -> AgentResult:
    return AgentResult(agent_name="security_privacy", verdict=Verdict.PASS)


def _confident_finding() -> Finding:
    """A detected HIGH finding with a concrete suggestion — triggers confident_only reject."""
    return Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="injection",
        language="python",
        file="app.py",
        line=10,
        description="SQL injection risk",
        suggestion="Use parameterized queries",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-89",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )


def _noisy_result() -> AgentResult:
    """9 suspected + 1 detected-medium without concrete suggestion — not reject-worthy."""
    suspected = [
        Finding(
            severity=Severity.MEDIUM,
            certainty=Certainty.SUSPECTED,
            category="noise",
            language="python",
            file=f"file{i}.py",
            line=i,
            description=f"Suspected issue {i}",
            evidence_basis=EvidenceBasis(pattern_match=True),
        )
        for i in range(9)
    ]
    detected_medium = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="detected_medium",
        language="python",
        file="other.py",
        line=1,
        description="Detected medium without concrete suggestion",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-200",
            suggestion_is_concrete=False,  # not reject-worthy at confident_only threshold
            cross_references=1,
        ),
    )
    return AgentResult(
        agent_name="security_privacy",
        verdict=Verdict.WARN,
        findings=suspected + [detected_medium],
    )


def _hub_trigger() -> StickyTrigger:
    return StickyTrigger(
        kind="archmap_hub",
        label="Archmap hub touched: src/core/orchestrator.py",
        source="src/core/orchestrator.py",
        reason="Hub file with 40 dependents",
    )


def _base_kwargs(**overrides) -> dict:
    base: dict = dict(
        risk_tier=RiskTier.LOW,
        repo_risk=RepoRiskClass.STANDARD,
        agent_results=[_safe_result()],
        score=1.0,
        config=_make_config(),
        trust_tier=None,
        sticky_triggers=[],
        finding_reasons=[],
        target_branch="main",
        gate_result=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# structural_only scenarios
# ---------------------------------------------------------------------------


def test_safe_paths_with_finding_noise_auto_approves():
    """Safe paths + 9 suspected + 1 detected-medium → AUTO_APPROVE in structural_only."""
    kwargs = _base_kwargs(agent_results=[_noisy_result()], score=2.0)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


def test_safe_with_confident_finding_rejects():
    """Safe paths + detected HIGH with concrete suggestion → REJECT (not HUMAN_REVIEW)."""
    result = AgentResult(
        agent_name="security_privacy",
        verdict=Verdict.FLAG_HUMAN,
        findings=[_confident_finding()],
    )
    finding_reasons: list[str] = []
    kwargs = _base_kwargs(agent_results=[result], score=6.0, finding_reasons=finding_reasons)
    assert resolve_decision(**kwargs) == Decision.REJECT
    assert finding_reasons, "reject reason should have been appended"


def test_archmap_hub_gates_human():
    """Archmap hub sticky trigger → HUMAN_REVIEW (no finding-based gate)."""
    stickies = [_hub_trigger()]
    kwargs = _base_kwargs(sticky_triggers=stickies)
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_gate_agent_gated_true_gates_human_and_records_sticky():
    """GateResult.gated=True → HUMAN_REVIEW + gate_agent sticky appended."""
    gate_result = GateResult(level="high", reason="Destructive migration detected", gated=True)
    stickies: list[StickyTrigger] = []
    kwargs = _base_kwargs(sticky_triggers=stickies, gate_result=gate_result)
    decision = resolve_decision(**kwargs)
    assert decision == Decision.HUMAN_REVIEW
    assert any(st.kind == "gate_agent" for st in stickies)


def test_gate_agent_not_gated_does_not_escalate():
    """GateResult.gated=False with safe paths → AUTO_APPROVE."""
    gate_result = GateResult(level="low", reason="CI-only change", gated=False)
    kwargs = _base_kwargs(gate_result=gate_result)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


def test_gate_error_fails_closed():
    """GateResult with error set (gated=True by fail-closed) → HUMAN_REVIEW."""
    gate_result = GateResult(level="high", reason="", gated=True, error="LLM timeout")
    kwargs = _base_kwargs(gate_result=gate_result)
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_trust_tier_mandatory_human_gates():
    """Trust tier mandatory_human → HUMAN_REVIEW in structural_only."""
    kwargs = _base_kwargs(trust_tier=TrustTier.MANDATORY_HUMAN)
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_trust_tier_human_primary_gates():
    """Trust tier human_primary → HUMAN_REVIEW in structural_only."""
    kwargs = _base_kwargs(trust_tier=TrustTier.HUMAN_PRIMARY)
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_hard_block_still_applies():
    """Score >= hard_block_score → HARD_BLOCK even in structural_only."""
    config = _make_config()
    kwargs = _base_kwargs(score=config.thresholds.hard_block_score, config=config)
    assert resolve_decision(**kwargs) == Decision.HARD_BLOCK


def test_hard_block_overrides_human_review():
    """Score >= hard_block_score with structural trigger → HARD_BLOCK (not HUMAN_REVIEW)."""
    config = _make_config()
    stickies = [_hub_trigger()]
    kwargs = _base_kwargs(
        score=config.thresholds.hard_block_score,
        config=config,
        sticky_triggers=stickies,
    )
    assert resolve_decision(**kwargs) == Decision.HARD_BLOCK


def test_confident_finding_overrides_human_review_to_reject():
    """Hub (→ HUMAN_REVIEW) + confident finding (→ REJECT): REJECT wins."""
    result = AgentResult(
        agent_name="security_privacy",
        verdict=Verdict.FLAG_HUMAN,
        findings=[_confident_finding()],
    )
    stickies = [_hub_trigger()]
    kwargs = _base_kwargs(agent_results=[result], score=4.0, sticky_triggers=stickies)
    assert resolve_decision(**kwargs) == Decision.REJECT


def test_replayed_gate_agent_sticky_escalates_in_structural_only():
    """A gate_agent sticky replayed from a previous review escalates to HUMAN_REVIEW."""
    replayed_gate_sticky = StickyTrigger(
        kind="gate_agent",
        label="Gate agent: HIGH danger",
        source="gate_agent",
        reason="Destructive migration from prior review",
    )
    kwargs = _base_kwargs(sticky_triggers=[replayed_gate_sticky])
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


# ---------------------------------------------------------------------------
# reject_threshold variants: medium_plus and any
# ---------------------------------------------------------------------------


def test_medium_plus_threshold_rejects_detected_medium():
    """reject_threshold=medium_plus: detected MEDIUM with concrete suggestion → REJECT."""
    medium_finding = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="injection",
        language="python",
        file="app.py",
        line=5,
        description="Input not sanitised",
        suggestion="Escape the value before use",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-79",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )
    result = AgentResult(
        agent_name="security_privacy", verdict=Verdict.FLAG_HUMAN, findings=[medium_finding]
    )
    config = _make_config(reject_threshold="medium_plus")
    kwargs = _base_kwargs(agent_results=[result], score=2.0, config=config)
    assert resolve_decision(**kwargs) == Decision.REJECT


def test_medium_plus_threshold_does_not_reject_suspected_medium():
    """reject_threshold=medium_plus: suspected MEDIUM (not detected) → AUTO_APPROVE, not REJECT."""
    suspected_medium = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.SUSPECTED,
        category="style",
        language="python",
        file="app.py",
        line=5,
        description="Potential issue",
        evidence_basis=EvidenceBasis(pattern_match=True),
    )
    result = AgentResult(
        agent_name="security_privacy", verdict=Verdict.WARN, findings=[suspected_medium]
    )
    config = _make_config(reject_threshold="medium_plus")
    kwargs = _base_kwargs(agent_results=[result], score=1.0, config=config)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


def test_any_threshold_rejects_on_any_finding():
    """reject_threshold=any: even a suspected LOW finding → REJECT (most aggressive setting)."""
    low_suspected = Finding(
        severity=Severity.LOW,
        certainty=Certainty.SUSPECTED,
        category="style",
        language="python",
        file="app.py",
        line=1,
        description="Minor style issue",
        evidence_basis=EvidenceBasis(),
    )
    result = AgentResult(
        agent_name="security_privacy", verdict=Verdict.WARN, findings=[low_suspected]
    )
    config = _make_config(reject_threshold="any")
    kwargs = _base_kwargs(agent_results=[result], score=0.5, config=config)
    assert resolve_decision(**kwargs) == Decision.REJECT


def test_any_threshold_auto_approves_when_no_findings():
    """reject_threshold=any: no findings at all → AUTO_APPROVE (any only fires on findings)."""
    config = _make_config(reject_threshold="any")
    kwargs = _base_kwargs(agent_results=[_safe_result()], score=0.0, config=config)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


def test_confident_only_does_not_reject_detected_medium_without_suggestion():
    """reject_threshold=confident_only: detected MEDIUM without concrete suggestion → AUTO_APPROVE."""
    medium_no_suggestion = Finding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="perf",
        language="python",
        file="app.py",
        line=3,
        description="Slow query pattern",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-400",
            suggestion_is_concrete=False,
            cross_references=1,
        ),
    )
    result = AgentResult(
        agent_name="security_privacy", verdict=Verdict.WARN, findings=[medium_no_suggestion]
    )
    config = _make_config(reject_threshold="confident_only")
    kwargs = _base_kwargs(agent_results=[result], score=2.0, config=config)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


# ---------------------------------------------------------------------------
# Standard mode regression
# ---------------------------------------------------------------------------


def test_standard_mode_unchanged_with_structural_trigger():
    """Standard mode: sticky trigger + finding reasons → HUMAN_REVIEW (pre-feature behavior)."""
    config = _make_config(mode="standard")
    stickies = [_hub_trigger()]
    finding_reasons = ["Agent security_privacy flagged for human review"]
    kwargs = _base_kwargs(
        config=config,
        sticky_triggers=stickies,
        finding_reasons=finding_reasons,
        gate_result=None,
    )
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_standard_mode_no_triggers_auto_approves():
    """Standard mode: no triggers, low risk, passing agent → AUTO_APPROVE."""
    config = _make_config(mode="standard")
    kwargs = _base_kwargs(config=config, gate_result=None)
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE


def test_standard_mode_finding_noise_gates_human():
    """Standard mode: 9 suspected findings → HUMAN_REVIEW (pre-feature behavior unchanged)."""
    config = _make_config(mode="standard")
    finding_reasons = ["9 suspected findings (threshold: 3)"]
    kwargs = _base_kwargs(
        config=config,
        agent_results=[_noisy_result()],
        score=2.0,
        finding_reasons=finding_reasons,
        gate_result=None,
    )
    assert resolve_decision(**kwargs) == Decision.HUMAN_REVIEW


def test_standard_mode_ignores_gate_result_parameter():
    """Standard mode with gate_result passed (should not affect anything) → AUTO_APPROVE."""
    config = _make_config(mode="standard")
    gate_result = GateResult(level="high", reason="Ignored in standard mode", gated=True)
    kwargs = _base_kwargs(config=config, gate_result=gate_result)
    # In standard mode gate_result is None conceptually — but even if passed, the
    # standard path doesn't inspect it, so result is driven by matrix alone.
    # With LOW risk, STANDARD repo, passing agent → AUTO_APPROVE.
    assert resolve_decision(**kwargs) == Decision.AUTO_APPROVE
