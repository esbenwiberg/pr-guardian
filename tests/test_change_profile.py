from pr_guardian.config.schema import FileRolesConfig
from pr_guardian.discovery.change_profile import build_change_profile
from pr_guardian.models.context import BlastRadius, FileRole, SecuritySurface
from pr_guardian.models.pr import Diff, DiffFile


class TestChangeProfile:
    def test_docs_only(self):
        diff = Diff(files=[DiffFile(path="README.md", status="modified")])
        profile = build_change_profile(
            ["README.md"], diff, SecuritySurface(), BlastRadius(), FileRolesConfig(),
        )
        assert profile.has_docs_only
        assert profile.skip_agents
        assert not profile.has_production_changes

    def test_production_code(self):
        diff = Diff(files=[DiffFile(path="src/handler.py", status="modified")])
        profile = build_change_profile(
            ["src/handler.py"], diff, SecuritySurface(), BlastRadius(), FileRolesConfig(),
        )
        assert profile.has_production_changes
        assert not profile.has_docs_only
        assert not profile.skip_agents

    def test_security_surface_triggers_agent(self):
        surface = SecuritySurface()
        surface.classify("src/auth/login.py", "security_critical")
        diff = Diff(files=[DiffFile(path="src/auth/login.py", status="modified")])
        profile = build_change_profile(
            ["src/auth/login.py"], diff, surface, BlastRadius(), FileRolesConfig(),
        )
        assert profile.touches_security_surface
        assert "security_privacy" in profile.implied_agents

    def test_api_boundary_triggers_agents(self):
        surface = SecuritySurface()
        surface.classify("src/api/users.py", "input_handling")
        diff = Diff(files=[DiffFile(path="src/api/users.py", status="modified")])
        profile = build_change_profile(
            ["src/api/users.py"], diff, surface, BlastRadius(), FileRolesConfig(),
        )
        assert profile.touches_api_boundary
        assert "security_privacy" in profile.implied_agents
        assert "performance" in profile.implied_agents

    def test_generated_only_skips_agents(self):
        diff = Diff(files=[DiffFile(path="migrations/001_auto.py", status="added")])
        profile = build_change_profile(
            ["migrations/001_auto.py"], diff, SecuritySurface(), BlastRadius(), FileRolesConfig(),
        )
        assert profile.has_generated_only
        assert profile.skip_agents
