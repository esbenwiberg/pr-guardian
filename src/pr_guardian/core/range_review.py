"""Commit-range review: runs the full PR review pipeline over a base..head diff.

Where ``repo_review`` treats a whole-file snapshot as a synthetic PR, this treats
the *change set between two commits* as one. The diff is the actual
``base..head`` delta from the platform compare API — the same shape a PR diff
has — so it flows through the hardened pipeline (mechanical gates → triage →
agents → decision) and produces a real verdict.

This is the engine behind "review everything since commit X / since time T":
e.g. a nightly CI sweep of the day's merges, reviewed with the same rules as a
PR, baseline-tracked by the caller (see docs/ci-nightly-range-review.md).

Resolution:

- ``since_commit`` — base is that commit/ref, head defaults to ``branch``.
- ``since_time``   — walk commits on ``branch`` after the timestamp; base is the
  parent of the earliest such commit, head is the newest (or an explicit head).

Refs (branch names, tags, SHAs) pass through to GitHub's compare API verbatim.
ADO's compare needs concrete commit SHAs — pass ``since_commit``/``head`` as
SHAs there, not branch names.
"""

from __future__ import annotations

import structlog

from pr_guardian.models.pr import Diff, Platform, PlatformPR
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()

# Mirror repo_review's per-file / total budget so a sprawling range can't blow
# the token budget. Patches are truncated, not dropped, so the agent still sees
# the head of every changed file.
DEFAULT_MAX_BYTES_PER_FILE = 200_000
DEFAULT_MAX_TOTAL_BYTES = 4_000_000


class RangeResolutionError(ValueError):
    """The requested range could not be resolved to a base..head pair."""


def _commit_sha(commit: dict) -> str:
    """Best-effort SHA extraction across GitHub (``sha``) and ADO (``commitId``)."""
    return commit.get("sha") or commit.get("commitId") or ""


def _first_parent_sha(commit: dict) -> str:
    """First-parent SHA of a commit, across GitHub and ADO payload shapes."""
    parents = commit.get("parents") or []
    if not parents:
        return ""
    first = parents[0]
    if isinstance(first, dict):
        return first.get("sha") or first.get("commitId") or ""
    return str(first)


async def resolve_range(
    adapter: PlatformAdapter,
    repo: str,
    *,
    branch: str,
    since_commit: str | None = None,
    since_time: str | None = None,
    head: str | None = None,
    project: str = "",
) -> tuple[str, str]:
    """Resolve review inputs to a concrete ``(base_ref, head_ref)`` pair.

    Exactly one of ``since_commit`` / ``since_time`` must be given. Raises
    ``RangeResolutionError`` when the range is ambiguous, empty, or predates the
    repo's history — we fail loud rather than silently review the wrong slice.
    """
    if bool(since_commit) == bool(since_time):
        raise RangeResolutionError("Provide exactly one of since_commit or since_time.")

    if since_commit:
        head_ref = head or branch
        if since_commit == head_ref:
            raise RangeResolutionError(f"Empty range: base and head are both '{head_ref}'.")
        return since_commit, head_ref

    # Time-based: find the commits on branch newer than the timestamp. The
    # platform returns them newest-first; the base is the parent of the oldest.
    commits = await adapter.fetch_recent_commits(repo, branch=branch, since=since_time or "")
    if not commits:
        raise RangeResolutionError(
            f"No commits on '{branch}' since {since_time} — nothing to review."
        )

    head_ref = head or _commit_sha(commits[0])
    base_ref = _first_parent_sha(commits[-1])
    if not head_ref:
        raise RangeResolutionError("Could not resolve a head commit for the range.")
    if not base_ref:
        raise RangeResolutionError(
            f"Earliest commit since {since_time} has no parent — the range reaches "
            f"the start of history; pass an explicit since_commit instead."
        )
    return base_ref, head_ref


async def build_range_diff(
    adapter: PlatformAdapter,
    repo: str,
    base_ref: str,
    head_ref: str,
    *,
    project: str = "",
    max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[Diff, dict]:
    """Fetch the ``base..head`` compare diff, capped to the byte budget.

    Returns ``(diff, metadata)``. Files whose patch exceeds the per-file cap are
    truncated; once the cumulative budget is hit, later files keep their
    metadata but their patch is dropped (so agents still know they changed).
    """
    diff = await adapter.fetch_compare_diff(repo, base_ref, head_ref, project=project)

    total_bytes = 0
    truncated = 0
    dropped = 0
    for f in diff.files:
        patch = f.patch or ""
        if total_bytes >= max_total_bytes:
            if patch:
                f.patch = ""
                dropped += 1
            continue
        if len(patch) > max_bytes_per_file:
            f.patch = patch[:max_bytes_per_file]
            truncated += 1
        total_bytes += len(f.patch or "")

    meta = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "files_changed": len(diff.files),
        "files_truncated": truncated,
        "files_dropped_budget": dropped,
        "total_bytes": total_bytes,
    }
    return diff, meta


def build_range_pr(
    repo: str,
    platform: str,
    base_ref: str,
    head_ref: str,
    branch: str,
    synthetic_id: str,
) -> PlatformPR:
    """Synthetic PR for a commit-range review.

    ``target_branch`` is the reviewed branch so branch-scoped policies still
    apply; ``head_commit_sha`` is the head ref so the dashboard and provenance
    record the exact range. Side effects are skipped by the caller — there is no
    real PR to approve or block.
    """
    plat = Platform.GITHUB if platform == "github" else Platform.ADO
    return PlatformPR(
        platform=plat,
        pr_id=f"range-review-{synthetic_id}",
        repo=repo,
        repo_url="",
        source_branch=head_ref,
        target_branch=branch,
        author="",
        title=f"Range review: {repo} {base_ref[:12]}..{head_ref[:12]}",
        head_commit_sha=head_ref,
        org="",
    )
