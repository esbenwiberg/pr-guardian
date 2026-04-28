from __future__ import annotations

from pr_guardian.models.findings import Finding


def inline_comment_body(findings: list[Finding]) -> str:
    """Format a list of co-located findings into a single inline comment body."""
    parts = []
    for f in findings:
        header = f"**{f.category}** ({f.severity.value}): {f.description}"
        parts.append(header + (f"\n> {f.suggestion}" if f.suggestion else ""))
    return "\n\n".join(parts)
