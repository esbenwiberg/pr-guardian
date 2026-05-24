"""Tests for build_repo_diff selection modes + clamp_max_files."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pr_guardian.core.repo_review import (
    HARD_MAX_FILES,
    build_repo_diff,
    clamp_max_files,
)


class TestClampMaxFiles:
    def test_negative_falls_back_to_default(self):
        assert clamp_max_files(-1) == 300

    def test_zero_falls_back_to_default(self):
        assert clamp_max_files(0) == 300

    def test_above_ceiling_clamped(self):
        assert clamp_max_files(999_999) == HARD_MAX_FILES

    def test_within_range_preserved(self):
        assert clamp_max_files(500) == 500


class TestBuildRepoDiffRecentSelection:
    @pytest.mark.asyncio
    async def test_recent_uses_adapter_recent_list(self):
        adapter = MagicMock()
        adapter.list_recently_changed_files = AsyncMock(
            return_value=["a.py", "b.py", "ignore.png", "c.py"],
        )
        adapter.fetch_file_content = AsyncMock(side_effect=lambda r, p, ref=None: f"# {p}\n")
        # list_repo_files should NOT be called in recent mode.
        adapter.list_repo_files = AsyncMock(side_effect=AssertionError("should not be called"))

        diff, meta = await build_repo_diff(
            adapter,
            "owner/repo",
            selection="recent",
            max_files=10,
        )

        adapter.list_recently_changed_files.assert_awaited_once()
        # binary skipped, three .py files included
        assert meta["selection"] == "recent"
        assert meta["files_included"] == 3
        assert meta["files_skipped_binary"] == 1
        assert meta["selection_capped"] is False
        assert [f.path for f in diff.files] == ["a.py", "b.py", "c.py"]

    @pytest.mark.asyncio
    async def test_recent_marks_capped_when_at_limit(self):
        adapter = MagicMock()
        adapter.list_recently_changed_files = AsyncMock(
            return_value=["a.py", "b.py"],
        )
        adapter.fetch_file_content = AsyncMock(side_effect=lambda r, p, ref=None: "x\n")
        adapter.list_repo_files = AsyncMock()

        _, meta = await build_repo_diff(
            adapter,
            "owner/repo",
            selection="recent",
            max_files=2,
        )
        assert meta["selection_capped"] is True

    @pytest.mark.asyncio
    async def test_all_raises_when_too_big(self):
        adapter = MagicMock()
        adapter.list_repo_files = AsyncMock(return_value=[f"f{i}.py" for i in range(10)])
        adapter.fetch_file_content = AsyncMock(return_value="x\n")

        with pytest.raises(ValueError, match="Too large"):
            await build_repo_diff(
                adapter,
                "owner/repo",
                selection="all",
                max_files=3,
            )

    @pytest.mark.asyncio
    async def test_truncation_counted(self):
        adapter = MagicMock()
        adapter.list_recently_changed_files = AsyncMock(return_value=["big.py"])
        adapter.fetch_file_content = AsyncMock(return_value="x" * 5000)
        adapter.list_repo_files = AsyncMock()

        _, meta = await build_repo_diff(
            adapter,
            "owner/repo",
            selection="recent",
            max_files=10,
            max_bytes_per_file=100,
        )
        assert meta["files_truncated"] == 1
        assert meta["files_included"] == 1
