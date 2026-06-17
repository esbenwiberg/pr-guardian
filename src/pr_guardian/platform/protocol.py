from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pr_guardian.models.findings import Finding
from pr_guardian.models.pr import Diff, PlatformPR


@dataclass(frozen=True)
class PlatformPRMetadata:
    """Platform facts that affect readiness but are not review quality signals."""

    head_sha: str
    draft: bool = False
    fork: bool = False
    closed: bool = False
    merged: bool = False


@dataclass(frozen=True)
class PlatformReadinessSignal:
    """One visible automated platform check, status, or policy."""

    name: str
    state: str
    source: str
    url: str = ""
    description: str = ""


@dataclass(frozen=True)
class GateResult:
    """Repository-side merge gate state for Guardian's required review check."""

    state: str
    message: str
    repo: str
    branch: str = ""
    context: str = "guardian/review"


@dataclass(frozen=True)
class InstallationMetadata:
    """GitHub App installation metadata safe to expose in setup flows."""

    installation_id: str
    account: str = ""
    target_type: str = ""
    repository_selection: str = ""
    permissions: dict | None = None


def inline_finding_payload(f: Finding) -> dict:
    """Serialize a finding into the payload stored against a posted inline comment.

    Mirrors the dismissal ``source_finding`` shape so a reply-to-comment dismissal
    can be recorded and matched (signature = file::category::agent_name) without
    re-deriving anything. ``agent_name`` must be the AgentResult's name — the same
    value the re-review dismissal filter uses — so dismissals actually take effect.
    """
    return {
        "file": f.file,
        "line": f.line,
        "category": f.category,
        "agent_name": f.primary_agent or "",
        "severity": f.severity.value,
        "certainty": f.certainty.value,
        "description": (f.description or "")[:500],
    }


@dataclass
class InlinePostResult:
    """Result of a post_inline_comments call."""

    posted_ids: list[str]
    skipped: list[Finding]
    # Maps each posted platform comment id -> the finding payloads it carries.
    # Populated by GitHub (used for reply-to-comment dismissals); other adapters
    # may leave it empty.
    id_to_findings: dict[str, list[dict]] = field(default_factory=dict)


class PlatformAdapter(Protocol):
    """Interface for platform operations (ADO + GitHub)."""

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        """Fetch and parse the PR diff."""
        ...

    async def fetch_archmap_artifact(self, pr: PlatformPR) -> str | None:
        """Fetch the optional Archmap JSON artifact for this PR head SHA."""
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
        self,
        pr: PlatformPR,
        state: str,
        description: str,
        context: str = "pr-guardian",
        target_url: str = "",
    ) -> None:
        """Set a commit status check."""
        ...

    async def fetch_pr_metadata(self, pr: PlatformPR) -> PlatformPRMetadata:
        """Fetch PR metadata used by readiness evaluation."""
        ...

    async def fetch_readiness_signals(self, pr: PlatformPR) -> list[PlatformReadinessSignal]:
        """Fetch visible automated checks/statuses/policies for the PR head SHA."""
        ...

    async def set_readiness_status(self, pr: PlatformPR, state: str, description: str) -> None:
        """Write Guardian's readiness status."""
        ...

    async def set_review_status(
        self, pr: PlatformPR, state: str, description: str, target_url: str = ""
    ) -> None:
        """Write Guardian's review execution/result status."""
        ...

    async def find_archmap_artifact(self, pr: PlatformPR, head_sha: str) -> bool:
        """Return whether the platform exposes the archmap artifact for the head SHA."""
        ...

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        """Request review from a team/group."""
        ...

    async def post_inline_comments(
        self,
        pr: PlatformPR,
        findings: list[Finding],
        *,
        threshold: str = "MEDIUM",
    ) -> InlinePostResult:
        """Post one inline comment per unique file+line group.

        Returns posted IDs and findings that could not be anchored (no line or
        line not present in the diff).  Callers must handle skipped findings —
        typically by falling back to the summary comment.
        """
        ...

    async def delete_inline_comments(
        self,
        pr: PlatformPR,
        comment_ids: list[str],
    ) -> None:
        """Delete previously posted inline comments by their platform-native IDs."""
        ...

    # --- Scan-mode methods ---

    async def fetch_recent_commits(
        self,
        repo: str,
        branch: str,
        since: str,
        until: str | None = None,
        per_page: int = 100,
    ) -> list[dict]:
        """Fetch commits on branch since a date (ISO 8601)."""
        ...

    async def fetch_merged_prs(
        self,
        repo: str,
        since: str,
        base: str = "main",
    ) -> list[dict]:
        """Fetch recently merged PRs."""
        ...

    async def fetch_file_content(
        self,
        repo: str,
        path: str,
        ref: str = "HEAD",
    ) -> str:
        """Fetch file content from the repo."""
        ...

    async def list_repo_files(
        self,
        repo: str,
        ref: str = "HEAD",
        path: str = "",
    ) -> list[str]:
        """List files in repo (recursive tree)."""
        ...

    async def list_recently_changed_files(
        self,
        repo: str,
        ref: str = "HEAD",
        limit: int = 300,
    ) -> list[str]:
        """List files most recently touched on ``ref``, newest-first.

        Walks recent commits (bounded internally) and returns up to ``limit``
        unique paths. Paths still existing as blobs in the tree only.
        """
        ...

    async def fetch_pr_files(
        self,
        repo: str,
        pr_id: int | str,
        project: str = "",
    ) -> list[dict]:
        """Fetch list of changed files for a PR (filename, additions, deletions)."""
        ...

    async def fetch_compare_diff(
        self,
        repo: str,
        base_sha: str,
        head_sha: str,
        project: str = "",
    ) -> Diff:
        """Fetch diff between two commits. Used for incremental re-reviews."""
        ...

    async def fetch_commits_for_path(
        self,
        repo: str,
        path: str,
        per_page: int = 1,
        project: str = "",
    ) -> list[dict]:
        """Fetch recent commits that touched a specific file path."""
        ...

    async def fetch_pr_body_and_commits(
        self,
        pr: PlatformPR,
    ) -> tuple[str, list[str]]:
        """Fetch the PR description body and a list of commit messages.

        Returns (pr_body, commit_messages). Both are best-effort — callers
        should treat empty strings / lists as acceptable outcomes.
        """
        ...
