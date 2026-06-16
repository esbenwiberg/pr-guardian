"""Coverage for the post-baseline column reconcile (ADR-010)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from pr_guardian.persistence import models, reconcile


def _columns(engine, table: str) -> set[str]:
    async def _go() -> set[str]:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sc: {c["name"] for c in inspect(sc).get_columns(table)}
            )

    return asyncio.run(_go())


def test_reconcile_readds_missing_column(tmp_path):
    db = f"sqlite+aiosqlite:///{tmp_path}/recon.db"
    engine = create_async_engine(db)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
            # simulate a behind database missing a recent column
            await conn.exec_driver_sql("ALTER TABLE repo_links DROP COLUMN require_review_check")

    asyncio.run(_setup())
    assert "require_review_check" not in _columns(engine, "repo_links")

    with patch.object(reconcile, "_get_engine", return_value=engine):
        added = asyncio.run(reconcile.reconcile_schema())

    assert added.get("repo_links") == ["require_review_check"]
    assert "require_review_check" in _columns(engine, "repo_links")
    asyncio.run(engine.dispose())


def test_reconcile_is_noop_on_correct_schema(tmp_path):
    db = f"sqlite+aiosqlite:///{tmp_path}/recon_ok.db"
    engine = create_async_engine(db)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    asyncio.run(_setup())

    with patch.object(reconcile, "_get_engine", return_value=engine):
        added = asyncio.run(reconcile.reconcile_schema())

    assert added == {}
    asyncio.run(engine.dispose())
