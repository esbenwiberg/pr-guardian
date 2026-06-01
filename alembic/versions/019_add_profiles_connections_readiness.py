"""Add profiles, connections, repo links, readiness candidates.

Revision ID: 019
Revises: 018
Create Date: 2026-05-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_PROFILE_ID = "00000000-0000-0000-0000-000000000001"
SQLITE_DEFAULT_PROFILE_ID = DEFAULT_PROFILE_ID.replace("-", "")


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(JSONB, "postgresql")


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    return table in sa.inspect(conn).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    if not _table_exists(table):
        return False
    return column in {col["name"] for col in sa.inspect(conn).get_columns(table)}


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    for table in sa.inspect(conn).get_table_names():
        if any(index["name"] == index_name for index in sa.inspect(conn).get_indexes(table)):
            return True
    return False


def _add_column(table: str, column: sa.Column) -> None:
    if _table_exists(table) and not _column_exists(table, column.name):
        op.add_column(table, column)


def _nullable_uuid_reference(column_name: str, target: str) -> sa.Column:
    if op.get_bind().dialect.name == "sqlite":
        return sa.Column(column_name, UUID(as_uuid=True), nullable=True)
    return sa.Column(column_name, UUID(as_uuid=True), sa.ForeignKey(target), nullable=True)


def _create_profiles() -> None:
    if _table_exists("profiles"):
        return
    op.create_table(
        "profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("settings", _json_type(), nullable=False, server_default="{}"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_profiles_name", "profiles", ["name"], unique=True)


def _create_connections() -> None:
    if _table_exists("connections"):
        return
    op.create_table(
        "connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("org_url", sa.Text, nullable=True),
        sa.Column("encrypted_token", sa.Text, nullable=True),
        sa.Column("token_secret_ref", sa.Text, nullable=True),
        sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
        sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("health_message", sa.Text, nullable=False, server_default=""),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("platform in ('github', 'ado')", name="ck_connections_platform"),
        sa.CheckConstraint(
            "health_status in ('unknown', 'healthy', 'unhealthy')",
            name="ck_connections_health_status",
        ),
    )
    op.create_index("ix_connections_name", "connections", ["name"], unique=True)
    op.create_index("ix_connections_platform", "connections", ["platform"])
    op.create_index(
        "uq_connections_single_default_github",
        "connections",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text(
            "platform = 'github' AND is_default = TRUE AND archived_at IS NULL"
        ),
        sqlite_where=sa.text("platform = 'github' AND is_default = 1 AND archived_at IS NULL"),
    )


def _create_repo_links() -> None:
    if _table_exists("repo_links"):
        return
    op.create_table(
        "repo_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("org_url", sa.Text, nullable=False, server_default=""),
        sa.Column("project", sa.String(256), nullable=False, server_default=""),
        sa.Column("repo_owner", sa.String(256), nullable=False, server_default=""),
        sa.Column("repo_name", sa.String(256), nullable=False),
        sa.Column("repo_url", sa.Text, nullable=False, server_default=""),
        sa.Column("canonical_repo_key", sa.String(512), nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("auto_review_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("paused", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_repo_links_platform", "repo_links", ["platform"])
    op.create_index("ix_repo_links_canonical_repo_key", "repo_links", ["canonical_repo_key"])
    op.create_index(
        "uq_repo_links_active_canonical",
        "repo_links",
        ["platform", "canonical_repo_key"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )


def _create_profile_managers_and_audit() -> None:
    if not _table_exists("profile_managers"):
        op.create_table(
            "profile_managers",
            sa.Column("email", sa.String(256), primary_key=True),
            sa.Column("added_by", sa.String(256), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not _table_exists("profile_audit_events"):
        op.create_table(
            "profile_audit_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("actor", sa.String(256), nullable=False, server_default="system"),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("target_type", sa.String(64), nullable=False),
            sa.Column("target_id", UUID(as_uuid=True), nullable=True),
            sa.Column("before", _json_type(), nullable=True),
            sa.Column("after", _json_type(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def _create_readiness_candidates() -> None:
    if not _table_exists("readiness_candidates"):
        op.create_table(
            "readiness_candidates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "repo_link_id", UUID(as_uuid=True), sa.ForeignKey("repo_links.id"), nullable=False
            ),
            sa.Column(
                "profile_id", UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=True
            ),
            sa.Column(
                "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=True
            ),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("org_url", sa.Text, nullable=False, server_default=""),
            sa.Column("project", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo_owner", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo_name", sa.String(256), nullable=False),
            sa.Column("repo", sa.String(512), nullable=False),
            sa.Column("canonical_repo_key", sa.String(512), nullable=False),
            sa.Column("pr_id", sa.String(64), nullable=False),
            sa.Column("pr_url", sa.Text, nullable=False, server_default=""),
            sa.Column("head_sha", sa.String(64), nullable=False),
            sa.Column("state", sa.String(16), nullable=False, server_default="waiting"),
            sa.Column("reason", sa.String(128), nullable=False, server_default=""),
            sa.Column("readiness_snapshot", _json_type(), nullable=False, server_default="{}"),
            sa.Column("profile_snapshot", _json_type(), nullable=True),
            sa.Column("connection_snapshot", _json_type(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint(
                "state in ('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
                name="ck_readiness_candidates_state",
            ),
            sa.UniqueConstraint(
                "platform",
                "canonical_repo_key",
                "pr_id",
                "head_sha",
                name="uq_readiness_candidate_sha",
            ),
        )
        op.create_index(
            "ix_readiness_candidates_repo_link_id", "readiness_candidates", ["repo_link_id"]
        )
        op.create_index("ix_readiness_candidates_platform", "readiness_candidates", ["platform"])
        op.create_index("ix_readiness_candidates_repo", "readiness_candidates", ["repo"])
        op.create_index(
            "ix_readiness_candidates_canonical_repo_key",
            "readiness_candidates",
            ["canonical_repo_key"],
        )
        op.create_index("ix_readiness_candidates_pr_id", "readiness_candidates", ["pr_id"])
        op.create_index("ix_readiness_candidates_head_sha", "readiness_candidates", ["head_sha"])

    if not _table_exists("readiness_candidate_transitions"):
        op.create_table(
            "readiness_candidate_transitions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "candidate_id",
                UUID(as_uuid=True),
                sa.ForeignKey("readiness_candidates.id"),
                nullable=False,
            ),
            sa.Column("from_state", sa.String(16), nullable=True),
            sa.Column("to_state", sa.String(16), nullable=False),
            sa.Column("source", sa.String(64), nullable=False, server_default=""),
            sa.Column("actor", sa.String(256), nullable=False, server_default=""),
            sa.Column("reason", sa.String(128), nullable=False, server_default=""),
            sa.Column("readiness_snapshot", _json_type(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint(
                "from_state is null or from_state in "
                "('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
                name="ck_readiness_transitions_from_state",
            ),
            sa.CheckConstraint(
                "to_state in ('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
                name="ck_readiness_transitions_to_state",
            ),
        )
        op.create_index(
            "ix_readiness_candidate_transitions_candidate_id",
            "readiness_candidate_transitions",
            ["candidate_id"],
        )


def _seed_default_profile() -> None:
    conn = op.get_bind()
    settings = (
        '{"guardian_clearance": false, "platform_approval_enabled": false, '
        '"side_effects": {"comments": false, "labels": false, '
        '"reviewers": false, "formal_approve": false, '
        '"formal_request_changes": false, "scan_issues": false}, '
        '"readiness": {"quiet_period_seconds": 10, "max_wait_minutes": 30, '
        '"archmap_max_wait_minutes": 10, "ignored_statuses": [], '
        '"ignored_checks": [], "archmap_expected": false}}'
    )
    if conn.dialect.name == "sqlite":
        conn.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO profiles (
                    id, name, description, settings, is_system, is_default,
                    created_by, updated_by, created_at, updated_at
                )
                VALUES (
                    :id, 'Default / noop',
                    'System default profile for unlinked manual work.',
                    :settings, 1, 1, 'system', 'system', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": SQLITE_DEFAULT_PROFILE_ID, "settings": settings},
        )
    else:
        conn.execute(
            sa.text(
                """
                INSERT INTO profiles (
                    id, name, description, settings, is_system, is_default,
                    created_by, updated_by, created_at, updated_at
                )
                VALUES (
                    :id, 'Default / noop',
                    'System default profile for unlinked manual work.',
                    CAST(:settings AS jsonb), TRUE, TRUE, 'system', 'system', now(), now()
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": DEFAULT_PROFILE_ID, "settings": settings},
        )


def _migrate_github_pats_to_connections() -> None:
    if not _table_exists("github_pats"):
        return
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        conn.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO connections (
                    id, name, description, platform, encrypted_token, token_prefix,
                    health_status, sync_enabled, is_default, created_by, updated_by,
                    created_at, updated_at
                )
                SELECT
                    id, name, description, 'github', encrypted_token,
                    CASE
                        WHEN token_prefix IS NULL OR token_prefix = '' THEN ''
                        WHEN token_prefix = '****' OR token_prefix LIKE '%...' THEN token_prefix
                        WHEN length(token_prefix) <= 8 THEN '****'
                        ELSE substr(token_prefix, 1, 8) || '...'
                    END,
                    'unknown', 1, is_default, 'migration', 'migration',
                    created_at, updated_at
                FROM github_pats
                """
            )
        )
    else:
        conn.execute(
            sa.text(
                """
                INSERT INTO connections (
                    id, name, description, platform, encrypted_token, token_prefix,
                    health_status, sync_enabled, is_default, created_by, updated_by,
                    created_at, updated_at
                )
                SELECT
                    id, name, description, 'github', encrypted_token,
                    CASE
                        WHEN token_prefix IS NULL OR token_prefix = '' THEN ''
                        WHEN token_prefix = '****' OR token_prefix LIKE '%...' THEN token_prefix
                        WHEN length(token_prefix) <= 8 THEN '****'
                        ELSE substr(token_prefix, 1, 8) || '...'
                    END,
                    'unknown', TRUE, is_default, 'migration', 'migration',
                    created_at, updated_at
                FROM github_pats
                ON CONFLICT (id) DO NOTHING
                """
            )
        )
    if _column_exists("reviews", "pat_name") and _column_exists("reviews", "connection_snapshot"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                sa.text(
                    """
                    UPDATE reviews
                    SET connection_snapshot = json_object('legacy_pat_name', pat_name)
                    WHERE pat_name IS NOT NULL AND connection_snapshot IS NULL
                    """
                )
            )
        else:
            conn.execute(
                sa.text(
                    """
                    UPDATE reviews
                    SET connection_snapshot = jsonb_build_object('legacy_pat_name', pat_name)
                    WHERE pat_name IS NOT NULL AND connection_snapshot IS NULL
                    """
                )
            )


def _add_provenance_columns() -> None:
    _add_column("reviews", _nullable_uuid_reference("profile_id", "profiles.id"))
    _add_column("reviews", sa.Column("profile_snapshot", _json_type(), nullable=True))
    _add_column("reviews", _nullable_uuid_reference("connection_id", "connections.id"))
    _add_column("reviews", sa.Column("connection_snapshot", _json_type(), nullable=True))
    _add_column("reviews", _nullable_uuid_reference("repo_link_id", "repo_links.id"))
    _add_column("reviews", _nullable_uuid_reference("candidate_id", "readiness_candidates.id"))
    _add_column(
        "reviews",
        sa.Column("review_source", sa.String(32), nullable=False, server_default="manual"),
    )

    _add_column("scans", _nullable_uuid_reference("profile_id", "profiles.id"))
    _add_column("scans", sa.Column("profile_snapshot", _json_type(), nullable=True))
    _add_column("scans", _nullable_uuid_reference("connection_id", "connections.id"))
    _add_column("scans", sa.Column("connection_snapshot", _json_type(), nullable=True))
    _add_column("scans", _nullable_uuid_reference("repo_link_id", "repo_links.id"))
    _add_column(
        "scans", sa.Column("scan_source", sa.String(32), nullable=False, server_default="scan")
    )

    _add_column("sync_sources", _nullable_uuid_reference("connection_id", "connections.id"))
    _add_column("sync_sources", sa.Column("connection_snapshot", _json_type(), nullable=True))
    _add_column("synced_prs", _nullable_uuid_reference("profile_id", "profiles.id"))
    _add_column("synced_prs", sa.Column("profile_snapshot", _json_type(), nullable=True))
    _add_column("synced_prs", _nullable_uuid_reference("connection_id", "connections.id"))
    _add_column("synced_prs", sa.Column("connection_snapshot", _json_type(), nullable=True))
    _add_column("synced_prs", _nullable_uuid_reference("repo_link_id", "repo_links.id"))
    _add_column(
        "synced_prs",
        sa.Column("sync_source", sa.String(32), nullable=False, server_default="sync"),
    )


def _drop_legacy_github_pats() -> None:
    if _column_exists("reviews", "pat_name"):
        op.drop_column("reviews", "pat_name")
    if _table_exists("github_pats"):
        if _index_exists("uq_github_pats_single_default"):
            op.drop_index("uq_github_pats_single_default", table_name="github_pats")
        if _index_exists("ix_github_pats_name"):
            op.drop_index("ix_github_pats_name", table_name="github_pats")
        op.drop_table("github_pats")


def upgrade() -> None:
    _create_profiles()
    _create_connections()
    _seed_default_profile()
    _create_repo_links()
    _create_profile_managers_and_audit()
    _create_readiness_candidates()
    _add_provenance_columns()
    _migrate_github_pats_to_connections()
    _drop_legacy_github_pats()


def downgrade() -> None:
    raise RuntimeError("019 is data-preserving and intentionally not downgraded")
