from __future__ import annotations

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
    # Platform-specific metadata for API callbacks
    org: str = ""            # ADO org or GitHub owner
    project: str = ""        # ADO project (empty for GitHub)
    install_id: int | None = None  # GitHub App installation ID


@dataclass
class DiffFile:
    """A single file in a diff."""
    path: str
    status: Literal["added", "modified", "deleted", "renamed"]
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
