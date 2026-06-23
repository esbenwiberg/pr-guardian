"""Path-based trust tier classification with 3-layer config fallback.

Layer 1: Built-in defaults (always available)
Layer 2: Derived from security_surface config (if no explicit trust_tiers)
Layer 3: Explicit trust_tiers.rules (full control)
"""

from __future__ import annotations

from fnmatch import fnmatch

import structlog

from pr_guardian.config.schema import (
    GuardianConfig,
    PathRiskConfig,
    SecuritySurfaceConfig,
    TrustTierConfig,
)
from pr_guardian.models.context import (
    RepoRiskClass,
    TrustTier,
    TrustTierResult,
)

log = structlog.get_logger()

# Layer 1: Built-in defaults — common conventions that work across most codebases.
_BUILTIN_RULES: list[tuple[TrustTier, list[str], str]] = [
    (
        TrustTier.AI_ONLY,
        [
            "**/*.md",
            "**/docs/**",
            "CHANGELOG*",
            "**/package-lock.json",
            "**/*.lock",
            "**/migrations/**",
            "**/.prettierrc*",
            "**/.eslintrc*",
            "**/generated/**",
        ],
        "Formatting, docs, or generated files",
    ),
    (
        TrustTier.SPOT_CHECK,
        [
            "**/tests/**",
            "**/*.test.*",
            "**/*.spec.*",
            "**/controllers/**",
            "**/handlers/**",
            "**/models/**",
            "**/repositories/**",
        ],
        "Standard CRUD, tests, or data access",
    ),
    (
        TrustTier.MANDATORY_HUMAN,
        [
            "**/middleware/**",
            "**/services/**",
            "**/infra/**",
            "**/terraform/**",
            "**/docker/**",
            "**/k8s/**",
            "**/config/**",
            "**/.env*",
            ".github/workflows/**",
            "**/Dockerfile*",
        ],
        "Business logic, infrastructure, or configuration",
    ),
    (
        TrustTier.HUMAN_PRIMARY,
        [
            "**/auth/**",
            "**/crypto/**",
            "**/security/**",
            "**/middleware/auth*",
            "**/permissions/**",
            "**/rbac/**",
            "**/oauth/**",
            "**/jwt/**",
            "**/payments/**",
            "**/billing/**",
            "**/secrets/**",
        ],
        "Security-critical code",
    ),
]

# Layer 2: Mapping from security_surface classifications to trust tiers.
_SURFACE_TO_TIER: dict[str, TrustTier] = {
    "security_critical": TrustTier.HUMAN_PRIMARY,
    "infrastructure": TrustTier.MANDATORY_HUMAN,
    "configuration": TrustTier.MANDATORY_HUMAN,
    "input_handling": TrustTier.SPOT_CHECK,
    "data_access": TrustTier.SPOT_CHECK,
}


def classify_trust_tier(
    changed_files: list[str],
    config: GuardianConfig,
    repo_risk_class: RepoRiskClass = RepoRiskClass.STANDARD,
    archmap_available: bool = False,
    has_production_changes: bool = False,
) -> TrustTierResult:
    """Classify the PR's trust tier based on changed file paths.

    Resolution order:
    1. Explicit trust_tiers.rules from config (if defined)
    2. Derived from security_surface patterns (if customized)
    3. If archmap is available, NO path globs apply — every file falls to
       default_tier and Archmap topology (the ``archmap_hub`` sticky trigger)
       drives escalation instead of one-size-fits-all built-in globs.
    4. Built-in defaults (always available, last resort)
    """
    trust_config = config.trust_tiers
    rules = _resolve_rules(trust_config, config.security_surface, archmap_available)
    default_tier = _parse_tier(trust_config.default_tier, TrustTier.SPOT_CHECK)

    result = TrustTierResult(resolved_tier=default_tier)

    if not changed_files:
        result.reasons.append("No changed files — using default tier")
        return result

    # Classify each file against rules (highest tier wins per file)
    for file_path in changed_files:
        file_tier, reason = _classify_file(file_path, rules, default_tier)
        result.file_tiers[file_path] = file_tier
        if reason:
            result.reasons.append(f"{file_path}: {reason}")

    # PR-level tier = highest (least trusting) across all file tiers.
    # Unmatched files already received default_tier in per-file classification,
    # so the default is factored in through file_tiers values.
    all_tiers = list(result.file_tiers.values())
    pr_tier = max(all_tiers, key=lambda t: _TIER_ORDER[t])
    triggering: list[str] = [
        fp for fp, ft in result.file_tiers.items() if ft == pr_tier and ft != default_tier
    ]

    result.resolved_tier = pr_tier
    result.triggering_files = triggering

    # Operator path-risk floors/ceilings. Applied before the repo-risk-class
    # floor below so a safe-path ceiling can never pierce the CRITICAL hard
    # floor — a docs-only PR in a CRITICAL repo still gets a human.
    result.resolved_tier, path_reasons = _apply_path_risk(
        result.resolved_tier,
        changed_files,
        config.path_risk,
        has_production_changes,
    )
    result.reasons.extend(path_reasons)

    # Repo risk class floor: critical repos never go below MANDATORY_HUMAN
    if repo_risk_class == RepoRiskClass.CRITICAL:
        if _TIER_ORDER[result.resolved_tier] < _TIER_ORDER[TrustTier.MANDATORY_HUMAN]:
            result.resolved_tier = TrustTier.MANDATORY_HUMAN
            result.reasons.append("Repo risk class CRITICAL — floor at MANDATORY_HUMAN")

    # Set reviewer group override for HUMAN_PRIMARY
    if result.resolved_tier == TrustTier.HUMAN_PRIMARY:
        group = trust_config.reviewer_groups.get("human_primary", "security-team")
        if group:
            result.reviewer_group_override = group

    log.info(
        "trust_tier_classified",
        tier=result.resolved_tier.value,
        triggering_files=result.triggering_files[:5],
        file_count=len(changed_files),
    )
    return result


# -- internal helpers --------------------------------------------------------

_TIER_ORDER = {
    TrustTier.AI_ONLY: 0,
    TrustTier.SPOT_CHECK: 1,
    TrustTier.MANDATORY_HUMAN: 2,
    TrustTier.HUMAN_PRIMARY: 3,
}


def _parse_tier(value: str, default: TrustTier) -> TrustTier:
    try:
        return TrustTier(value)
    except ValueError:
        return default


def _parse_tier_or_none(value: str) -> TrustTier | None:
    """Parse a trust-tier string, or None if blank/unrecognized.

    Unlike :func:`_parse_tier`, an empty or junk value means "no constraint"
    rather than falling back to a default — a path-risk entry with no tier set
    simply does not apply.
    """
    try:
        return TrustTier(value)
    except ValueError:
        return None


def _apply_path_risk(
    tier: TrustTier,
    changed_files: list[str],
    path_risk: PathRiskConfig,
    has_production_changes: bool,
) -> tuple[TrustTier, list[str]]:
    """Apply operator path-risk floors and ceilings to a resolved trust tier.

    ``critical_paths`` raise the floor (``min_tier`` — can only increase
    governance); ``safe_paths`` lower the ceiling (``max_tier`` — can only
    decrease governance). A blank tier means no constraint. ``safe_paths`` may
    carry a ``no_production_changes`` condition that voids the ceiling when the
    PR also touches production code. Mirrors the floor/ceiling editor in the
    profiles UI.
    """
    reasons: list[str] = []

    # Critical paths — floor: raise the tier up to min_tier on a match.
    for entry in path_risk.critical_paths:
        min_tier = _parse_tier_or_none(entry.min_tier)
        if min_tier is None:
            continue
        if not any(_path_matches(f, entry.pattern) for f in changed_files):
            continue
        if _TIER_ORDER[min_tier] > _TIER_ORDER[tier]:
            tier = min_tier
            suffix = f" ({entry.reason})" if entry.reason else ""
            reasons.append(f"Path risk: {entry.pattern} → floor {min_tier.value}{suffix}")

    # Safe paths — ceiling: lower the tier down to max_tier on a match.
    for entry in path_risk.safe_paths:
        max_tier = _parse_tier_or_none(entry.max_tier)
        if max_tier is None:
            continue
        if not any(_path_matches(f, entry.pattern) for f in changed_files):
            continue
        if entry.condition == "no_production_changes" and has_production_changes:
            continue
        if _TIER_ORDER[max_tier] < _TIER_ORDER[tier]:
            tier = max_tier
            suffix = f" ({entry.reason})" if entry.reason else ""
            reasons.append(f"Safe path: {entry.pattern} → ceiling {max_tier.value}{suffix}")

    return tier, reasons


def _resolve_rules(
    trust_config: TrustTierConfig,
    surface_config: SecuritySurfaceConfig,
    archmap_available: bool = False,
) -> list[tuple[TrustTier, list[str], str]]:
    """Resolve effective trust-tier rules (see :func:`classify_trust_tier`)."""
    # Layer 3: Explicit rules take full precedence
    if trust_config.rules:
        return [
            (_parse_tier(r.tier, TrustTier.SPOT_CHECK), r.patterns, r.reason)
            for r in trust_config.rules
            if r.patterns
        ]

    # Layer 2: Derive from security_surface config (if non-default)
    derived = _derive_from_surface(surface_config)
    if derived:
        return derived

    # Archmap retires the built-in path globs: with topology data available and
    # no operator-defined rules, no glob escalates — files default to
    # default_tier and the archmap_hub sticky trigger handles risky files.
    if archmap_available:
        return []

    # Layer 1: Built-in defaults
    return list(_BUILTIN_RULES)


def _derive_from_surface(config: SecuritySurfaceConfig) -> list[tuple[TrustTier, list[str], str]]:
    """Derive trust tier rules from security_surface config.

    Only used when no explicit trust_tiers.rules are defined.
    Returns empty list if the security_surface config is all defaults.
    """
    default_config = SecuritySurfaceConfig()
    is_customized = (
        config.security_critical != default_config.security_critical
        or config.infrastructure != default_config.infrastructure
        or config.configuration != default_config.configuration
        or config.input_handling != default_config.input_handling
        or config.data_access != default_config.data_access
    )
    if not is_customized:
        return []

    rules: list[tuple[TrustTier, list[str], str]] = []
    surface_map = {
        "security_critical": config.security_critical,
        "infrastructure": config.infrastructure,
        "configuration": config.configuration,
        "input_handling": config.input_handling,
        "data_access": config.data_access,
    }
    for classification, patterns in surface_map.items():
        tier = _SURFACE_TO_TIER.get(classification, TrustTier.SPOT_CHECK)
        if patterns:
            rules.append((tier, patterns, f"Derived from security_surface.{classification}"))

    return rules


def _classify_file(
    file_path: str,
    rules: list[tuple[TrustTier, list[str], str]],
    default_tier: TrustTier,
) -> tuple[TrustTier, str]:
    """Match a single file against rules. Highest governance tier wins.

    If no rule matches, returns (default_tier, "").
    """
    matched_tier: TrustTier | None = None
    matched_reason = ""

    for tier, patterns, reason in rules:
        for pattern in patterns:
            if _path_matches(file_path, pattern):
                if matched_tier is None or _TIER_ORDER[tier] > _TIER_ORDER[matched_tier]:
                    matched_tier = tier
                    matched_reason = reason
                break  # matched this rule, move to next

    if matched_tier is not None:
        return matched_tier, matched_reason
    return default_tier, ""


def _path_matches(file_path: str, pattern: str) -> bool:
    """Match file path against a glob pattern, supporting ** for any depth.

    Python's fnmatch doesn't treat ** specially (it's just two wildcards),
    which means **/*.md won't match root-level files like README.md because
    fnmatch requires a / separator that isn't there.

    This helper progressively strips leading **/ prefixes so patterns like
    **/*.md also match files at the repository root.
    """
    if fnmatch(file_path, pattern):
        return True
    # Strip leading **/ to match files at any depth (including root)
    p = pattern
    while p.startswith("**/"):
        p = p[3:]
        if fnmatch(file_path, p):
            return True
    return False
