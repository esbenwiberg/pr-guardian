"""Widen findings.category from 64 to 128 chars.

LLM-generated category strings can exceed 64 characters, causing
StringDataRightTruncationError and leaving reviews stuck as zombie
records in the dashboard.

Revision ID: 006
Revises: 005
Create Date: 2026-03-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "findings",
        "category",
        existing_type=sa.String(64),
        type_=sa.String(128),
    )


def downgrade() -> None:
    op.alter_column(
        "findings",
        "category",
        existing_type=sa.String(128),
        type_=sa.String(64),
    )
