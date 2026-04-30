"""Whole-repo review: runs the PR review pipeline against a full repo snapshot.

Treats the repo (at a given ref) as a synthetic PR where every file is "added".
This reuses the hardened PR review pipeline but lets users ad-hoc review a
smaller repository end-to-end.

Only suitable for small repos — token budget grows linearly with total code size.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog

from pr_guardian.core.orchestrator import run_review
from pr_guardian.models.output import ReviewResult
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()


# Safety caps to prevent runaway cost/timeouts. "Small repos only."
DEFAULT_MAX_FILES = 300
DEFAULT_MAX_BYTES_PER_FILE = 200_000
DEFAULT_MAX_TOTAL_BYTES = 4_000_000

# Binary/generated paths we never want to feed to agents.
_SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".mov", ".avi", ".wav",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".class", ".jar",
    ".lock", ".min.js", ".min.css", ".map",
)
_SKIP_DIR_PARTS = (
    "node_modules/", "dist/", "build/", ".next/", ".git/", "vendor/",
    "__pycache__/", ".venv/", ".mypy_cache/", ".pytest_cache/",
)


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


async def build_repo_diff(
    adapter: PlatformAdapter,
    repo: str,
    ref: str = "HEAD",
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[Diff, dict]:
    """Walk the repo tree at ``ref`` and produce a synthetic Diff.

    Returns (diff, metadata). ``metadata`` reports counts for UI feedback.
    Raises ValueError if the repo exceeds file or byte caps.
    """
    all_paths = await adapter.list_repo_files(repo, ref=ref)
    candidates = [p for p in all_paths if not _should_skip(p)]
    skipped = len(all_paths) - len(candidates)

    if len(candidates) > max_files:
        raise ValueError(
            f"Repo has {len(candidates)} reviewable files (limit: {max_files}). "
            f"Too large for repo review — use PR reviews or scans instead."
        )

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
                f"Repo total size exceeds {max_total_bytes} bytes. "
                f"Too large for repo review."
            )

        diff_files.append(DiffFile(
            path=path,
            status="added",
            old_path=None,
            additions=content.count("\n") + (1 if content and not content.endswith("\n") else 0),
            deletions=0,
            patch=_synthesize_patch(content),
        ))

    meta = {
        "files_listed": len(all_paths),
        "files_skipped_binary": skipped,
        "files_included": len(diff_files),
        "files_truncated": truncated_files,
        "files_read_errors": read_errors,
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


async def run_repo_review(
    repo: str,
    platform: str,
    adapter: PlatformAdapter,
    *,
    ref: str = "HEAD",
    max_files: int = DEFAULT_MAX_FILES,
) -> ReviewResult:
    """Run the full review pipeline against an entire repo snapshot.

    Note: intended for small repos only. Platform side-effects (comments,
    status checks, labels) are suppressed since there's no real PR.
    """
    synthetic_id = uuid.uuid4().hex[:12]
    log.info("repo_review_started", repo=repo, ref=ref, synthetic_id=synthetic_id)

    diff, meta = await build_repo_diff(
        adapter, repo, ref=ref, max_files=max_files,
    )
    log.info(
        "repo_review_diff_built",
        repo=repo,
        files_included=meta["files_included"],
        total_bytes=meta["total_bytes"],
    )

    pr = build_synthetic_pr(repo, platform, ref, synthetic_id)

    return await run_review(
        pr,
        adapter,
        post_comment=False,
        dismissals=None,
        diff_override=diff,
        skip_platform_side_effects=True,
    )
