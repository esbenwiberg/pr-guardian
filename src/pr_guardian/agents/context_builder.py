from __future__ import annotations

from pr_guardian.models.context import ReviewContext
from pr_guardian.models.pr import DiffFile


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code."""
    return len(text) // 4


def _prioritize_files(context: ReviewContext) -> list[DiffFile]:
    """Return diff files sorted by review priority (highest first).

    Priority: security surface > blast radius > hotspots > rest.
    Within each tier, smaller patches come first so more files fit in the budget.
    """
    security_files = set(context.security_surface.classifications.keys())
    blast_files = set(context.blast_radius.propagated_surface.keys())
    hotspot_files = context.hotspots or set()

    def _sort_key(df: DiffFile) -> tuple[int, int]:
        if df.path in security_files:
            tier = 0
        elif df.path in blast_files:
            tier = 1
        elif df.path in hotspot_files:
            tier = 2
        else:
            tier = 3
        return (tier, len(df.patch))

    return sorted(context.diff.files, key=_sort_key)


def _build_diff_section(
    prioritized: list[DiffFile],
    budget_tokens: int,
) -> list[str]:
    """Build diff sections within a token budget.

    Returns text parts for the diff section, with truncation/omission markers
    so the LLM knows when content is missing.
    """
    parts: list[str] = []
    remaining = budget_tokens
    per_file_cap = max(budget_tokens * 30 // 100, 500)
    omitted: list[str] = []

    for df in prioritized:
        header = f"### {df.path} ({df.status})"
        header_tokens = _estimate_tokens(header) + 10  # fencing overhead

        if not df.patch:
            marker = f"{header}\n*[diff content not available — do not speculate about this file]*"
            cost = _estimate_tokens(marker)
            if cost <= remaining:
                parts.append(marker)
                remaining -= cost
            else:
                omitted.append(df.path)
            continue

        patch_tokens = _estimate_tokens(df.patch)

        if header_tokens > remaining:
            omitted.append(df.path)
            continue

        available = min(per_file_cap, remaining - header_tokens)

        if patch_tokens <= available:
            parts.append(f"{header}\n```\n{df.patch}\n```")
            remaining -= header_tokens + patch_tokens
        elif available >= 200:
            # Truncate to fit — cut at a line boundary
            char_limit = available * 4
            truncated = df.patch[:char_limit]
            last_nl = truncated.rfind("\n")
            if last_nl > 0:
                truncated = truncated[:last_nl]
            total_lines = df.patch.count("\n")
            shown_lines = truncated.count("\n")
            omitted_lines = total_lines - shown_lines
            parts.append(
                f"{header}\n```\n{truncated}\n```\n"
                f"*[diff truncated — {omitted_lines} lines omitted]*"
            )
            remaining -= header_tokens + _estimate_tokens(truncated) + 15
        else:
            omitted.append(df.path)

    if omitted:
        listing = ", ".join(omitted)
        parts.append(
            f"\n*[{len(omitted)} file(s) omitted due to context budget: {listing} "
            f"— do not speculate about omitted files]*"
        )

    return parts


def build_agent_context(
    context: ReviewContext,
    agent_name: str,
    max_context_tokens: int = 120_000,
) -> str:
    """Build the user message (diff + context) sent to an agent."""
    parts: list[str] = []

    # PR metadata
    parts.append(f"## PR: {context.pr.title}")
    parts.append(f"- Author: {context.pr.author}")
    parts.append(f"- Branch: {context.pr.source_branch} → {context.pr.target_branch}")
    parts.append(f"- Repo: {context.pr.repo}")
    parts.append(f"- Languages: {', '.join(context.language_map.languages.keys())}")
    parts.append(f"- Files changed: {len(context.changed_files)}")
    parts.append(f"- Lines changed: {context.lines_changed}")

    # Security surface context
    if context.security_surface.has_hits():
        parts.append("\n## Security Surface Hits")
        for file_path, classifications in context.security_surface.classifications.items():
            parts.append(f"- {file_path}: {', '.join(sorted(classifications))}")

    # Blast radius context
    if context.blast_radius.propagates_to_security or context.blast_radius.propagates_to_api:
        parts.append("\n## Blast Radius (Transitive Risk)")
        for file_path, propagated in context.blast_radius.propagated_surface.items():
            parts.append(f"- {file_path} propagates to: {', '.join(sorted(propagated))}")

    # Hotspot context
    if context.hotspots:
        hotspot_hits = [f for f in context.changed_files if f in context.hotspots]
        if hotspot_hits:
            parts.append(f"\n## Hotspot Files: {', '.join(hotspot_hits)}")

    # Calculate token budget remaining for diff
    metadata_text = "\n".join(parts)
    metadata_tokens = _estimate_tokens(metadata_text)
    diff_budget = max(max_context_tokens - metadata_tokens, 1000)

    # Prioritize files and build diff within budget
    prioritized = _prioritize_files(context)
    diff_parts = _build_diff_section(prioritized, diff_budget)

    parts.append("\n## Diff\n")
    parts.extend(diff_parts)

    return "\n".join(parts)
