from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkItem:
    """Linked work item (ADO work item or GitHub issue)."""
    id: str
    source: str  # "azure-devops" or "github"
    title: str
    description: str = ""
    type: str = ""  # "User Story", "Bug", "Task", etc.
    state: str = ""


async def fetch_work_item(
    pr_title: str,
    platform: str,
    org: str = "",
    project: str = "",
) -> WorkItem | None:
    """Extract and fetch linked work item from PR title/description.

    Convention: ADO uses "AB#12345", GitHub uses "#42".
    Full implementation will call platform API.
    """
    import re

    # ADO pattern: AB#12345
    ado_match = re.search(r'AB#(\d+)', pr_title)
    if ado_match and platform == "ado":
        return WorkItem(
            id=f"AB#{ado_match.group(1)}",
            source="azure-devops",
            title="",  # Would be fetched from ADO API
        )

    # GitHub pattern: #42
    gh_match = re.search(r'#(\d+)', pr_title)
    if gh_match and platform == "github":
        return WorkItem(
            id=f"#{gh_match.group(1)}",
            source="github",
            title="",  # Would be fetched from GitHub API
        )

    return None
