"""Cross-agent finding deduplication.

Provides heuristic pre-grouping of potentially duplicate findings across agents,
and a merge function that consolidates duplicates while preserving attribution.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace, field

from pr_guardian.models.findings import (
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
)

# Severity/certainty ordering for max-promotion during merge
_SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
_CERTAINTY_ORDER = [Certainty.UNCERTAIN, Certainty.SUSPECTED, Certainty.DETECTED]


def _tokenize_category(category: str) -> set[str]:
    """Normalize and tokenize a category string for overlap comparison."""
    normalized = re.sub(r"[-_/.]", " ", category.lower()).strip()
    return {t for t in normalized.split() if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_potential_duplicates(
    flat: list[tuple[str, int, Finding]],
    line_threshold: int = 5,
    category_threshold: float = 0.3,
) -> dict[int, int]:
    """Group findings that are likely duplicates based on file proximity + category overlap.

    Args:
        flat: Flattened findings as (agent_name, local_index, Finding) tuples.
        line_threshold: Max line distance to consider findings "nearby".
        category_threshold: Min Jaccard similarity for category tokens.

    Returns:
        Mapping of global_index -> cluster_id (only includes findings that belong
        to a cluster of 2+). Singletons are omitted.
    """
    # Group by file
    by_file: dict[str, list[int]] = defaultdict(list)
    for global_idx, (_, _, finding) in enumerate(flat):
        by_file[finding.file].append(global_idx)

    clusters: dict[int, int] = {}
    cluster_id = 0

    for file_indices in by_file.values():
        if len(file_indices) < 2:
            continue

        # Build adjacency: which findings in this file are "similar enough"
        # to be potential duplicates
        n = len(file_indices)
        visited = [False] * n

        for i in range(n):
            if visited[i]:
                continue

            idx_i = file_indices[i]
            finding_i = flat[idx_i][2]
            tokens_i = _tokenize_category(finding_i.category)

            group = [i]
            visited[i] = True

            for j in range(i + 1, n):
                if visited[j]:
                    continue

                idx_j = file_indices[j]
                finding_j = flat[idx_j][2]

                # Check line proximity
                lines_close = _lines_are_close(
                    finding_i.line, finding_j.line, line_threshold,
                )
                if not lines_close:
                    continue

                # Check category overlap
                tokens_j = _tokenize_category(finding_j.category)
                if _jaccard(tokens_i, tokens_j) >= category_threshold:
                    group.append(j)
                    visited[j] = True

            if len(group) >= 2:
                for member in group:
                    clusters[file_indices[member]] = cluster_id
                cluster_id += 1

    return clusters


def _lines_are_close(
    line_a: int | None, line_b: int | None, threshold: int,
) -> bool:
    """Check if two line numbers are within threshold of each other.

    If either line is None, we're conservative: only match if both are None
    (meaning both agents couldn't pinpoint a line — likely file-level findings).
    """
    if line_a is None and line_b is None:
        return True
    if line_a is None or line_b is None:
        return False
    return abs(line_a - line_b) <= threshold


def merge_findings(
    keeper_agent: str,
    keeper: Finding,
    merged: list[tuple[str, Finding]],
    agent_weights: dict[str, float] | None = None,
) -> Finding:
    """Merge duplicate findings into a single finding with attribution.

    The keeper is the finding the validator chose as the best representative.
    Merged findings contribute their severity/certainty/evidence but are otherwise
    absorbed into the keeper.

    Args:
        keeper_agent: Agent name of the keeper finding.
        keeper: The finding chosen as representative by the validator.
        merged: List of (agent_name, finding) pairs being merged into keeper.
        agent_weights: Optional agent weight mapping for attribution context.

    Returns:
        A new Finding with merged attributes and cross-agent attribution.
    """
    all_findings = [(keeper_agent, keeper)] + merged

    # Promote severity to max across all contributors
    max_severity = max(
        (f.severity for _, f in all_findings),
        key=lambda s: _SEVERITY_ORDER.index(s),
    )

    # Promote certainty to max across all contributors
    max_certainty = max(
        (f.certainty for _, f in all_findings),
        key=lambda c: _CERTAINTY_ORDER.index(c),
    )

    # OR-merge evidence basis booleans, max on cross_references
    merged_evidence = EvidenceBasis(
        saw_full_context=any(f.evidence_basis.saw_full_context for _, f in all_findings),
        pattern_match=any(f.evidence_basis.pattern_match for _, f in all_findings),
        cwe_id=next(
            (f.evidence_basis.cwe_id for _, f in all_findings if f.evidence_basis.cwe_id),
            None,
        ),
        similar_code_in_repo=any(
            f.evidence_basis.similar_code_in_repo for _, f in all_findings
        ),
        suggestion_is_concrete=any(
            f.evidence_basis.suggestion_is_concrete for _, f in all_findings
        ),
        cross_references=max(
            f.evidence_basis.cross_references for _, f in all_findings
        ),
    )

    # Collect unique CWE references
    cwes = {f.cwe for _, f in all_findings if f.cwe}
    merged_cwe = ", ".join(sorted(cwes)) if cwes else keeper.cwe

    # Build contributing agents list
    contributing = [
        {
            "agent_name": agent_name,
            "severity": f.severity.value,
            "certainty": f.certainty.value,
            "description": f.description[:300],
        }
        for agent_name, f in all_findings
    ]

    return replace(
        keeper,
        severity=max_severity,
        certainty=max_certainty,
        evidence_basis=merged_evidence,
        cwe=merged_cwe,
        primary_agent=keeper_agent,
        contributing_agents=contributing,
        merged_from_count=len(all_findings),
    )
