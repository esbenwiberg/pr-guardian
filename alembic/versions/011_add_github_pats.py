"""Add github_pats table for named encrypted PAT storage.

Revision ID: 011
Revises: 010
Create Date: 2026-05-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :table"),
        {"table": table},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _table_exists("github_pats"):
        op.create_table(
            "github_pats",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(256), nullable=False, server_default=""),
            sa.Column("encrypted_token", sa.Text, nullable=False),
            sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_github_pats_name", "github_pats", ["name"], unique=True)


def downgrade() -> None:
    if _table_exists("github_pats"):
        op.drop_index("ix_github_pats_name", "github_pats")
        op.drop_table("github_pats")
