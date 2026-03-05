from __future__ import annotations

from fnmatch import fnmatch

from pr_guardian.discovery.blast_radius import DependencyGraph


def build_dep_graph(
    critical_consumers: dict[str, list[str]] | None = None,
) -> DependencyGraph:
    """Build dependency graph from available sources.

    Priority:
    1. Config-declared critical_consumers (from review.yml)
    2. Pre-computed graph from DB (future)
    3. Empty fallback
    """
    if critical_consumers:
        # Expand glob patterns in consumer declarations
        return DependencyGraph.from_critical_consumers(critical_consumers)

    return DependencyGraph.empty()


def expand_consumer_globs(
    mapping: dict[str, list[str]],
    all_files: list[str],
) -> dict[str, list[str]]:
    """Expand glob patterns in critical_consumers to actual file paths."""
    expanded: dict[str, list[str]] = {}
    for source, consumer_patterns in mapping.items():
        consumers: list[str] = []
        for pattern in consumer_patterns:
            if "*" in pattern or "?" in pattern:
                consumers.extend(f for f in all_files if fnmatch(f, pattern))
            else:
                consumers.append(pattern)
        expanded[source] = consumers
    return expanded
