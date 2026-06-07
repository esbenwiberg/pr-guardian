"""Unit tests for save_guidance_comment_id / load_guidance_comment_id storage helpers.

Tests cover the non-PostgreSQL UPDATE→INSERT path (used with SQLite in-memory),
the IntegrityError rollback/tolerate path, and the load helper.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.persistence import models, storage


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_save_guidance_comment_id_inserts_when_no_existing_row():
    engine, factory = await _make_db()
    with (
        patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
        patch("pr_guardian.persistence.storage._get_engine", return_value=engine),
    ):
        await storage.save_guidance_comment_id("github", "owner/repo", "42", "cmt-001")
        result = await storage.load_guidance_comment_id("github", "owner/repo", "42")
    assert result == "cmt-001"


@pytest.mark.asyncio
async def test_save_guidance_comment_id_updates_existing_row():
    engine, factory = await _make_db()
    with (
        patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
        patch("pr_guardian.persistence.storage._get_engine", return_value=engine),
    ):
        await storage.save_guidance_comment_id("github", "owner/repo", "42", "cmt-001")
        await storage.save_guidance_comment_id("github", "owner/repo", "42", "cmt-002")
        result = await storage.load_guidance_comment_id("github", "owner/repo", "42")
    assert result == "cmt-002"


@pytest.mark.asyncio
async def test_save_guidance_comment_id_tolerates_integrity_error_on_concurrent_insert():
    """Simulate a concurrent INSERT winning the race: IntegrityError must be swallowed."""
    engine, factory = await _make_db()

    call_count = 0

    async def patched_session():
        nonlocal call_count
        call_count += 1
        sess = factory()
        if call_count == 1:
            original_add = sess.__class__.add

            def raising_add(self, obj):
                raise IntegrityError("UNIQUE constraint failed", None, None)

            sess.__class__.add = raising_add
            try:
                return sess
            finally:
                sess.__class__.add = original_add
        return sess

    # We cannot easily intercept the session.add path without deeper monkeypatching;
    # instead verify rollback is called when IntegrityError is raised inside the
    # save function by mocking the session at a higher level.
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    execute_result = MagicMock()
    execute_result.rowcount = 0
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_session.add = MagicMock(
        side_effect=IntegrityError("UNIQUE constraint failed", None, None)
    )
    mock_session.rollback = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.dialect.name = "sqlite"

    with (
        patch("pr_guardian.persistence.storage.async_session", lambda: mock_session),
        patch("pr_guardian.persistence.storage._get_engine", return_value=mock_engine),
    ):
        # Must not raise
        await storage.save_guidance_comment_id("github", "owner/repo", "99", "cmt-race")

    mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_load_guidance_comment_id_returns_none_when_absent():
    engine, factory = await _make_db()
    with (
        patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
        patch("pr_guardian.persistence.storage._get_engine", return_value=engine),
    ):
        result = await storage.load_guidance_comment_id("github", "owner/repo", "999")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_load_multiple_prs_are_independent():
    engine, factory = await _make_db()
    with (
        patch("pr_guardian.persistence.storage.async_session", lambda: factory()),
        patch("pr_guardian.persistence.storage._get_engine", return_value=engine),
    ):
        await storage.save_guidance_comment_id("github", "owner/repo", "1", "cmt-pr1")
        await storage.save_guidance_comment_id("github", "owner/repo", "2", "cmt-pr2")
        r1 = await storage.load_guidance_comment_id("github", "owner/repo", "1")
        r2 = await storage.load_guidance_comment_id("github", "owner/repo", "2")
    assert r1 == "cmt-pr1"
    assert r2 == "cmt-pr2"
