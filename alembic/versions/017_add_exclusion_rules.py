"""Add exclusion_rules table for wildcard repo exclusions.

Revision ID: 017
Revises: 016
Create Date: 2026-05-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables"
            " WHERE table_name = :table AND table_schema = current_schema()"
        ),
        {"table": table},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("exclusion_rules"):
        op.create_table(
            "exclusion_rules",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("org_pattern", sa.String(256), nullable=False, server_default=""),
            sa.Column("project_pattern", sa.String(256), nullable=False, server_default=""),
            sa.Column("repo_pattern", sa.String(256), nullable=False, server_default=""),
            sa.Column("created_by_email", sa.String(256), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    if _table_exists("exclusion_rules"):
        op.drop_table("exclusion_rules")
