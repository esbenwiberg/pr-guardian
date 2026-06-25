"""Guard the validator → decision ordering in run_review().

The adversarial validator runs BEFORE decide(), so a finding the validator
dismisses can no longer drive the verdict. Without this ordering a PR could
read "Changes Requested" over findings the critic already killed (verdict and
visible evidence disagree). These tests lock the ordering in:

- A HIGH/DETECTED finding with a concrete suggestion forces REJECT.
- When the validator DISMISSES it, the verdict must soften (no longer REJECT)
  and decide() must see the reduced finding set.
- When the validator KEEPS it (control), the verdict stays REJECT.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.context import (
    ArchmapContext,
    BlastRadius,
    ChangeProfile,
    RiskTier,
    TrustTier,
    TrustTierResult,
)
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pr_guardian.triage.classifier import TriageResult


def _reject_finding() -> Finding:
    """A finding that forces REJECT under the default confident_only threshold:
    DETECTED certainty, HIGH severity, concrete suggestion, full evidence."""
    return Finding(
        severity=Severity.HIGH,
        certainty=Certainty.DETECTED,
        category="auth_bypass",
        language="python",
        file="src/auth/handler.py",
        line=42,
        description="Missing authorization check",
        evidence_basis=EvidenceBasis(
            saw_full_context=True,
            pattern_match=True,
            cwe_id="CWE-862",
            suggestion_is_concrete=True,
            cross_references=1,
        ),
    )


def _reject_agent_result() -> AgentResult:
    return AgentResult(
        agent_name="security_privacy",
        verdict=Verdict.WARN,
        findings=[_reject_finding()],
    )


def _minimal_pr() -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="101",
        repo="acme/service",
        repo_url="https://github.com/acme/service",
        source_branch="feat/x",
        target_branch="main",
        author="dev",
        title="feat: add handler",
        head_commit_sha="abc123",
    )


class _FakeAdapter:
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
    """Stub heavy pipeline steps; triage selects one agent that returns a
    REJECT-driving finding. Validator behavior is left to each test to set."""
    import pr_guardian.core.orchestrator as orch

    monkeypatch.setattr(orch, "_try_import_storage", lambda: None)
    monkeypatch.setattr(orch, "_is_stale_automatic_review", AsyncMock(return_value=True))
    monkeypatch.setattr(orch, "apply_global_settings", AsyncMock(side_effect=lambda c: c))
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
    trust_result = TrustTierResult(resolved_tier=TrustTier.AI_ONLY)
    monkeypatch.setattr(orch, "classify_trust_tier", lambda *a, **kw: trust_result)
    monkeypatch.setattr(orch, "maybe_escalate_trust", lambda *a, **kw: trust_result)
    monkeypatch.setattr(orch, "run_mechanical_checks", AsyncMock(return_value=[]))
    monkeypatch.setattr(orch, "all_checks_passed", lambda results: True)
    # Triage selects the security agent so a finding flows into the pipeline.
    triage = TriageResult(risk_tier=RiskTier.MEDIUM, agent_set={"security_privacy"})
    monkeypatch.setattr(orch, "classify", lambda *a, **kw: triage)
    # The security agent returns the REJECT-driving finding.
    from pr_guardian.agents.security_privacy import SecurityPrivacyAgent

    async def _fake_review(self, ctx, dismissal_context=None):
        return _reject_agent_result()

    monkeypatch.setattr(SecurityPrivacyAgent, "review", _fake_review)
    # Severity floor: passthrough (don't suppress the HIGH finding).
    monkeypatch.setattr(orch, "filter_findings", lambda results, *a, **kw: (results, 0))
    monkeypatch.setattr(orch, "_save_result", AsyncMock())


@pytest.mark.asyncio
async def test_validator_dismissal_softens_verdict(monkeypatch):
    """Validator dismisses the REJECT-driving finding → verdict must not be REJECT,
    and decide() must see the dismissed (empty) finding set."""
    import pr_guardian.core.orchestrator as orch

    _patch_pipeline(monkeypatch)

    # Validator dismisses everything: returns agent results with no findings.
    async def _dismissing_validator(agent_results, context, config, *a, **kw):
        cleared = [
            AgentResult(agent_name=ar.agent_name, verdict=Verdict.PASS, findings=[])
            for ar in agent_results
        ]
        return cleared, {"validator_ran": True, "dismissed": 1, "downgraded": 0}

    monkeypatch.setattr(orch, "validate_findings", _dismissing_validator)

    # Capture the finding set decide() actually receives.
    seen: list[int] = []
    real_decide = orch.decide

    def _tracking_decide(ctx, agent_results, *a, **kw):
        seen.append(sum(len(ar.findings) for ar in agent_results))
        return real_decide(ctx, agent_results, *a, **kw)

    monkeypatch.setattr(orch, "decide", _tracking_decide)

    result = await orch.run_review(
        _minimal_pr(), _FakeAdapter(), service_config=GuardianConfig(), post_comment=False
    )

    assert seen == [0], "decide() must run on the validated (dismissed) finding set"
    assert result.decision != Decision.REJECT, (
        "verdict must soften once the validator dismisses the driving finding"
    )


@pytest.mark.asyncio
async def test_validator_kept_finding_still_rejects(monkeypatch):
    """Control: validator keeps the finding → verdict stays REJECT."""
    import pr_guardian.core.orchestrator as orch

    _patch_pipeline(monkeypatch)

    # Validator is a no-op: returns findings unchanged.
    async def _noop_validator(agent_results, context, config, *a, **kw):
        return agent_results, {"validator_ran": True, "dismissed": 0, "downgraded": 0}

    monkeypatch.setattr(orch, "validate_findings", _noop_validator)

    result = await orch.run_review(
        _minimal_pr(), _FakeAdapter(), service_config=GuardianConfig(), post_comment=False
    )

    assert result.decision == Decision.REJECT, (
        "a kept HIGH/DETECTED finding with concrete fix must still force REJECT"
    )
