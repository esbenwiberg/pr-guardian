"""Pre-validator deduplication: remove duplicate findings across agents.

Multiple agents may flag the same root cause (same file, nearby lines, similar
description). This module runs *after* scoring but *before* the severity filter
and validator to remove obvious duplicates cheaply — no LLM call required.

Like the severity filter, this is display-level filtering: the decision and
combined_score are computed on the full finding set inside decide().
"""
from __future__ import annotations

from dataclasses import replace

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict

log = structlog.get_logger()

# Weights for deciding which duplicate to keep. Higher = preferred.
DEFAULT_AGENT_WEIGHTS = {
    "security_privacy": 3.0,
    "test_quality": 2.5,
    "architecture_intent": 2.0,
    "performance": 1.5,
    "hotspot": 1.5,
    "code_quality_observability": 1.0,
}

_SEVERITY_RANK = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

_CERTAINTY_RANK = {
    Certainty.UNCERTAIN: 0,
    Certainty.SUSPECTED: 1,
    Certainty.DETECTED: 2,
}

# Similarity thresholds
_DEFAULT_SIMILARITY_THRESHOLD = 0.5
_SAME_CATEGORY_THRESHOLD = 0.3
_LINE_PROXIMITY = 3


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens from text."""
    return {w.lower() for w in text.split() if len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _lines_near(a: int | None, b: int | None) -> bool:
    """Check if two line numbers are within proximity range."""
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _LINE_PROXIMITY


def _is_better(
    candidate: tuple[str, Finding],
    current: tuple[str, Finding],
) -> bool:
    """Return True if candidate should replace current as the kept finding."""
    c_agent, c_finding = candidate
    k_agent, k_finding = current

    c_weight = DEFAULT_AGENT_WEIGHTS.get(c_agent, 1.0)
    k_weight = DEFAULT_AGENT_WEIGHTS.get(k_agent, 1.0)
    if c_weight != k_weight:
        return c_weight > k_weight

    c_sev = _SEVERITY_RANK.get(c_finding.severity, 0)
    k_sev = _SEVERITY_RANK.get(k_finding.severity, 0)
    if c_sev != k_sev:
        return c_sev > k_sev

    c_cert = _CERTAINTY_RANK.get(c_finding.certainty, 0)
    k_cert = _CERTAINTY_RANK.get(k_finding.certainty, 0)
    return c_cert > k_cert


def deduplicate_findings(
    agent_results: list[AgentResult],
    config: GuardianConfig,
) -> tuple[list[AgentResult], int]:
    """Remove duplicate findings across different agents.

    Findings from the same file with nearby lines and similar descriptions
    are considered duplicates. The finding from the higher-weighted agent is
    kept; ties are broken by severity then certainty.

    Only deduplicates across agents — not within the same agent.

    Returns:
        (deduplicated_agent_results, total_removed_count)

    The returned AgentResult objects are copies — originals are not mutated.
    """
    # Flatten all findings: (agent_name, local_index, finding)
    flat: list[tuple[str, int, Finding]] = []
    for result in agent_results:
        for i, finding in enumerate(result.findings):
            flat.append((result.agent_name, i, finding))

    if len(flat) <= 1:
        return agent_results, 0

    # Group by file path for efficient pairwise comparison
    by_file: dict[str, list[int]] = {}
    for global_idx, (_, _, finding) in enumerate(flat):
        by_file.setdefault(finding.file, []).append(global_idx)

    # Track which global indices to remove
    remove_indices: set[int] = set()

    for file_path, indices in by_file.items():
        for i_pos, gi in enumerate(indices):
            if gi in remove_indices:
                continue
            for gj in indices[i_pos + 1:]:
                if gj in remove_indices:
                    continue

                a_agent, a_local, a_finding = flat[gi]
                b_agent, b_local, b_finding = flat[gj]

                # Skip same-agent findings
                if a_agent == b_agent:
                    continue

                # Check line proximity
                if not _lines_near(a_finding.line, b_finding.line):
                    continue

                # Check description similarity
                a_tokens = _tokenize(a_finding.description)
                b_tokens = _tokenize(b_finding.description)
                similarity = _jaccard(a_tokens, b_tokens)

                threshold = _DEFAULT_SIMILARITY_THRESHOLD
                if a_finding.category == b_finding.category and a_finding.category:
                    threshold = _SAME_CATEGORY_THRESHOLD

                if similarity < threshold:
                    continue

                # Duplicate found — remove the weaker one
                if _is_better((b_agent, b_finding), (a_agent, a_finding)):
                    remove_indices.add(gi)
                    break  # gi is removed, no more comparisons for it
                else:
                    remove_indices.add(gj)

    if not remove_indices:
        return agent_results, 0

    # Map global_index → (agent_name, local_index) for removals
    remove_keys: set[tuple[str, int]] = set()
    for gi in remove_indices:
        agent_name, local_idx, _ = flat[gi]
        remove_keys.add((agent_name, local_idx))

    # Build new agent results
    new_results: list[AgentResult] = []
    for result in agent_results:
        new_findings = [
            f for i, f in enumerate(result.findings)
            if (result.agent_name, i) not in remove_keys
        ]
        new_result = replace(
            result,
            findings=new_findings,
            verdict=(
                Verdict.PASS
                if not new_findings
                and result.findings
                and result.verdict != Verdict.FLAG_HUMAN
                else result.verdict
            ),
        )
        new_results.append(new_result)

    total_removed = len(remove_indices)
    log.info("dedup_applied", removed=total_removed)
    return new_results, total_removed
