"""Add pr_url column to reviews table.

Revision ID: 004
Revises: 003
Create Date: 2026-03-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("pr_url", sa.Text, server_default=""))


def downgrade() -> None:
    op.drop_column("reviews", "pr_url")
