"""Tests for commit-range review (path B) and the require_write auth gate."""

from __future__ import annotations

import pytest

from pr_guardian.auth.dependencies import require_write
from pr_guardian.auth.identity import Identity
from pr_guardian.core.range_review import (
    RangeResolutionError,
    build_range_diff,
    build_range_pr,
    resolve_range,
)
from pr_guardian.models.pr import Diff, DiffFile, Platform


class FakeRangeAdapter:
    """Minimal adapter exposing only what range resolution/build needs."""

    def __init__(self, *, commits=None, compare=None):
        self._commits = commits or []
        self._compare = compare or Diff(files=[])
        self.compare_calls: list[tuple] = []
        self.recent_calls: list[dict] = []

    async def fetch_recent_commits(self, repo, branch, since, until=None, per_page=100):
        self.recent_calls.append({"repo": repo, "branch": branch, "since": since})
        return self._commits

    async def fetch_compare_diff(self, repo, base_sha, head_sha, project=""):
        self.compare_calls.append((repo, base_sha, head_sha))
        return self._compare


# ---------------------------------------------------------------------------
# resolve_range
# ---------------------------------------------------------------------------


async def test_resolve_range_commit_mode_defaults_head_to_branch():
    adapter = FakeRangeAdapter()
    base, head = await resolve_range(adapter, "o/r", branch="main", since_commit="abc123")
    assert base == "abc123"
    assert head == "main"
    # Commit mode never needs to walk history.
    assert adapter.recent_calls == []


async def test_resolve_range_commit_mode_explicit_head():
    adapter = FakeRangeAdapter()
    base, head = await resolve_range(adapter, "o/r", branch="main", since_commit="abc", head="def")
    assert (base, head) == ("abc", "def")


async def test_resolve_range_time_mode_uses_parent_as_base():
    # newest-first; earliest (last) commit's first parent is the base.
    commits = [
        {"sha": "head_sha", "parents": [{"sha": "mid"}]},
        {"sha": "old_sha", "parents": [{"sha": "base_sha"}]},
    ]
    adapter = FakeRangeAdapter(commits=commits)
    base, head = await resolve_range(
        adapter, "o/r", branch="main", since_time="2026-06-28T00:00:00Z"
    )
    assert base == "base_sha"
    assert head == "head_sha"
    assert adapter.recent_calls[0]["since"] == "2026-06-28T00:00:00Z"


async def test_resolve_range_requires_exactly_one_input():
    adapter = FakeRangeAdapter()
    with pytest.raises(RangeResolutionError):
        await resolve_range(adapter, "o/r", branch="main")
    with pytest.raises(RangeResolutionError):
        await resolve_range(adapter, "o/r", branch="main", since_commit="a", since_time="t")


async def test_resolve_range_time_mode_no_commits_raises():
    adapter = FakeRangeAdapter(commits=[])
    with pytest.raises(RangeResolutionError):
        await resolve_range(adapter, "o/r", branch="main", since_time="t")


async def test_resolve_range_time_mode_root_commit_raises():
    # Earliest commit has no parent → range reaches start of history.
    commits = [{"sha": "only", "parents": []}]
    adapter = FakeRangeAdapter(commits=commits)
    with pytest.raises(RangeResolutionError):
        await resolve_range(adapter, "o/r", branch="main", since_time="t")


async def test_resolve_range_commit_equals_head_raises():
    adapter = FakeRangeAdapter()
    with pytest.raises(RangeResolutionError):
        await resolve_range(adapter, "o/r", branch="main", since_commit="x", head="x")


# ---------------------------------------------------------------------------
# build_range_diff
# ---------------------------------------------------------------------------


async def test_build_range_diff_passes_through_and_reports_meta():
    compare = Diff(
        files=[
            DiffFile(path="a.py", status="modified", patch="+x"),
            DiffFile(path="b.py", status="added", patch="+y"),
        ]
    )
    adapter = FakeRangeAdapter(compare=compare)
    diff, meta = await build_range_diff(adapter, "o/r", "base", "head")
    assert adapter.compare_calls == [("o/r", "base", "head")]
    assert meta["files_changed"] == 2
    assert meta["base_ref"] == "base" and meta["head_ref"] == "head"


async def test_build_range_diff_truncates_and_drops_over_budget():
    big = "+" + ("z" * 1000)
    compare = Diff(
        files=[
            DiffFile(path="a.py", status="modified", patch=big),
            DiffFile(path="b.py", status="modified", patch=big),
        ]
    )
    adapter = FakeRangeAdapter(compare=compare)
    diff, meta = await build_range_diff(
        adapter,
        "o/r",
        "base",
        "head",
        max_bytes_per_file=100,
        max_total_bytes=100,
    )
    # First file truncated to the per-file cap; second dropped once budget hit.
    assert meta["files_truncated"] == 1
    assert meta["files_dropped_budget"] == 1
    assert len(diff.files[0].patch) == 100
    assert diff.files[1].patch == ""


# ---------------------------------------------------------------------------
# build_range_pr
# ---------------------------------------------------------------------------


def test_build_range_pr_shape():
    pr = build_range_pr("o/r", "github", "basesha1234567", "headsha7654321", "main", "abc12345")
    assert pr.platform == Platform.GITHUB
    assert pr.pr_id == "range-review-abc12345"
    assert pr.target_branch == "main"  # branch policies still apply
    assert pr.head_commit_sha == "headsha7654321"
    assert "basesha12345" in pr.title and "headsha76543" in pr.title


# ---------------------------------------------------------------------------
# require_write auth gate
# ---------------------------------------------------------------------------


class _Req:
    def __init__(self, identity):
        self.state = type("S", (), {"identity": identity})()


async def test_require_write_allows_user():
    ident = Identity(kind="user", email="x@y.z")
    assert await require_write(_Req(ident)) is ident


async def test_require_write_allows_write_scoped_key():
    ident = Identity(kind="api_key", key_name="ci", scopes=["read", "write"])
    assert await require_write(_Req(ident)) is ident


async def test_require_write_blocks_readonly_key():
    ident = Identity(kind="api_key", key_name="ro", scopes=["read"])
    with pytest.raises(Exception) as ei:
        await require_write(_Req(ident))
    assert getattr(ei.value, "status_code", None) == 403


async def test_require_write_blocks_anonymous_nonadmin():
    ident = Identity(kind="anonymous", is_admin=False)
    with pytest.raises(Exception) as ei:
        await require_write(_Req(ident))
    assert getattr(ei.value, "status_code", None) == 401


async def test_require_write_allows_anonymous_admin_devmode():
    # GUARDIAN_DEV_ADMIN / no-DB fallback resolves anonymous as admin.
    ident = Identity(kind="anonymous", is_admin=True)
    assert await require_write(_Req(ident)) is ident
