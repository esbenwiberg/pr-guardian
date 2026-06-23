"""Tests for path-risk floors/ceilings on the trust-tier (governance) axis.

These cover the invariant that the profiles "Path risk" editor actually moves
the resolved trust tier — the bug being that the editor saves trust-tier
strings (ai_only/spot_check/mandatory_human/human_primary) while the old
consumer parsed RiskTier strings (trivial/low/medium/high) and silently
no-op'd every rule.

Critical paths raise a floor (min_tier, can only increase governance); safe
paths lower a ceiling (max_tier, can only decrease governance); a blank tier is
no constraint; the no_production_changes condition voids a safe-path ceiling
when the PR touches production code; and a safe-path ceiling can never pierce
the CRITICAL repo-risk-class hard floor.
"""

import pytest

from pr_guardian.config.schema import (
    GuardianConfig,
    PathRiskConfig,
    PathRiskEntry,
    TrustTierConfig,
    TrustTierRule,
)
from pr_guardian.models.context import RepoRiskClass, TrustTier
from pr_guardian.triage.trust_classifier import classify_trust_tier


def _config_with_base(tier: str, pattern: str, path_risk: PathRiskConfig) -> GuardianConfig:
    """Pin the base trust tier for ``pattern`` via an explicit rule.

    Explicit trust_tiers.rules retire the built-in globs, so the base tier is
    deterministic and the path-risk adjustment is the only other mover.
    """
    return GuardianConfig(
        trust_tiers=TrustTierConfig(
            default_tier="spot_check",
            rules=[TrustTierRule(tier=tier, patterns=[pattern], reason="test base")],
        ),
        path_risk=path_risk,
    )


def test_critical_path_raises_floor():
    cfg = _config_with_base(
        "spot_check",
        "src/widget.py",
        PathRiskConfig(
            critical_paths=[
                PathRiskEntry(pattern="src/widget.py", min_tier="human_primary", reason="core")
            ]
        ),
    )
    result = classify_trust_tier(["src/widget.py"], cfg)
    assert result.resolved_tier == TrustTier.HUMAN_PRIMARY
    assert any("floor human_primary" in r for r in result.reasons)


def test_safe_path_lowers_ceiling():
    cfg = _config_with_base(
        "human_primary",
        "src/feature/x.py",
        PathRiskConfig(
            safe_paths=[
                PathRiskEntry(pattern="src/feature/**", max_tier="spot_check", reason="reviewed")
            ]
        ),
    )
    result = classify_trust_tier(["src/feature/x.py"], cfg)
    assert result.resolved_tier == TrustTier.SPOT_CHECK
    assert any("ceiling spot_check" in r for r in result.reasons)


def test_critical_floor_only_raises_never_lowers():
    cfg = _config_with_base(
        "human_primary",
        "src/widget.py",
        PathRiskConfig(
            critical_paths=[PathRiskEntry(pattern="src/widget.py", min_tier="ai_only")]
        ),
    )
    result = classify_trust_tier(["src/widget.py"], cfg)
    # Floor is below the base tier — it must not pull the tier down.
    assert result.resolved_tier == TrustTier.HUMAN_PRIMARY


def test_safe_ceiling_only_lowers_never_raises():
    cfg = _config_with_base(
        "ai_only",
        "src/widget.py",
        PathRiskConfig(
            safe_paths=[PathRiskEntry(pattern="src/widget.py", max_tier="human_primary")]
        ),
    )
    result = classify_trust_tier(["src/widget.py"], cfg)
    # Ceiling is above the base tier — it must not push the tier up.
    assert result.resolved_tier == TrustTier.AI_ONLY


def test_blank_tier_is_noop():
    cfg = _config_with_base(
        "spot_check",
        "src/widget.py",
        PathRiskConfig(
            critical_paths=[PathRiskEntry(pattern="src/widget.py", min_tier="")],
            safe_paths=[PathRiskEntry(pattern="src/widget.py", max_tier="")],
        ),
    )
    result = classify_trust_tier(["src/widget.py"], cfg)
    assert result.resolved_tier == TrustTier.SPOT_CHECK


@pytest.mark.parametrize(
    "has_prod,expected",
    [(True, TrustTier.HUMAN_PRIMARY), (False, TrustTier.AI_ONLY)],
)
def test_safe_path_condition_gates_ceiling(has_prod, expected):
    cfg = _config_with_base(
        "human_primary",
        "src/feature/x.py",
        PathRiskConfig(
            safe_paths=[
                PathRiskEntry(
                    pattern="src/feature/**",
                    max_tier="ai_only",
                    condition="no_production_changes",
                )
            ]
        ),
    )
    result = classify_trust_tier(["src/feature/x.py"], cfg, has_production_changes=has_prod)
    assert result.resolved_tier == expected


def test_safe_ceiling_cannot_pierce_critical_repo_floor():
    cfg = GuardianConfig(
        path_risk=PathRiskConfig(
            safe_paths=[PathRiskEntry(pattern="**/docs/**", max_tier="ai_only")]
        )
    )
    # docs-only PR would normally cap at ai_only, but a CRITICAL repo class
    # re-floors at MANDATORY_HUMAN afterward.
    result = classify_trust_tier(
        ["docs/guide.md"], cfg, repo_risk_class=RepoRiskClass.CRITICAL
    )
    assert result.resolved_tier == TrustTier.MANDATORY_HUMAN


def test_unmatched_path_risk_leaves_tier_untouched():
    cfg = _config_with_base(
        "spot_check",
        "src/widget.py",
        PathRiskConfig(
            critical_paths=[
                PathRiskEntry(pattern="other/**", min_tier="human_primary")
            ]
        ),
    )
    result = classify_trust_tier(["src/widget.py"], cfg)
    assert result.resolved_tier == TrustTier.SPOT_CHECK
