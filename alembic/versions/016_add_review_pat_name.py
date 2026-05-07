"""Add pat_name column to reviews table to track which GitHub PAT was used.

Revision ID: 016
Revises: 015
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("pat_name", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reviews", "pat_name")
