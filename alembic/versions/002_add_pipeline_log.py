"""Add pipeline_log and cost tracking columns to reviews table.

Revision ID: 002
Revises: 001
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("pipeline_log", JSONB, server_default="[]"))
    op.add_column("reviews", sa.Column("total_input_tokens", sa.Integer, server_default="0"))
    op.add_column("reviews", sa.Column("total_output_tokens", sa.Integer, server_default="0"))
    op.add_column("reviews", sa.Column("cost_usd", sa.Float, server_default="0"))


def downgrade() -> None:
    op.drop_column("reviews", "cost_usd")
    op.drop_column("reviews", "total_output_tokens")
    op.drop_column("reviews", "total_input_tokens")
    op.drop_column("reviews", "pipeline_log")
