"""Add ci_status, assignees to synced_prs; add excluded_repos table.

Revision ID: 015
Revises: 014
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.scalar() is not None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :table"),
        {"table": table},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("synced_prs", "ci_status"):
        op.add_column(
            "synced_prs",
            sa.Column("ci_status", sa.String(32), nullable=False, server_default="unknown"),
        )
    if not _column_exists("synced_prs", "assignees"):
        op.add_column(
            "synced_prs",
            sa.Column("assignees", JSONB, nullable=False, server_default="[]"),
        )

    if not _table_exists("excluded_repos"):
        op.create_table(
            "excluded_repos",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("org", sa.String(256), nullable=False),
            sa.Column("project", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo", sa.String(256), nullable=False),
            sa.Column("excluded_by_email", sa.String(256), nullable=False, server_default=""),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
        )
        op.create_unique_constraint(
            "uq_excluded_repo",
            "excluded_repos",
            ["platform", "org", "project", "repo"],
        )


def downgrade() -> None:
    if _table_exists("excluded_repos"):
        op.drop_table("excluded_repos")
    if _column_exists("synced_prs", "assignees"):
        op.drop_column("synced_prs", "assignees")
    if _column_exists("synced_prs", "ci_status"):
        op.drop_column("synced_prs", "ci_status")
