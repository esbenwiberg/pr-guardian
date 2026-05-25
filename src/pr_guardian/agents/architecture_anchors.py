"""Architecture anchor discovery — cheapest-first rank/mode selection.

Walks candidate anchor files in rank order (cheapest fetches first), classifies
each as rule/convention/structural, then selects one of three modes:

    full_verifier      — rank 1-3 present, or rank 4-5 + rank 7+ corroboration
    narrow_local_pattern — only rank 7-10 present
    skip               — only rank 11 (sibling) or nothing
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

from pr_guardian.config.schema import GuardianConfig

log = structlog.get_logger()

# Heading pattern that marks architecture-relevant sections in AGENTS.md / CLAUDE.md.
# No trailing \b so that "architect" matches inside "Architecture".
_ARCH_HEADING_RE = re.compile(
    r"^#{1,4}[^\n]*\b(architect|layer|module|boundar|conventions?|structure|organization)",
    re.IGNORECASE | re.MULTILINE,
)
# Bullet "do/don't" rules — strong signal for convention content
_BULLET_RULE_RE = re.compile(
    r"^\s*[-*+]\s+(do\s+not|don'?t|must\s+not|must|never|always|avoid)\b",
    re.IGNORECASE | re.MULTILINE,
)
# ADR "Status: Accepted" marker
_ADR_ACCEPTED_RE = re.compile(r"status\s*:\s*accepted", re.IGNORECASE)
# dependency-cruiser meaningful rule content
_DEP_CRUISER_RULE_RE = re.compile(r"\b(forbidden|allowed)\b", re.IGNORECASE)

# Top-level marker files: (path, rank, weight, anchor_class)
_STAGE1_FILES: list[tuple[str, int, float, str]] = [
    ("ARCHITECTURE.md", 4, 0.7, "rule"),
    ("CONVENTIONS.md", 5, 0.7, "convention"),
    ("docs/conventions/index.md", 5, 0.7, "convention"),
    ("AGENTS.md", 7, 0.5, "convention"),
    ("CLAUDE.md", 7, 0.5, "convention"),
    ("CONTRIBUTING.md", 8, 0.4, "convention"),
    ("CONTRIBUTING.adoc", 8, 0.4, "convention"),
    (".cursorrules", 9, 0.3, "convention"),
    (".github/copilot-instructions.md", 9, 0.3, "convention"),
]

_NEEDS_ARCH_FILTER = {"AGENTS.md", "CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md"}
_CONTRIBUTING_PREFIXES = {"CONTRIBUTING.md", "CONTRIBUTING.adoc"}

_ADR_DIRS = [
    "docs/adr",
    "docs/architecture/decisions",
    "doc/adr",
    "adr",
    "architecture/decisions",
]

_DEP_CRUISER_PATHS = [
    ".dependency-cruiser.js",
    ".dependency-cruiser.cjs",
    ".dependency-cruiser.json",
    "dependency-cruiser.config.js",
]

_MODE_RANK = {"full_verifier": 2, "narrow_local_pattern": 1, "skip": 0}


@dataclass
class ArchitectureAnchor:
    """A single discovered architecture anchor."""

    path: str
    rank: int
    weight: float
    anchor_class: Literal["rule", "convention", "structural"]
    content: str
    # None = global; "packages/api/**" = scoped to that subtree
    scope_glob: str | None = None


@dataclass
class ArchitectureAnchorSet:
    """Full result of architecture anchor discovery for one review."""

    mode: Literal["full_verifier", "narrow_local_pattern", "skip"]
    anchors_by_path: dict[str, list[ArchitectureAnchor]] = field(default_factory=dict)
    status_reason: str | None = None


# ---------------------------------------------------------------------------
# Content classifiers
# ---------------------------------------------------------------------------

def _has_architecture_content(content: str) -> bool:
    """Return True if a file contains architecture-relevant sections."""
    return bool(_ARCH_HEADING_RE.search(content) or _BULLET_RULE_RE.search(content))


def _extract_architecture_sections(content: str) -> str:
    """Extract architecture-relevant sections from CONTRIBUTING.md."""
    # No trailing \b so "architect" matches inside "Architecture".
    arch_re = re.compile(
        r"\b(architect|layer|module|boundar|conventions?|structure|organization"
        r"|code organization|project structure)",
        re.IGNORECASE,
    )
    sections: list[str] = []
    in_arch_section = False
    section_lines: list[str] = []

    for line in content.splitlines():
        heading_match = re.match(r"^(#{1,4})\s+(.*)", line)
        if heading_match:
            if in_arch_section and section_lines:
                sections.append("\n".join(section_lines))
            heading_text = heading_match.group(2)
            if arch_re.search(heading_text):
                in_arch_section = True
                section_lines = [line]
            else:
                in_arch_section = False
                section_lines = []
        elif in_arch_section:
            section_lines.append(line)

    if in_arch_section and section_lines:
        sections.append("\n".join(section_lines))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Scope and mode helpers
# ---------------------------------------------------------------------------

# First-level directory names that are documentation containers, not package roots.
# Anchor files inside these directories apply globally to the whole repo.
_GLOBAL_DOC_DIRS = frozenset(
    {"docs", "doc", "adr", "architecture", ".github", "design"}
)


def _infer_scope_glob(anchor_path: str) -> str | None:
    """Return a scope glob for an anchor based on its repo location.

    Root-level files (ARCHITECTURE.md) → global (None).
    Documentation-dir files (docs/arch.md) → global (None).
    Package-dir files (packages/api/ARCHITECTURE.md) → packages/api/**.
    """
    parts = anchor_path.split("/")
    if len(parts) == 1:
        return None  # root-level file → global
    if parts[0] in _GLOBAL_DOC_DIRS:
        return None  # documentation directory → global
    return "/".join(parts[:-1]) + "/**"


def _anchor_applies_to(anchor: ArchitectureAnchor, file_path: str) -> bool:
    """Return True if the anchor's scope covers the given changed file path."""
    if anchor.scope_glob is None:
        return True
    return fnmatch.fnmatch(file_path, anchor.scope_glob)


def _compute_mode(anchors: list[ArchitectureAnchor]) -> str:
    """Pick full_verifier, narrow_local_pattern, or skip.

    full_verifier:       rank 1-3 present, OR rank 4-5 with rank 7+ corroboration.
    narrow_local_pattern: rank 4-5 alone, OR rank 7-10 (no authoritative rules).
    skip:                only rank 11 (sibling-file) or nothing.
    """
    if not anchors:
        return "skip"
    has_rank1_3 = any(a.rank <= 3 for a in anchors)
    has_rank4_5 = any(4 <= a.rank <= 5 for a in anchors)
    has_rank7_plus = any(a.rank >= 7 for a in anchors)
    has_rank4_10 = any(4 <= a.rank <= 10 for a in anchors)

    if has_rank1_3:
        return "full_verifier"
    if has_rank4_5 and has_rank7_plus:
        return "full_verifier"
    if has_rank4_10:
        return "narrow_local_pattern"
    return "skip"


def _build_anchors_by_path(
    anchors: list[ArchitectureAnchor],
    changed_paths: list[str],
) -> dict[str, list[ArchitectureAnchor]]:
    """Map each changed file path to the anchors that apply to it."""
    return {
        path: [a for a in anchors if _anchor_applies_to(a, path)]
        for path in changed_paths
    }


# ---------------------------------------------------------------------------
# Fetch helper
# ---------------------------------------------------------------------------

async def _try_fetch(adapter, repo: str, path: str, ref: str) -> str | None:
    """Fetch file content; return None on any error."""
    try:
        content = await adapter.fetch_file_content(repo, path, ref=ref)
        return content if content else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def discover_architecture_anchors(
    changed_paths: list[str],
    config: GuardianConfig,
    adapter=None,
    repo: str = "",
    head_sha: str = "",
) -> ArchitectureAnchorSet:
    """Discover architecture anchors cheapest-first and choose a review mode.

    Stage 0a — mode_override (config-only, no I/O)
    Stage 0b — explicit architecture_docs list (config rank 1)
    Stage 1  — top-level marker files (one fetch each)
    Stage 2  — ADR directories (list_repo_files + fetch each accepted ADR)
    Stage 3  — machine-enforced dependency-cruiser configs (one fetch each)
    Config   — path_scopes explicit anchors

    Returns an ArchitectureAnchorSet with mode + per-path anchor lists.
    """
    arch_cfg = getattr(config, "architecture", None)
    mode_override = getattr(arch_cfg, "mode_override", "auto") if arch_cfg else "auto"
    config_path_scopes: dict[str, list[str]] = (
        getattr(arch_cfg, "path_scopes", {}) if arch_cfg else {}
    )
    architecture_docs: list[str] = getattr(config, "architecture_docs", [])

    ref = head_sha or "HEAD"

    # --- Stage 0a: hard mode_override ---
    if mode_override != "auto":
        anchors_by_path: dict[str, list[ArchitectureAnchor]] = {
            p: [] for p in changed_paths
        }
        reason = f"mode forced by config: {mode_override}" if mode_override == "skip" else None
        return ArchitectureAnchorSet(
            mode=mode_override,  # type: ignore[arg-type]
            anchors_by_path=anchors_by_path,
            status_reason=reason,
        )

    # --- Stage 0b: explicit architecture_docs (rank 1, weight 0.9) ---
    # When architecture_docs is set it wins over auto-discovery: we still respect
    # path scoping so that an anchor scoped to packages/api/ does not apply to
    # changed files outside that subtree.
    anchors: list[ArchitectureAnchor] = []
    if architecture_docs and adapter and repo:
        for doc_path in architecture_docs:
            content = await _try_fetch(adapter, repo, doc_path, ref)
            if content:
                anchors.append(
                    ArchitectureAnchor(
                        path=doc_path,
                        rank=1,
                        weight=0.9,
                        anchor_class="rule",
                        content=content,
                        scope_glob=_infer_scope_glob(doc_path),
                    )
                )
        if anchors:
            anchors_by_path = _build_anchors_by_path(anchors, changed_paths)
            # If no changed file falls within any anchor's scope → skip.
            if not any(anchors_by_path.values()):
                return ArchitectureAnchorSet(
                    mode="skip",
                    anchors_by_path=anchors_by_path,
                    status_reason="no architecture context found",
                )
            return ArchitectureAnchorSet(
                mode="full_verifier",
                anchors_by_path=anchors_by_path,
            )

    if not adapter or not repo:
        anchors_by_path = {p: [] for p in changed_paths}
        return ArchitectureAnchorSet(
            mode="skip",
            anchors_by_path=anchors_by_path,
            status_reason="no architecture context found",
        )

    # --- Stage 1: top-level marker files ---
    for path, rank, weight, cls in _STAGE1_FILES:
        content = await _try_fetch(adapter, repo, path, ref)
        if not content:
            continue

        # AGENTS.md, CLAUDE.md, .cursorrules: filter to architecture-relevant content
        if path in _NEEDS_ARCH_FILTER:
            if not _has_architecture_content(content):
                log.debug("arch_anchor_skipped_non_arch", path=path)
                continue

        # CONTRIBUTING.md: extract only architecture sections
        if path in _CONTRIBUTING_PREFIXES:
            extracted = _extract_architecture_sections(content)
            if not extracted:
                log.debug("arch_anchor_skipped_no_arch_sections", path=path)
                continue
            content = extracted

        # ARCHITECTURE.md: classify as rule (imperative) or convention (descriptive)
        if path == "ARCHITECTURE.md":
            imperative = re.search(
                r"\b(must|shall|forbidden|prohibited|required|never)\b",
                content,
                re.IGNORECASE,
            )
            if imperative:
                cls = "rule"
                rank = 4
            else:
                cls = "convention"
                rank = 5

        anchors.append(
            ArchitectureAnchor(
                path=path,
                rank=rank,
                weight=weight,
                anchor_class=cls,
                content=content,
                scope_glob=_infer_scope_glob(path),
            )
        )

    # --- Stage 2: ADR directories ---
    for adr_dir in _ADR_DIRS:
        try:
            files = await adapter.list_repo_files(repo, ref=ref, path=adr_dir)
        except Exception:
            continue
        if not files:
            continue
        # Determine scope for ADRs in this directory.
        # Root-level dirs (docs/adr, adr, architecture/decisions) → global (None).
        # Nested dirs (packages/api/docs/adr) → scoped to package root.
        adr_dir_parts = adr_dir.split("/")
        if adr_dir_parts[0] in ("docs", "doc", "adr", "architecture"):
            adr_scope: str | None = None  # global
        else:
            # Scope to first two path components (the package root)
            adr_scope = "/".join(adr_dir_parts[:2]) + "/**"

        adr_loaded = 0
        for adr_file in files:
            if not adr_file.endswith(".md"):
                continue
            adr_content = await _try_fetch(adapter, repo, adr_file, ref)
            if not adr_content:
                continue
            is_accepted = bool(_ADR_ACCEPTED_RE.search(adr_content))
            anchors.append(
                ArchitectureAnchor(
                    path=adr_file,
                    rank=3,
                    weight=0.9 if is_accepted else 0.5,
                    anchor_class="rule" if is_accepted else "convention",
                    content=adr_content,
                    scope_glob=adr_scope,
                )
            )
            adr_loaded += 1
            if adr_loaded >= 20:
                break
        if adr_loaded:
            break  # use first found ADR directory

    # --- Stage 3: machine-enforced dependency-cruiser configs (rank 2, weight 1.0) ---
    for dc_path in _DEP_CRUISER_PATHS:
        content = await _try_fetch(adapter, repo, dc_path, ref)
        if content and _DEP_CRUISER_RULE_RE.search(content):
            anchors.append(
                ArchitectureAnchor(
                    path=dc_path,
                    rank=2,
                    weight=1.0,
                    anchor_class="rule",
                    content=content,
                    scope_glob=None,
                )
            )
            break

    # --- Config path_scopes: load explicit anchors for specific patterns ---
    if config_path_scopes:
        for path_pattern, anchor_paths in config_path_scopes.items():
            if not any(fnmatch.fnmatch(p, path_pattern) for p in changed_paths):
                continue
            for anchor_path in anchor_paths:
                if any(a.path == anchor_path for a in anchors):
                    continue
                content = await _try_fetch(adapter, repo, anchor_path, ref)
                if content:
                    anchors.append(
                        ArchitectureAnchor(
                            path=anchor_path,
                            rank=1,
                            weight=0.9,
                            anchor_class="rule",
                            content=content,
                            scope_glob=path_pattern,
                        )
                    )

    # --- Finalize: compute per-path anchors and overall mode ---
    anchors_by_path = _build_anchors_by_path(anchors, changed_paths)

    if not anchors:
        return ArchitectureAnchorSet(
            mode="skip",
            anchors_by_path=anchors_by_path,
            status_reason="no architecture context found",
        )

    # Overall mode = strongest mode across all paths that have anchors
    path_modes = [_compute_mode(path_anchors) for path_anchors in anchors_by_path.values()]
    non_skip_modes = [m for m in path_modes if m != "skip"]
    if not non_skip_modes:
        return ArchitectureAnchorSet(
            mode="skip",
            anchors_by_path=anchors_by_path,
            status_reason="no architecture context found",
        )

    overall_mode = max(non_skip_modes, key=lambda m: _MODE_RANK[m])

    log.info(
        "arch_anchors_discovered",
        count=len(anchors),
        mode=overall_mode,
        paths_covered=sum(1 for path_anchors in anchors_by_path.values() if path_anchors),
    )
    return ArchitectureAnchorSet(mode=overall_mode, anchors_by_path=anchors_by_path)
