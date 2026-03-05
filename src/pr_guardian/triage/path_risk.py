from __future__ import annotations

from fnmatch import fnmatch

from pr_guardian.config.schema import PathRiskConfig
from pr_guardian.models.context import RiskTier


TIER_ORDER = [RiskTier.TRIVIAL, RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH]


def _tier_index(tier: RiskTier) -> int:
    return TIER_ORDER.index(tier)


def _tier_from_str(s: str) -> RiskTier | None:
    mapping = {"trivial": RiskTier.TRIVIAL, "low": RiskTier.LOW,
               "medium": RiskTier.MEDIUM, "high": RiskTier.HIGH}
    return mapping.get(s.lower())


def apply_path_risk(
    base_tier: RiskTier,
    changed_files: list[str],
    path_risk: PathRiskConfig,
    has_production_changes: bool,
) -> tuple[RiskTier, list[str]]:
    """Apply path-level risk floors/ceilings. Returns adjusted tier + reasons."""
    tier = base_tier
    reasons: list[str] = []

    # Apply critical_paths (floor — can only raise tier)
    for entry in path_risk.critical_paths:
        if any(fnmatch(f, entry.pattern) for f in changed_files):
            min_tier = _tier_from_str(entry.min_tier)
            if min_tier and _tier_index(min_tier) > _tier_index(tier):
                reasons.append(f"Path risk: {entry.pattern} → min {entry.min_tier} ({entry.reason})")
                tier = min_tier

    # Apply safe_paths (ceiling — can only lower tier)
    for entry in path_risk.safe_paths:
        if not any(fnmatch(f, entry.pattern) for f in changed_files):
            continue
        # Check condition
        if entry.condition == "no_production_changes" and has_production_changes:
            continue
        max_tier = _tier_from_str(entry.max_tier)
        if max_tier and _tier_index(max_tier) < _tier_index(tier):
            reasons.append(f"Safe path: {entry.pattern} → max {entry.max_tier} ({entry.reason})")
            tier = max_tier

    return tier, reasons
