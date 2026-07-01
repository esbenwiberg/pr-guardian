from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Platform(str, Enum):
    ADO = "ado"
    GITHUB = "github"


@dataclass(frozen=True)
class PlatformPR:
    """Normalized PR representation from any platform."""

    platform: Platform
    pr_id: str
    repo: str
    repo_url: str
    source_branch: str
    target_branch: str
    author: str
    title: str
    head_commit_sha: str
    # PR description fetched from the platform during hydration.  None means
    # the body has not been fetched yet; "" means the PR genuinely has no
    # description.  fetch_pr_body_and_commits checks for None to avoid a
    # redundant GET when _hydrate_pr already retrieved the body.
    body: str | None = None
    # Platform-specific metadata for API callbacks
    org: str = ""  # ADO org or GitHub owner
    project: str = ""  # ADO project (empty for GitHub)
    install_id: int | None = None  # GitHub App installation ID

    @property
    def pr_url(self) -> str:
        """Construct the web URL for this pull request."""
        if self.platform == Platform.GITHUB:
            return f"https://github.com/{self.repo}/pull/{self.pr_id}"
        if self.platform == Platform.ADO:
            return f"{self.repo_url}/pullrequest/{self.pr_id}"
        return ""


FileStatus = Literal["added", "modified", "deleted", "renamed"]


@dataclass
class DiffFile:
    """A single file in a diff."""

    path: str
    status: FileStatus
    old_path: str | None = None  # for renames
    additions: int = 0
    deletions: int = 0
    patch: str = ""


@dataclass
class Diff:
    """Parsed PR diff."""

    files: list[DiffFile] = field(default_factory=list)

    @property
    def file_paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def lines_added(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def lines_removed(self) -> int:
        return sum(f.deletions for f in self.files)

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_removed

    @property
    def identity_hash(self) -> str:
        """Stable content hash of the PR's *net* changes (three-dot diff vs base).

        GitHub's ``/pulls/{id}/files`` — the source of this ``Diff`` — returns the
        merge-base→head comparison, so this fingerprints exactly what the PR adds
        on top of its base, independent of the head SHA. A pure "Update branch"
        base-merge that introduces no new head-side content and shifts no hunks
        yields the same hash as the pre-merge head; if the base changed a file the
        PR also touches (so hunks move), the hash changes and the PR is re-reviewed.
        Files are sorted so ordering from the platform can't perturb the hash.
        Used by readiness carry-forward (issue #97).
        """
        payload = [
            [f.path, f.old_path or "", f.status, f.additions, f.deletions, f.patch]
            for f in sorted(self.files, key=lambda f: (f.path, f.old_path or ""))
        ]
        blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
