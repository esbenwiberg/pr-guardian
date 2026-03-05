from pr_guardian.discovery.blast_radius import DependencyGraph, compute_blast_radius
from pr_guardian.models.context import SecuritySurface


class TestBlastRadius:
    def test_no_consumers(self):
        result = compute_blast_radius(
            ["src/utils.py"], SecuritySurface(), DependencyGraph.empty()
        )
        assert not result.touches_shared_code
        assert not result.propagates_to_security

    def test_security_propagation(self):
        surface = SecuritySurface()
        surface.classify("src/middleware/auth.ts", "security_critical")

        graph = DependencyGraph.from_critical_consumers({
            "src/shared/validate.ts": ["src/middleware/auth.ts"],
        })

        result = compute_blast_radius(
            ["src/shared/validate.ts"], surface, graph,
        )
        assert result.propagates_to_security
        assert "security_critical" in result.propagated_surface["src/shared/validate.ts"]

    def test_shared_code_threshold(self):
        graph = DependencyGraph({
            "lib/utils.py": {f"consumer_{i}.py" for i in range(5)},
        })
        result = compute_blast_radius(
            ["lib/utils.py"], SecuritySurface(), graph,
        )
        assert result.touches_shared_code  # >3 consumers

    def test_api_propagation(self):
        surface = SecuritySurface()
        surface.classify("src/controllers/payment.ts", "input_handling")

        graph = DependencyGraph.from_critical_consumers({
            "src/utils/format.ts": ["src/controllers/payment.ts"],
        })

        result = compute_blast_radius(
            ["src/utils/format.ts"], surface, graph,
        )
        assert result.propagates_to_api
