from pr_guardian.discovery.blast_radius import DependencyGraph, compute_blast_radius
from pr_guardian.models.context import SecuritySurface


class TestBlastRadius:
    def test_isolated_file_has_no_shared_or_security_blast_radius(self):
        result = compute_blast_radius(["src/utils.py"], SecuritySurface(), DependencyGraph.empty())
        assert result.touches_shared_code is False
        assert result.propagates_to_security is False

    def test_shared_file_propagates_security_surface_from_critical_consumer(self):
        surface = SecuritySurface()
        surface.classify("src/middleware/auth.ts", "security_critical")

        graph = DependencyGraph.from_critical_consumers(
            {
                "src/shared/validate.ts": ["src/middleware/auth.ts"],
            }
        )

        result = compute_blast_radius(
            ["src/shared/validate.ts"],
            surface,
            graph,
        )
        assert result.propagates_to_security is True
        assert "security_critical" in result.propagated_surface["src/shared/validate.ts"]

    def test_file_with_more_than_three_consumers_is_shared_code(self):
        graph = DependencyGraph(
            {
                "lib/utils.py": {f"consumer_{i}.py" for i in range(5)},
            }
        )
        result = compute_blast_radius(
            ["lib/utils.py"],
            SecuritySurface(),
            graph,
        )
        assert result.touches_shared_code is True

    def test_file_with_exactly_three_consumers_is_not_shared_code(self):
        graph = DependencyGraph(
            {
                "lib/utils.py": {f"consumer_{i}.py" for i in range(3)},
            }
        )
        result = compute_blast_radius(
            ["lib/utils.py"],
            SecuritySurface(),
            graph,
        )
        assert result.touches_shared_code is False

    def test_unrelated_file_does_not_inherit_surface_from_graph(self):
        surface = SecuritySurface()
        surface.classify("src/controllers/payment.ts", "input_handling")
        graph = DependencyGraph.from_critical_consumers(
            {
                "src/utils/format.ts": ["src/controllers/payment.ts"],
            }
        )

        result = compute_blast_radius(
            ["src/utils/unrelated.ts"],
            surface,
            graph,
        )

        assert result.propagates_to_api is False
        assert result.propagated_surface == {}

    def test_shared_file_propagates_api_surface_from_input_handling_consumer(self):
        surface = SecuritySurface()
        surface.classify("src/controllers/payment.ts", "input_handling")

        graph = DependencyGraph.from_critical_consumers(
            {
                "src/utils/format.ts": ["src/controllers/payment.ts"],
            }
        )

        result = compute_blast_radius(
            ["src/utils/format.ts"],
            surface,
            graph,
        )
        assert result.propagates_to_api is True
