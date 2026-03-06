"""Async database engine and session factory.

Engine and session are created lazily on first use so that importing this module
does not require a reachable database or the asyncpg driver at import time.
"""
from __future__ import annotations

import os


def _get_database_url() -> str:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://guardian:guardian@localhost:5432/pr_guardian",
    )
    # Ensure the async driver is used regardless of how the URL was configured
    if raw.startswith("postgresql://"):
        raw = "postgresql+asyncpg://" + raw[len("postgresql://"):]
    # asyncpg uses 'ssl' not 'sslmode' — strip the incompatible param
    raw = raw.replace("?sslmode=require", "?ssl=require").replace("&sslmode=require", "&ssl=require")
    return raw


_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        _engine = create_async_engine(
            _get_database_url(),
            echo=False,
            pool_size=5,
            max_overflow=10,
            connect_args={"timeout": 10},
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        _session_factory = async_sessionmaker(_get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


def async_session():
    """Return an async session context manager (lazy-initialised)."""
    return _get_session_factory()()


async def get_session():
    """Yield a session for dependency injection."""
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create tables if they don't exist (dev convenience — use Alembic in production)."""
    from pr_guardian.persistence.models import Base

    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()
