from __future__ import annotations

from typing import Protocol

from pr_guardian.models.pr import Diff, PlatformPR


class PlatformAdapter(Protocol):
    """Interface for platform operations (ADO + GitHub)."""

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        """Fetch and parse the PR diff."""
        ...

    async def post_comment(self, pr: PlatformPR, body: str) -> None:
        """Post a comment on the PR."""
        ...

    async def approve_pr(self, pr: PlatformPR) -> None:
        """Vote approve on the PR."""
        ...

    async def request_changes(self, pr: PlatformPR, body: str) -> None:
        """Submit a 'request changes' review on the PR."""
        ...

    async def add_label(self, pr: PlatformPR, label: str) -> None:
        """Add a label to the PR."""
        ...

    async def set_status(
        self, pr: PlatformPR, state: str, description: str, context: str = "pr-guardian"
    ) -> None:
        """Set a commit status check."""
        ...

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        """Request review from a team/group."""
        ...

    # --- Scan-mode methods ---

    async def fetch_recent_commits(
        self, repo: str, branch: str, since: str, until: str | None = None, per_page: int = 100,
    ) -> list[dict]:
        """Fetch commits on branch since a date (ISO 8601)."""
        ...

    async def fetch_merged_prs(
        self, repo: str, since: str, base: str = "main",
    ) -> list[dict]:
        """Fetch recently merged PRs."""
        ...

    async def fetch_file_content(
        self, repo: str, path: str, ref: str = "HEAD",
    ) -> str:
        """Fetch file content from the repo."""
        ...

    async def list_repo_files(
        self, repo: str, ref: str = "HEAD", path: str = "",
    ) -> list[str]:
        """List files in repo (recursive tree)."""
        ...
