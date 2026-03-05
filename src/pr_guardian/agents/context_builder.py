from __future__ import annotations

from pr_guardian.models.context import ReviewContext


def build_agent_context(context: ReviewContext, agent_name: str) -> str:
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

    # Diff
    parts.append("\n## Diff\n")
    for diff_file in context.diff.files:
        parts.append(f"### {diff_file.path} ({diff_file.status})")
        if diff_file.patch:
            parts.append(f"```\n{diff_file.patch}\n```")

    return "\n".join(parts)
