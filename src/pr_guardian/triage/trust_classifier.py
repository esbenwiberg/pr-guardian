"""Path-based trust tier classification with 3-layer config fallback.

Layer 1: Built-in defaults (always available)
Layer 2: Derived from security_surface config (if no explicit trust_tiers)
Layer 3: Explicit trust_tiers.rules (full control)
"""
from __future__ import annotations

from fnmatch import fnmatch

import structlog

from pr_guardian.config.schema import GuardianConfig, SecuritySurfaceConfig, TrustTierConfig
from pr_guardian.models.context import (
    RepoRiskClass,
    TrustTier,
    TrustTierResult,
    max_trust_tier,
)

log = structlog.get_logger()

# Layer 1: Built-in defaults — common conventions that work across most codebases.
_BUILTIN_RULES: list[tuple[TrustTier, list[str], str]] = [
    (TrustTier.AI_ONLY, [
        "**/*.md", "**/docs/**", "CHANGELOG*",
        "**/package-lock.json", "**/*.lock",
        "**/migrations/**", "**/.prettierrc*", "**/.eslintrc*",
        "**/generated/**",
    ], "Formatting, docs, or generated files"),

    (TrustTier.SPOT_CHECK, [
        "**/tests/**", "**/*.test.*", "**/*.spec.*",
        "**/controllers/**", "**/handlers/**",
        "**/models/**", "**/repositories/**",
    ], "Standard CRUD, tests, or data access"),

    (TrustTier.MANDATORY_HUMAN, [
        "**/middleware/**", "**/services/**",
        "**/infra/**", "**/terraform/**", "**/docker/**", "**/k8s/**",
        "**/config/**", "**/.env*",
        ".github/workflows/**", "**/Dockerfile*",
    ], "Business logic, infrastructure, or configuration"),

    (TrustTier.HUMAN_PRIMARY, [
        "**/auth/**", "**/crypto/**", "**/security/**",
        "**/middleware/auth*", "**/permissions/**", "**/rbac/**",
        "**/oauth/**", "**/jwt/**",
        "**/payments/**", "**/billing/**", "**/secrets/**",
    ], "Security-critical code"),
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
) -> TrustTierResult:
    """Classify the PR's trust tier based on changed file paths.

    Uses a 3-layer fallback:
    1. Explicit trust_tiers.rules from config (if defined)
    2. Derived from security_surface patterns (if no explicit rules)
    3. Built-in defaults (always available)
    """
    trust_config = config.trust_tiers
    rules = _resolve_rules(trust_config, config.security_surface)
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
        fp for fp, ft in result.file_tiers.items()
        if ft == pr_tier and ft != default_tier
    ]

    result.resolved_tier = pr_tier
    result.triggering_files = triggering

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


def _resolve_rules(
    trust_config: TrustTierConfig,
    surface_config: SecuritySurfaceConfig,
) -> list[tuple[TrustTier, list[str], str]]:
    """Resolve effective rules using the 3-layer fallback."""
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
