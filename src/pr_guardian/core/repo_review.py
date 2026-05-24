"""Whole-repo review: runs the PR review pipeline against a full repo snapshot.

Treats the repo (at a given ref) as a synthetic PR where every selected file is
"added". Reuses the hardened PR review pipeline but lets users ad-hoc review a
smaller (or recently-active slice of a) repository end-to-end.

Two selection modes:

- ``all``    — walk the full tree, fail loudly if it's larger than ``max_files``.
- ``recent`` — walk recent commits and pick the N most recently touched files.

Only suitable for relatively small slices — token budget grows linearly with
total code size.
"""

from __future__ import annotations

from typing import Literal

import structlog

from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()


# Safety caps to prevent runaway cost/timeouts. "Small repos only."
DEFAULT_MAX_FILES = 300
DEFAULT_MAX_BYTES_PER_FILE = 200_000
DEFAULT_MAX_TOTAL_BYTES = 4_000_000

# Server-side ceiling. Caller-supplied max_files is clamped to this value.
HARD_MAX_FILES = 2000

SelectionMode = Literal["all", "recent"]

# Binary/generated paths we never want to feed to agents.
_SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".wav",
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".class",
    ".jar",
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
)
_SKIP_DIR_PARTS = (
    "node_modules/",
    "dist/",
    "build/",
    ".next/",
    ".git/",
    "vendor/",
    "__pycache__/",
    ".venv/",
    ".mypy_cache/",
    ".pytest_cache/",
)


def clamp_max_files(requested: int) -> int:
    """Clamp a caller-supplied max_files to [1, HARD_MAX_FILES]."""
    if requested <= 0:
        return DEFAULT_MAX_FILES
    return min(requested, HARD_MAX_FILES)


def _should_skip(path: str) -> bool:
    low = path.lower()
    if any(p in low for p in _SKIP_DIR_PARTS):
        return True
    return low.endswith(_SKIP_SUFFIXES)


def _synthesize_patch(content: str) -> str:
    """Render a unified-diff patch showing the entire file as added."""
    lines = content.splitlines()
    count = len(lines) if lines else 1
    header = f"@@ -0,0 +1,{count} @@\n"
    return header + "\n".join(f"+{line}" for line in lines)


async def _select_candidates(
    adapter: PlatformAdapter,
    repo: str,
    ref: str,
    selection: SelectionMode,
    max_files: int,
) -> tuple[list[str], int, bool]:
    """Pick the file paths we'll feed to the diff.

    Returns (candidates, total_listed, capped_in_selection).

    For ``all``: lists the full tree, filters, and *raises* if the survivors
    exceed ``max_files`` — we don't want to silently review a random slice of a
    huge repo.

    For ``recent``: walks the most recent commits and returns up to ``max_files``
    most-recently-touched paths. ``capped_in_selection`` is True iff we hit the
    cap (i.e. there were more recently-changed files than ``max_files``).
    """
    if selection == "recent":
        # Ask the adapter for up to max_files recently-changed paths. The
        # adapter is responsible for ordering newest-first.
        paths = await adapter.list_recently_changed_files(
            repo,
            ref=ref,
            limit=max_files,
        )
        filtered = [p for p in paths if not _should_skip(p)]
        capped = len(filtered) >= max_files
        return filtered[:max_files], len(paths), capped

    # "all" — load the full tree
    all_paths = await adapter.list_repo_files(repo, ref=ref)
    candidates = [p for p in all_paths if not _should_skip(p)]
    if len(candidates) > max_files:
        raise ValueError(
            f"Repo has {len(candidates)} reviewable files (limit: {max_files}). "
            f"Too large for repo review — try selection='recent' or a "
            f"narrower scope."
        )
    return candidates, len(all_paths), False


async def build_repo_diff(
    adapter: PlatformAdapter,
    repo: str,
    ref: str = "HEAD",
    *,
    selection: SelectionMode = "all",
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[Diff, dict]:
    """Build a synthetic Diff covering the selected files.

    Returns (diff, metadata). ``metadata`` reports counts for UI feedback.
    Raises ValueError if the repo exceeds file or byte caps (only in
    ``all`` mode — ``recent`` is bounded by definition).
    """
    candidates, total_listed, capped = await _select_candidates(
        adapter,
        repo,
        ref,
        selection,
        max_files,
    )
    skipped_binary = total_listed - (len(candidates) if selection == "all" else total_listed)
    # For "recent" mode, skipped_binary is calculated differently:
    # adapter returns raw paths, we filter to candidates, the difference is skips.
    if selection == "recent":
        skipped_binary = total_listed - len(candidates)

    diff_files: list[DiffFile] = []
    total_bytes = 0
    truncated_files = 0
    read_errors = 0

    for path in candidates:
        try:
            content = await adapter.fetch_file_content(repo, path, ref=ref)
        except Exception as e:
            log.debug("repo_review_file_read_failed", path=path, error=str(e))
            read_errors += 1
            continue

        if len(content) > max_bytes_per_file:
            content = content[:max_bytes_per_file]
            truncated_files += 1

        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            raise ValueError(
                f"Repo total size exceeds {max_total_bytes} bytes. Too large for repo review."
            )

        diff_files.append(
            DiffFile(
                path=path,
                status="added",
                old_path=None,
                additions=content.count("\n")
                + (1 if content and not content.endswith("\n") else 0),
                deletions=0,
                patch=_synthesize_patch(content),
            )
        )

    meta = {
        "selection": selection,
        "requested_max_files": max_files,
        "files_listed": total_listed,
        "files_skipped_binary": skipped_binary,
        "files_included": len(diff_files),
        "files_truncated": truncated_files,
        "files_read_errors": read_errors,
        "selection_capped": capped,
        "total_bytes": total_bytes,
    }
    return Diff(files=diff_files), meta


def build_synthetic_pr(
    repo: str,
    platform: str,
    ref: str,
    synthetic_id: str,
) -> PlatformPR:
    plat = Platform.GITHUB if platform == "github" else Platform.ADO
    return PlatformPR(
        platform=plat,
        pr_id=f"repo-review-{synthetic_id}",
        repo=repo,
        repo_url="",
        source_branch=ref,
        target_branch="",
        author="",
        title=f"Repo review: {repo}@{ref}",
        head_commit_sha="",
        org="",
    )
