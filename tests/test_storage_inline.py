"""Tests for save_inline_comment_ids / load_inline_comment_ids storage helpers."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.persistence.storage import load_inline_comment_ids, save_inline_comment_ids

# Minimal SQLite-compatible schema — avoids JSONB/PostgreSQL-UUID DDL issues
# while still exercising the ORM bind/result processors at runtime.
_meta = sa.MetaData()
sa.Table("reviews", _meta, sa.Column("id", sa.Text, primary_key=True))
sa.Table(
    "posted_inline_comments",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("review_id", sa.Text, nullable=False),
    sa.Column("platform_comment_id", sa.String(256), nullable=False),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("pr_id", sa.String(64), nullable=False),
    sa.Column("repo", sa.String(256), nullable=False),
    sa.Column("created_at", sa.DateTime),
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_load_unknown_review_returns_empty():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            result = await load_inline_comment_ids(uuid.uuid4())
        assert result == []
    finally:
        await engine.dispose()


async def test_save_empty_list_then_load():
    engine, factory = await _make_session_factory()
    try:
        review_id = uuid.uuid4()
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await save_inline_comment_ids(review_id, [], "github", "42", "org/repo")
            result = await load_inline_comment_ids(review_id)
        assert result == []
    finally:
        await engine.dispose()


async def test_save_and_load_multiple_ids():
    engine, factory = await _make_session_factory()
    try:
        review_id = uuid.uuid4()
        ids = ["comment-1", "comment-2", "comment-3"]
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await save_inline_comment_ids(review_id, ids, "github", "99", "org/repo")
            result = await load_inline_comment_ids(review_id)
        assert sorted(result) == sorted(ids)
    finally:
        await engine.dispose()


async def test_load_isolates_by_review_id():
    engine, factory = await _make_session_factory()
    try:
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await save_inline_comment_ids(r1, ["c-a", "c-b"], "github", "1", "org/repo")
            await save_inline_comment_ids(r2, ["c-c"], "github", "2", "org/repo")
            assert sorted(await load_inline_comment_ids(r1)) == ["c-a", "c-b"]
            assert await load_inline_comment_ids(r2) == ["c-c"]
    finally:
        await engine.dispose()
