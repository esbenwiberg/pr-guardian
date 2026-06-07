"""Storage coverage for Profiles, Connections, and legacy PAT migration."""

from __future__ import annotations

import uuid
from importlib import util
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.persistence.crypto import encrypt
from pr_guardian.persistence import models
from pr_guardian.persistence.storage import (
    ArchiveBlockedError,
    archive_connection,
    archive_profile,
    create_connection,
    create_profile,
    create_repo_link,
    get_connection,
    update_repo_link_state,
)

_migration_path = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "019_add_profiles_connections_readiness.py"
)
_spec = util.spec_from_file_location(
    "migration_019_add_profiles_connections_readiness", _migration_path
)
assert _spec is not None and _spec.loader is not None
migration = util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


_meta = sa.MetaData()
sa.Table(
    "profiles",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.String(128), unique=True),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "connections",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.String(128), unique=True),
    sa.Column("description", sa.String(256), nullable=False, server_default=""),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("auth_kind", sa.String(32)),
    sa.Column("org_url", sa.Text),
    sa.Column("encrypted_token", sa.Text),
    sa.Column("token_secret_ref", sa.Text),
    sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
    sa.Column("app_id", sa.String(64)),
    sa.Column("app_slug", sa.String(128)),
    sa.Column("installation_id", sa.String(64)),
    sa.Column("installation_account", sa.String(256)),
    sa.Column("installation_target_type", sa.String(32)),
    sa.Column("encrypted_private_key", sa.Text),
    sa.Column("private_key_fingerprint", sa.String(128)),
    sa.Column("app_permissions", sa.JSON),
    sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown"),
    sa.Column("health_message", sa.Text, nullable=False, server_default=""),
    sa.Column("health_checked_at", sa.DateTime(timezone=True)),
    sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "repo_links",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("org_url", sa.Text, nullable=False, server_default=""),
    sa.Column("project", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_owner", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_name", sa.String(256), nullable=False),
    sa.Column("repo_url", sa.Text, nullable=False, server_default=""),
    sa.Column("canonical_repo_key", sa.String(512), nullable=False, unique=True),
    sa.Column("profile_id", sa.Text, nullable=False),
    sa.Column("connection_id", sa.Text, nullable=False),
    sa.Column("auto_review_enabled", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("paused", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "profile_audit_events",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("actor", sa.String(256), nullable=False, server_default="system"),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("target_type", sa.String(64), nullable=False),
    sa.Column("target_id", sa.Text),
    sa.Column("before", sa.JSON),
    sa.Column("after", sa.JSON),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_profile_and_connection_archive_protection():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            profile = await create_profile("High Risk", settings={"severity_floor": "medium"})
            connection = await create_connection(
                "Org GitHub",
                platform="github",
                token="fixture-value-archive",
                sync_enabled=True,
                health_status="healthy",
            )
            ado_connection = await create_connection(
                "Org ADO",
                platform="ado",
                token="ado-fixture-value",
                org_url="https://dev.azure.com/example",
            )
            with pytest.raises(ValueError, match="platform must match"):
                await create_repo_link(
                    platform="github",
                    repo_owner="octo",
                    repo_name="wrong-credential",
                    profile_id=uuid.UUID(profile["id"]),
                    connection_id=uuid.UUID(ado_connection["id"]),
                )

            async with factory() as session:
                stored_connection = await session.get(
                    models.ConnectionRow, uuid.UUID(connection["id"])
                )
                assert stored_connection is not None
                stored_connection.token_prefix = "short"
                await session.commit()
            exposed_connection = await get_connection(uuid.UUID(connection["id"]))
            assert exposed_connection is not None
            assert exposed_connection["token_prefix"] == "****"

            link = await create_repo_link(
                platform="github",
                repo_owner="octo",
                repo_name="service",
                repo_url="https://github.com/octo/service",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
                auto_review_enabled=True,
            )

            with pytest.raises(ArchiveBlockedError, match="repo link"):
                await archive_profile(uuid.UUID(profile["id"]))
            with pytest.raises(ArchiveBlockedError, match="repo link"):
                await archive_connection(uuid.UUID(connection["id"]))

            await update_repo_link_state(uuid.UUID(link["id"]), paused=True)

            assert await archive_profile(uuid.UUID(profile["id"])) is True
            assert await archive_connection(uuid.UUID(connection["id"])) is True

            with pytest.raises(ArchiveBlockedError, match="Cannot activate repo link"):
                await update_repo_link_state(
                    uuid.UUID(link["id"]),
                    paused=False,
                    auto_review_enabled=True,
                )
    finally:
        await engine.dispose()


def test_existing_github_pats_migrate_to_connections():
    engine = sa.create_engine("sqlite:///:memory:")
    meta = sa.MetaData()
    sa.Table(
        "github_pats",
        meta,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("encrypted_token", sa.Text, nullable=False),
        sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    sa.Table(
        "connections",
        meta,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("encrypted_token", sa.Text),
        sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
        sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    sa.Table(
        "reviews",
        meta,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("pat_name", sa.String(128)),
        sa.Column("connection_snapshot", sa.JSON),
    )
    meta.create_all(engine)

    pat_id = str(uuid.uuid4())
    short_pat_id = str(uuid.uuid4())
    encrypted = encrypt("fixture-value-migrated")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO github_pats (
                    id, name, description, encrypted_token, token_prefix, is_default
                )
                VALUES (:id, 'legacy', 'Legacy PAT', :token, 'fixture-...', 1)
                """
            ),
            {"id": pat_id, "token": encrypted},
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO github_pats (
                    id, name, description, encrypted_token, token_prefix, is_default
                )
                VALUES (:id, 'legacy-short', 'Legacy short PAT', :token, 'short', 0)
                """
            ),
            {"id": short_pat_id, "token": encrypted},
        )
        conn.execute(
            sa.text("INSERT INTO reviews (id, pat_name) VALUES (:id, 'legacy')"),
            {"id": str(uuid.uuid4())},
        )
        ops = Operations(MigrationContext.configure(conn))
        with patch.object(migration, "op", ops):
            migration._migrate_github_pats_to_connections()
            migration._drop_legacy_github_pats()

        row = (
            conn.execute(sa.text("SELECT * FROM connections WHERE id = :id"), {"id": pat_id})
            .mappings()
            .one()
        )
        assert row["name"] == "legacy"
        assert row["platform"] == "github"
        assert row["encrypted_token"] == encrypted
        assert row["token_prefix"] == "fixture-..."
        assert row["health_status"] == "unknown"
        assert bool(row["sync_enabled"]) is True
        short_row = (
            conn.execute(sa.text("SELECT * FROM connections WHERE id = :id"), {"id": short_pat_id})
            .mappings()
            .one()
        )
        assert short_row["token_prefix"] == "****"
        inspector = sa.inspect(conn)
        assert "github_pats" not in inspector.get_table_names()
        assert "pat_name" not in {col["name"] for col in inspector.get_columns("reviews")}
        review = conn.execute(sa.text("SELECT connection_snapshot FROM reviews")).scalar_one()
        assert "legacy" in review
        assert not hasattr(models, "GithubPatRow")
    engine.dispose()


def test_sqlite_default_profile_seed_uses_uuid_storage_form():
    engine = sa.create_engine("sqlite:///:memory:")
    meta = sa.MetaData()
    sa.Table(
        "profiles",
        meta,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    meta.create_all(engine)

    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        with patch.object(migration, "op", ops):
            migration._seed_default_profile()

        seeded_id = conn.execute(sa.text("SELECT id FROM profiles")).scalar_one()
        assert seeded_id == migration.SQLITE_DEFAULT_PROFILE_ID
        assert "-" not in seeded_id
    engine.dispose()
