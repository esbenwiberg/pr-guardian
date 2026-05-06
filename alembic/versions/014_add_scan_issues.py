"""Add scan_issues table for tracking platform issues created from scan findings.

Revision ID: 014
Revises: 013
Create Date: 2026-05-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "014"
down_revision: Union[str, None] = "013"
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
    if not _table_exists("scan_issues"):
        op.create_table(
            "scan_issues",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "scan_id",
                UUID(as_uuid=True),
                sa.ForeignKey("scans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("finding_ids", JSONB, nullable=False, server_default="[]"),
            sa.Column("issue_url", sa.Text, nullable=False, server_default=""),
            sa.Column("issue_number", sa.String(32), nullable=False, server_default=""),
            sa.Column("title", sa.Text, nullable=False, server_default=""),
            sa.Column("platform", sa.String(16), nullable=False, server_default=""),
            sa.Column("repo", sa.String(256), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_scan_issues_scan_id", "scan_issues", ["scan_id"])


def downgrade() -> None:
    if _table_exists("scan_issues"):
        op.drop_table("scan_issues")
