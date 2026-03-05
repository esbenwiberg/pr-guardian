from __future__ import annotations

from pr_guardian.models.context import BlastRadius, SecuritySurface


class DependencyGraph:
    """Pre-computed or config-declared dependency graph."""

    def __init__(self, edges: dict[str, set[str]] | None = None):
        # edges: file -> set of files that import/reference it
        self._consumers: dict[str, set[str]] = edges or {}

    def get_consumers(self, file_path: str) -> set[str]:
        return self._consumers.get(file_path, set())

    @classmethod
    def from_critical_consumers(cls, mapping: dict[str, list[str]]) -> DependencyGraph:
        """Build from review.yml critical_consumers config."""
        edges: dict[str, set[str]] = {}
        for source, consumers in mapping.items():
            edges[source] = set(consumers)
        return cls(edges)

    @classmethod
    def empty(cls) -> DependencyGraph:
        return cls()


def compute_blast_radius(
    changed_files: list[str],
    security_surface: SecuritySurface,
    dep_graph: DependencyGraph,
) -> BlastRadius:
    """For each changed file, find consumers and propagate security classifications."""
    result = BlastRadius(consumers={}, propagated_surface={})

    for file_path in changed_files:
        file_consumers = dep_graph.get_consumers(file_path)
        result.consumers[file_path] = file_consumers

        # Propagate: if consumer is security_critical, the changed file inherits risk
        propagated: set[str] = set()
        for consumer in file_consumers:
            classifications = security_surface.get_classifications(consumer)
            propagated.update(classifications)
        if propagated:
            result.propagated_surface[file_path] = propagated

    result.touches_shared_code = any(
        len(c) > 3 for c in result.consumers.values()
    )
    result.propagates_to_security = any(
        "security_critical" in cs
        for cs in result.propagated_surface.values()
    )
    result.propagates_to_api = any(
        "input_handling" in cs
        for cs in result.propagated_surface.values()
    )

    return result
