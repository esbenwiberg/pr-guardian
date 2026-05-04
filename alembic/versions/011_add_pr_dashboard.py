"""Add PR dashboard tables: user_identities, sync_sources, synced_prs.

Revision ID: 011
Revises: 010
Create Date: 2026-05-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :table"
        ),
        {"table": table},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("user_identities"):
        op.create_table(
            "user_identities",
            sa.Column("email", sa.String(256), primary_key=True),
            sa.Column("github_handle", sa.String(128), nullable=True),
            sa.Column("ado_upn", sa.String(256), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _table_exists("sync_sources"):
        op.create_table(
            "sync_sources",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("org", sa.String(256), nullable=False),
            sa.Column("project", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo", sa.String(256), nullable=False),
            sa.Column("repo_url", sa.Text, nullable=False, server_default=""),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_sync_sources_platform", "sync_sources", ["platform"])

    if not _table_exists("synced_prs"):
        op.create_table(
            "synced_prs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("pr_id", sa.String(64), nullable=False),
            sa.Column("org", sa.String(256), nullable=False),
            sa.Column("project", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo", sa.String(256), nullable=False),
            sa.Column("title", sa.Text, nullable=False, server_default=""),
            sa.Column("author", sa.String(256), nullable=False, server_default=""),
            sa.Column("author_display", sa.String(256), nullable=False, server_default=""),
            sa.Column("pr_url", sa.Text, nullable=False, server_default=""),
            sa.Column("source_branch", sa.String(256), nullable=False, server_default=""),
            sa.Column("target_branch", sa.String(256), nullable=False, server_default=""),
            sa.Column("is_draft", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("has_conflicts", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("approval_status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("reviewers", JSONB, nullable=False, server_default="[]"),
            sa.Column("comment_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("pr_created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("pr_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_synced_prs_platform", "synced_prs", ["platform"])
        op.create_index("ix_synced_prs_org", "synced_prs", ["org"])
        op.create_index("ix_synced_prs_repo", "synced_prs", ["repo"])
        op.create_index("ix_synced_prs_author", "synced_prs", ["author"])
        op.create_unique_constraint(
            "uq_synced_pr", "synced_prs", ["platform", "pr_id", "repo", "project"]
        )


def downgrade() -> None:
    if _table_exists("synced_prs"):
        op.drop_table("synced_prs")
    if _table_exists("sync_sources"):
        op.drop_table("sync_sources")
    if _table_exists("user_identities"):
        op.drop_table("user_identities")
