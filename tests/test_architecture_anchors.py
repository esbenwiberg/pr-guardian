"""Tests for architecture anchor discovery (Brief 04).

Required fact coverage:
  fact-architecture-anchor-modes   → anchor_modes tests
  fact-architecture-path-scoped    → path_scoped tests
"""
from __future__ import annotations

import pytest

from pr_guardian.agents.architecture_anchors import (
    ArchitectureAnchor,
    ArchitectureAnchorSet,
    _compute_mode,
    _has_architecture_content,
    _infer_scope_glob,
    discover_architecture_anchors,
)
from pr_guardian.config.schema import ArchitectureConfig, GuardianConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(
    mode_override: str = "auto",
    architecture_docs: list[str] | None = None,
    path_scopes: dict | None = None,
) -> GuardianConfig:
    cfg = GuardianConfig()
    cfg.architecture = ArchitectureConfig(
        mode_override=mode_override,
        path_scopes=path_scopes or {},
    )
    cfg.architecture_docs = architecture_docs or []
    return cfg


def _anchor(rank: int, anchor_class: str = "rule") -> ArchitectureAnchor:
    return ArchitectureAnchor(
        path=f"docs/anchor-rank{rank}.md",
        rank=rank,
        weight=1.0,
        anchor_class=anchor_class,
        content="content",
        scope_glob=None,
    )


class FetchRouter:
    """Adapter stub that returns different content per file path."""

    def __init__(self, files: dict[str, str]):
        self._files = files
        self.fetched: list[str] = []
        self.listed: list[str] = []

    async def fetch_file_content(self, repo: str, path: str, ref: str = "HEAD") -> str:
        self.fetched.append(path)
        content = self._files.get(path)
        if content is None:
            raise FileNotFoundError(f"not found: {path}")
        return content

    async def list_repo_files(
        self, repo: str, ref: str = "HEAD", path: str = ""
    ) -> list[str]:
        self.listed.append(path)
        # Return filenames that are under the requested directory
        return [
            p for p in self._files if path and p.startswith(path + "/")
        ]


class SkipAdapter:
    """Adapter that returns nothing for every request."""

    async def fetch_file_content(self, repo, path, ref="HEAD"):
        raise FileNotFoundError(f"not found: {path}")

    async def list_repo_files(self, repo, ref="HEAD", path=""):
        return []


# ---------------------------------------------------------------------------
# _compute_mode unit tests
# ---------------------------------------------------------------------------

class TestComputeMode:
    def test_empty_anchors_is_skip(self):
        assert _compute_mode([]) == "skip"

    def test_rank1_is_full_verifier(self):
        assert _compute_mode([_anchor(1)]) == "full_verifier"

    def test_rank2_is_full_verifier(self):
        assert _compute_mode([_anchor(2)]) == "full_verifier"

    def test_rank3_is_full_verifier(self):
        assert _compute_mode([_anchor(3)]) == "full_verifier"

    def test_rank4_alone_is_narrow(self):
        # rank 4 without rank 7+ corroboration → narrow_local_pattern
        assert _compute_mode([_anchor(4, "rule")]) == "narrow_local_pattern"

    def test_rank5_alone_is_narrow(self):
        assert _compute_mode([_anchor(5)]) == "narrow_local_pattern"

    def test_rank4_plus_rank7_is_full_verifier(self):
        # rank 4-5 with rank 7+ corroboration → full_verifier
        anchors = [_anchor(4, "rule"), _anchor(7, "convention")]
        assert _compute_mode(anchors) == "full_verifier"

    def test_rank5_plus_rank8_is_full_verifier(self):
        anchors = [_anchor(5), _anchor(8)]
        assert _compute_mode(anchors) == "full_verifier"

    def test_rank7_alone_is_narrow(self):
        assert _compute_mode([_anchor(7)]) == "narrow_local_pattern"

    def test_rank8_alone_is_narrow(self):
        assert _compute_mode([_anchor(8)]) == "narrow_local_pattern"

    def test_rank10_alone_is_narrow(self):
        assert _compute_mode([_anchor(10)]) == "narrow_local_pattern"

    def test_rank11_alone_is_skip(self):
        # Only sibling-file signal → skip
        assert _compute_mode([_anchor(11)]) == "skip"


# ---------------------------------------------------------------------------
# anchor_modes — fact-architecture-anchor-modes
# ---------------------------------------------------------------------------

class TestAnchorModes:
    """Integration tests: discover_architecture_anchors picks the right mode."""

    @pytest.mark.asyncio
    async def test_anchor_modes_explicit_architecture_docs_is_full_verifier(self):
        """Explicit architecture_docs in config → full_verifier regardless of content."""
        adapter = FetchRouter({"docs/arch.md": "# Architecture\nAll services must ..."})
        cfg = _config(architecture_docs=["docs/arch.md"])
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=cfg,
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "full_verifier"
        assert "docs/arch.md" in adapter.fetched

    @pytest.mark.asyncio
    async def test_anchor_modes_imperative_architecture_md_is_full_verifier(self):
        """ARCHITECTURE.md with imperative voice → rank 4 rule → full_verifier when corroborated."""
        # rank 4 (ARCHITECTURE.md imperative) + rank 7 (AGENTS.md with arch heading) → full_verifier
        adapter = FetchRouter({
            "ARCHITECTURE.md": "All services must use the repository pattern.",
            "AGENTS.md": "# Architecture\n- All imports must go through the facade.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "full_verifier"

    @pytest.mark.asyncio
    async def test_anchor_modes_agents_md_architecture_section_is_narrow(self):
        """AGENTS.md with architecture heading only → rank 7 → narrow_local_pattern."""
        adapter = FetchRouter({
            "AGENTS.md": (
                "# Architecture\n\n"
                "Keep services in the services/ directory.\n"
                "Avoid circular imports."
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "narrow_local_pattern"

    @pytest.mark.asyncio
    async def test_anchor_modes_agents_md_no_arch_content_is_skip(self):
        """AGENTS.md with only build instructions → filtered out → skip."""
        adapter = FetchRouter({
            "AGENTS.md": (
                "# Build\n\nRun `make test` to run tests.\n"
                "Run `make build` to build."
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "skip"

    @pytest.mark.asyncio
    async def test_anchor_modes_adr_accepted_is_full_verifier(self):
        """Accepted ADR → rank 3 → full_verifier."""
        adapter = FetchRouter({
            "docs/adr/0001-use-cqrs.md": (
                "# ADR-001 Use CQRS\n\nStatus: Accepted\n\nAll writes go through commands."
            ),
        })

        class AdrAdapter(FetchRouter):
            async def list_repo_files(self, repo, ref="HEAD", path=""):
                if path == "docs/adr":
                    return ["docs/adr/0001-use-cqrs.md"]
                return []

        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=AdrAdapter(adapter._files),
            repo="org/repo",
        )
        assert result.mode == "full_verifier"

    @pytest.mark.asyncio
    async def test_anchor_modes_rejected_adr_is_not_anchor(self):
        """Rejected/Superseded ADRs must not contribute anchors — they document
        decisions the team explicitly chose NOT to follow."""
        files = {
            "docs/adr/0001-rejected.md": (
                "# ADR-001 Use Microservices\n\nStatus: Rejected\n\n"
                "All services must be deployed independently."
            ),
        }

        class AdrAdapter(FetchRouter):
            async def list_repo_files(self, repo, ref="HEAD", path=""):
                if path == "docs/adr":
                    return ["docs/adr/0001-rejected.md"]
                return []

        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=AdrAdapter(files),
            repo="org/repo",
        )
        # Rejected ADR alone → no anchors → skip
        assert result.mode == "skip"
        assert result.status_reason == "no architecture context found"

    @pytest.mark.asyncio
    async def test_anchor_modes_superseded_adr_is_not_anchor(self):
        """Superseded ADRs are excluded; only the surviving decision should anchor."""
        files = {
            "docs/adr/0001-old.md": (
                "# ADR-001 Use REST\n\nStatus: Superseded by ADR-002\n\n"
                "Endpoints must be RESTful."
            ),
            "docs/adr/0002-new.md": (
                "# ADR-002 Use GraphQL\n\nStatus: Accepted\n\n"
                "All new APIs are GraphQL."
            ),
        }

        class AdrAdapter(FetchRouter):
            async def list_repo_files(self, repo, ref="HEAD", path=""):
                if path == "docs/adr":
                    return ["docs/adr/0001-old.md", "docs/adr/0002-new.md"]
                return []

        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=AdrAdapter(files),
            repo="org/repo",
        )
        # Only the accepted ADR contributes → still full_verifier, but only one anchor
        assert result.mode == "full_verifier"
        anchors = result.anchors_by_path["src/service.py"]
        adr_paths = {a.path for a in anchors}
        assert "docs/adr/0002-new.md" in adr_paths
        assert "docs/adr/0001-old.md" not in adr_paths

    @pytest.mark.asyncio
    async def test_anchor_modes_dep_cruiser_is_full_verifier(self):
        """dependency-cruiser config with forbidden rules → rank 2 → full_verifier."""
        adapter = FetchRouter({
            ".dependency-cruiser.js": (
                "module.exports = { forbidden: [{ name: 'no-circular', ... }] };"
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "full_verifier"

    @pytest.mark.asyncio
    async def test_anchor_modes_no_anchors_is_skip(self):
        """No anchor files at all → skip."""
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=SkipAdapter(),
            repo="org/repo",
        )
        assert result.mode == "skip"
        assert result.status_reason == "no architecture context found"

    @pytest.mark.asyncio
    async def test_anchor_modes_mode_override_skip(self):
        """mode_override=skip bypasses discovery entirely."""
        # Adapter would return ARCHITECTURE.md but the override wins.
        adapter = FetchRouter({"ARCHITECTURE.md": "All services must follow rules."})
        cfg = _config(mode_override="skip")
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=cfg,
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "skip"
        assert "ARCHITECTURE.md" not in adapter.fetched

    @pytest.mark.asyncio
    async def test_anchor_modes_mode_override_narrow(self):
        """mode_override=narrow_local_pattern ignores discovery."""
        cfg = _config(mode_override="narrow_local_pattern")
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=cfg,
            adapter=SkipAdapter(),
            repo="org/repo",
        )
        assert result.mode == "narrow_local_pattern"

    @pytest.mark.asyncio
    async def test_anchor_modes_mode_override_full_verifier(self):
        """mode_override=full_verifier ignores discovery."""
        cfg = _config(mode_override="full_verifier")
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=cfg,
            adapter=SkipAdapter(),
            repo="org/repo",
        )
        assert result.mode == "full_verifier"

    @pytest.mark.asyncio
    async def test_anchor_modes_no_adapter_is_skip(self):
        """Without an adapter no I/O can happen → skip."""
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=None,
            repo="org/repo",
        )
        assert result.mode == "skip"

    @pytest.mark.asyncio
    async def test_anchor_modes_contributing_arch_section_is_narrow(self):
        """CONTRIBUTING.md with an architecture section → rank 8 → narrow_local_pattern."""
        adapter = FetchRouter({
            "CONTRIBUTING.md": (
                "# How to Contribute\n\n"
                "Run tests with `pytest`.\n\n"
                "## Code Structure\n\n"
                "All business logic lives in `domain/`. "
                "Infrastructure adapters live in `infra/`.\n"
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "narrow_local_pattern"

    @pytest.mark.asyncio
    async def test_anchor_modes_contributing_no_arch_section_is_skip(self):
        """CONTRIBUTING.md with only test/build instructions → skip."""
        adapter = FetchRouter({
            "CONTRIBUTING.md": (
                "# How to Contribute\n\n"
                "## Running Tests\n\nRun `pytest`.\n\n"
                "## Build\n\nRun `make build`."
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "skip"

    @pytest.mark.asyncio
    async def test_anchor_modes_cursorrules_without_arch_is_skip(self):
        """.cursorrules with no architecture content → filtered → skip."""
        adapter = FetchRouter({
            ".cursorrules": "Use TypeScript. Prefer functional style. Keep files small.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "skip"

    @pytest.mark.asyncio
    async def test_anchor_modes_cursorrules_with_arch_is_narrow(self):
        """.cursorrules with architecture/layer content → rank 9 → narrow_local_pattern."""
        adapter = FetchRouter({
            ".cursorrules": (
                "# Layer conventions\n"
                "- Never import from infrastructure in domain code.\n"
                "- Always inject dependencies through the constructor."
            ),
        })
        result = await discover_architecture_anchors(
            changed_paths=["src/service.py"],
            config=_config(),
            adapter=adapter,
            repo="org/repo",
        )
        assert result.mode == "narrow_local_pattern"


# ---------------------------------------------------------------------------
# _has_architecture_content unit tests
# ---------------------------------------------------------------------------

class TestHasArchitectureContent:
    def test_arch_heading_detected(self):
        assert _has_architecture_content("# Architecture\n\nSome notes.")

    def test_layer_heading_detected(self):
        assert _has_architecture_content("## Layer boundaries")

    def test_module_heading_detected(self):
        assert _has_architecture_content("## Module Organization")

    def test_bullet_rule_must_detected(self):
        assert _has_architecture_content("- Must use the repository pattern.")

    def test_bullet_rule_never_detected(self):
        assert _has_architecture_content("* Never import from infrastructure.")

    def test_bullet_dont_detected(self):
        assert _has_architecture_content("- Don't mix concerns across layers.")

    def test_build_instructions_not_arch(self):
        assert not _has_architecture_content(
            "# Build\n\nRun `make test`.\nRun `make build`."
        )

    def test_empty_content_not_arch(self):
        assert not _has_architecture_content("")


# ---------------------------------------------------------------------------
# _infer_scope_glob unit tests
# ---------------------------------------------------------------------------

class TestInferScopeGlob:
    def test_root_file_is_global(self):
        assert _infer_scope_glob("ARCHITECTURE.md") is None

    def test_subdirectory_file_scoped(self):
        assert _infer_scope_glob("packages/api/ARCHITECTURE.md") == "packages/api/**"

    def test_deeply_nested_file_scoped(self):
        assert _infer_scope_glob("a/b/c/CONVENTIONS.md") == "a/b/c/**"


# ---------------------------------------------------------------------------
# path_scoped — fact-architecture-path-scoped
# ---------------------------------------------------------------------------

class TestPathScoped:
    """Tests for per-path anchor scoping in monorepo setups."""

    @pytest.mark.asyncio
    async def test_path_scoped_anchor_applies_only_to_matching_subtree(self):
        """An anchor under packages/api/ only applies to files under packages/api/."""
        adapter = FetchRouter({
            "packages/api/ARCHITECTURE.md": "# Architecture\nAll services must follow rules.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["packages/api/service.py", "packages/web/index.ts"],
            config=_config(architecture_docs=["packages/api/ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        api_anchors = result.anchors_by_path.get("packages/api/service.py", [])
        web_anchors = result.anchors_by_path.get("packages/web/index.ts", [])

        assert len(api_anchors) == 1
        assert api_anchors[0].path == "packages/api/ARCHITECTURE.md"
        assert len(web_anchors) == 0

    @pytest.mark.asyncio
    async def test_path_scoped_global_anchor_applies_to_all_paths(self):
        """Root-level ARCHITECTURE.md applies to all changed files."""
        adapter = FetchRouter({
            "ARCHITECTURE.md": "All services must follow rules.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["packages/api/service.py", "packages/web/index.ts"],
            config=_config(architecture_docs=["ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        api_anchors = result.anchors_by_path.get("packages/api/service.py", [])
        web_anchors = result.anchors_by_path.get("packages/web/index.ts", [])

        assert len(api_anchors) == 1
        assert len(web_anchors) == 1

    @pytest.mark.asyncio
    async def test_path_scoped_unmatched_path_gets_empty_anchor_list(self):
        """A changed file under tools/ with no applicable anchor → empty list."""
        adapter = FetchRouter({
            "packages/api/ARCHITECTURE.md": "# Architecture\nAll services must follow rules.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["packages/api/service.py", "tools/build.sh"],
            config=_config(architecture_docs=["packages/api/ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        tools_anchors = result.anchors_by_path.get("tools/build.sh", [])
        assert tools_anchors == []

    @pytest.mark.asyncio
    async def test_path_scoped_unmatched_does_not_disable_matched(self):
        """tools/ having no anchors does not prevent packages/api/ from running."""
        adapter = FetchRouter({
            "packages/api/ARCHITECTURE.md": "# Architecture\nAll services must follow rules.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["packages/api/service.py", "tools/build.sh"],
            config=_config(architecture_docs=["packages/api/ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        # Overall mode is still full_verifier because packages/api/ has anchors
        assert result.mode == "full_verifier"

    @pytest.mark.asyncio
    async def test_path_scoped_config_path_scopes_respected(self):
        """Config path_scopes maps a changed-file pattern to explicit anchor files.

        The anchor file is at a non-standard location so it is only loaded via the
        path_scopes config (not picked up by Stage 1 / Stage 2 auto-discovery).
        """
        # Use a non-standard path that auto-discovery will never check:
        # not in _STAGE1_FILES and not under any _ADR_DIRS.
        anchor_path = "apps/api/arch-guide.md"
        adapter = FetchRouter({
            anchor_path: "All reads must go through repositories.",
        })
        cfg = _config(
            path_scopes={"apps/api/**": [anchor_path]},
        )
        result = await discover_architecture_anchors(
            changed_paths=["apps/api/service.py", "apps/web/index.ts"],
            config=cfg,
            adapter=adapter,
            repo="org/repo",
        )
        api_anchors = result.anchors_by_path.get("apps/api/service.py", [])
        web_anchors = result.anchors_by_path.get("apps/web/index.ts", [])

        # apps/api/ matches the path_scope pattern → gets the anchor
        assert any(a.path == anchor_path for a in api_anchors)
        # apps/web/ does not match → no anchor from that scope
        assert not any(a.path == anchor_path for a in web_anchors)

    @pytest.mark.asyncio
    async def test_path_scoped_two_subtrees_independent_anchors(self):
        """Two subtrees with different anchors: each path gets only its own."""
        adapter = FetchRouter({
            "apps/api/ARCHITECTURE.md": "All services must follow rules.",
            "apps/web/CONVENTIONS.md": "Components use hooks only.",
        })
        cfg = _config(
            architecture_docs=["apps/api/ARCHITECTURE.md", "apps/web/CONVENTIONS.md"]
        )
        result = await discover_architecture_anchors(
            changed_paths=["apps/api/handler.py", "apps/web/Button.tsx"],
            config=cfg,
            adapter=adapter,
            repo="org/repo",
        )
        api_anchors = result.anchors_by_path.get("apps/api/handler.py", [])
        web_anchors = result.anchors_by_path.get("apps/web/Button.tsx", [])

        api_paths = {a.path for a in api_anchors}
        web_paths = {a.path for a in web_anchors}

        assert "apps/api/ARCHITECTURE.md" in api_paths
        assert "apps/web/CONVENTIONS.md" not in api_paths
        assert "apps/web/CONVENTIONS.md" in web_paths
        assert "apps/api/ARCHITECTURE.md" not in web_paths

    @pytest.mark.asyncio
    async def test_path_scoped_all_paths_unmatched_is_skip(self):
        """All changed files under tools/ with no anchor → skip mode."""
        adapter = FetchRouter({
            "packages/api/ARCHITECTURE.md": "All services must follow rules.",
        })
        result = await discover_architecture_anchors(
            changed_paths=["tools/build.sh", "tools/lint.sh"],
            config=_config(architecture_docs=["packages/api/ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        # Neither tools/ file matches packages/api/** → all path anchor lists empty
        assert all(anchors == [] for anchors in result.anchors_by_path.values())
        assert result.mode == "skip"

    @pytest.mark.asyncio
    async def test_path_scoped_anchors_by_path_keys_match_changed_paths(self):
        """anchors_by_path contains exactly the changed paths as keys."""
        adapter = FetchRouter({"ARCHITECTURE.md": "Rules must be followed."})
        result = await discover_architecture_anchors(
            changed_paths=["src/a.py", "src/b.py", "src/c.py"],
            config=_config(architecture_docs=["ARCHITECTURE.md"]),
            adapter=adapter,
            repo="org/repo",
        )
        assert set(result.anchors_by_path.keys()) == {"src/a.py", "src/b.py", "src/c.py"}
