"""Fix #2: re-review routes through the shared decision engine.

These cover the building blocks the re-review pipeline now uses instead of the
old hardcoded `any kept finding -> HUMAN_REVIEW`: resolve_decision (shared with
the full-review path) and the structural-signal replay helpers.
"""

from __future__ import annotations

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.orchestrator import (
    _parse_risk_tier,
    _parse_trust_tier,
    _replay_sticky_triggers,
)
from pr_guardian.decision.engine import finding_overrides, resolve_decision
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import RepoRiskClass, RiskTier, TrustTier
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)
from pr_guardian.models.output import Decision


def _cfg() -> GuardianConfig:
    return GuardianConfig()


def _finding(**ov) -> Finding:
    d = dict(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="test",
        language="python",
        file="a.py",
        line=1,
        description="x",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-89",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )
    d.update(ov)
    return Finding(**d)


def _resolve(agent_results, *, trust_tier=None, sticky=None, target="main"):
    sticky_triggers = list(sticky or [])
    finding_reasons = finding_overrides(agent_results, _cfg())
    score = 0.0
    if agent_results:
        from pr_guardian.decision.engine import combined_score

        score = combined_score(agent_results, _cfg())
    return resolve_decision(
        risk_tier=RiskTier.MEDIUM if agent_results else RiskTier.TRIVIAL,
        repo_risk=RepoRiskClass.STANDARD,
        agent_results=agent_results,
        score=score,
        config=_cfg(),
        trust_tier=trust_tier,
        sticky_triggers=sticky_triggers,
        finding_reasons=finding_reasons,
        target_branch=target,
    )


def test_all_resolved_no_trust_gate_auto_approves():
    """No kept findings, no structural gate -> AUTO_APPROVE (the new behavior)."""
    decision = _resolve([], trust_tier=TrustTier.AI_ONLY)
    assert decision == Decision.AUTO_APPROVE


def test_all_resolved_but_mandatory_human_still_blocks():
    """Clean re-review but MANDATORY_HUMAN trust still forces a human — this is the
    portfolio-simulation#287 case: the verdict is consistent with a full review."""
    decision = _resolve([], trust_tier=TrustTier.MANDATORY_HUMAN)
    assert decision == Decision.HUMAN_REVIEW


def test_low_medium_kept_findings_escalate():
    ar = AgentResult(agent_name="test_quality", verdict=Verdict.WARN, findings=[_finding()])
    decision = _resolve([ar], trust_tier=TrustTier.AI_ONLY)
    # A detected medium finding is a finding-reason -> escalates off AUTO_APPROVE.
    assert decision == Decision.HUMAN_REVIEW


def test_replayed_structural_sticky_blocks_even_when_findings_resolved():
    """A new-dependency sticky trigger from the original review must still gate a
    re-review where every finding was resolved."""
    sticky = [
        StickyTrigger(kind="new_dep", label="New dependency added", source="x", reason="dep")
    ]
    decision = _resolve([], trust_tier=TrustTier.AI_ONLY, sticky=sticky)
    assert decision == Decision.HUMAN_REVIEW


def test_replay_sticky_triggers_drops_trust_tier_and_rebuilds_rest():
    stored = [
        {"kind": "new_dep", "label": "New dep", "source": "requests", "reason": "r"},
        {"kind": "trust_tier", "label": "t", "source": "mandatory_human", "reason": "r"},
        {"kind": "hotspot", "label": "Hotspot", "source": "auth.py", "reason": "r"},
    ]
    out = _replay_sticky_triggers(stored)
    kinds = {t.kind for t in out}
    assert kinds == {"new_dep", "hotspot"}  # trust_tier dropped (re-derived)
    assert all(isinstance(t, StickyTrigger) for t in out)


def test_replay_sticky_triggers_tolerates_garbage():
    assert _replay_sticky_triggers([]) == []
    assert _replay_sticky_triggers([{"kind": "new_dep"}])  # missing fields default to ""
    assert _replay_sticky_triggers(["not a dict", 42]) == []


def test_parse_trust_tier():
    assert _parse_trust_tier("mandatory_human") == TrustTier.MANDATORY_HUMAN
    assert _parse_trust_tier("") is None
    assert _parse_trust_tier(None) is None
    assert _parse_trust_tier("bogus") is None


def test_parse_risk_tier_falls_back():
    assert _parse_risk_tier("high", fallback=RiskTier.LOW) == RiskTier.HIGH
    assert _parse_risk_tier("", fallback=RiskTier.MEDIUM) == RiskTier.MEDIUM
    assert _parse_risk_tier("bogus", fallback=RiskTier.TRIVIAL) == RiskTier.TRIVIAL
