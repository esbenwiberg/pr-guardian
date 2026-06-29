"""Regression: a long LLM-generated finding field must not truncate-crash the
scan save, and a save failure must surface as a failed scan (not fake-complete).

A scan agent emitted a `category` longer than varchar(64); the INSERT raised
StringDataRightTruncationError and rolled back the whole save — 11 findings
persisted as 0, scan stuck at `scan_report`, yet the UI showed "complete"
because scan_complete was emitted regardless. Note: sqlite (the test DB) does
NOT enforce varchar length, so this class of bug is invisible to in-memory
tests — these guards are structural + behavioural instead.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from pr_guardian.persistence.models import ScanAgentResultRow, ScanFindingRow


def _length(col_name: str):
    return getattr(ScanFindingRow.__table__.c[col_name].type, "length", None)


def test_llm_freetext_finding_columns_are_uncapped_text():
    # category + effort_estimate are LLM-generated; a capped varchar truncates
    # and rolls back the entire scan save. They must be unbounded Text.
    for name in ("category", "effort_estimate"):
        col = ScanFindingRow.__table__.c[name]
        assert isinstance(col.type, sa.Text), f"{name} must be Text, got {col.type!r}"
        assert _length(name) is None, f"{name} must be uncapped, got length={_length(name)}"


def test_scan_agent_name_is_uncapped_text():
    # The deep ("fat nightly") scan stores a per-PR identity (`PR #<n>: <title>`)
    # in agent_name; a PR title easily exceeds varchar(64) and a capped column
    # truncate-crashed the whole deep-scan save (strands it at scan_report with 0
    # findings). Must be unbounded Text — see migration 006.
    col = ScanAgentResultRow.__table__.c["agent_name"]
    assert isinstance(col.type, sa.Text), f"agent_name must be Text, got {col.type!r}"
    assert getattr(col.type, "length", None) is None


@pytest.mark.asyncio
async def test_scan_save_failure_marks_scan_failed_and_propagates():
    """If persistence fails, run_recent_changes_scan must mark the scan failed
    and re-raise — NOT swallow it and let the pipeline emit scan_complete."""
    from pr_guardian.core import recent_changes as rc

    fake_storage = AsyncMock()
    scan_db_id = uuid.uuid4()

    with (
        patch.object(rc, "_try_import_storage", return_value=fake_storage),
        patch.object(
            rc, "_run_recent_pipeline", side_effect=RuntimeError("value too long for varchar(64)")
        ),
    ):
        with pytest.raises(RuntimeError, match="value too long"):
            await rc.run_recent_changes_scan(
                repo="context-and/cicd",
                platform="github",
                adapter=AsyncMock(),
                config=AsyncMock(),
                scan_db_id=scan_db_id,
            )

    fake_storage.mark_scan_failed.assert_awaited_once()
    args, kwargs = fake_storage.mark_scan_failed.await_args
    assert args[0] == scan_db_id
    assert "value too long" in args[1]


@pytest.mark.asyncio
async def test_deep_scan_save_failure_marks_scan_failed_and_propagates():
    """Same guarantee for the deep (pr_like) scan: a save failure must mark the
    scan failed and re-raise, not be swallowed while scan_complete is emitted."""
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core import pr_like_scan as deep

    fake_storage = AsyncMock()
    scan_db_id = uuid.uuid4()

    with (
        patch.object(deep, "_try_import_storage", return_value=fake_storage),
        patch.object(
            deep,
            "_run_deep_pipeline",
            side_effect=RuntimeError("value too long for varchar(64)"),
        ),
    ):
        with pytest.raises(RuntimeError, match="value too long"):
            await deep.run_pr_like_scan(
                repo="context-and/cicd",
                platform="github",
                adapter=AsyncMock(),
                config=GuardianConfig(),
                scan_db_id=scan_db_id,
            )

    fake_storage.mark_scan_failed.assert_awaited_once()
    args, kwargs = fake_storage.mark_scan_failed.await_args
    assert args[0] == scan_db_id
    assert "value too long" in args[1]
