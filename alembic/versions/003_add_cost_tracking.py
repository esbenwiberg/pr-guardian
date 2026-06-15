"""Add cost tracking columns to reviews table.

Revision ID: 003
Revises: 002
Create Date: 2026-03-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migration 002 already adds these cost-tracking columns. 003 originally
    # re-added the same three, which crashed `alembic upgrade head` on a fresh DB
    # with DuplicateColumnError. Guard each add so 003 is a catch-up no-op for any
    # DB that already has the columns, while still adding them for a DB that ran an
    # older 002 (pipeline_log only) before the cost columns were folded into it.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("reviews")}
    if "total_input_tokens" not in existing:
        op.add_column("reviews", sa.Column("total_input_tokens", sa.Integer, server_default="0"))
    if "total_output_tokens" not in existing:
        op.add_column("reviews", sa.Column("total_output_tokens", sa.Integer, server_default="0"))
    if "cost_usd" not in existing:
        op.add_column("reviews", sa.Column("cost_usd", sa.Float, server_default="0"))


def downgrade() -> None:
    # No-op: migration 002 owns these columns and drops them on its own
    # downgrade. Dropping them here too would double-drop when downgrading
    # through 002 -> 001.
    pass
