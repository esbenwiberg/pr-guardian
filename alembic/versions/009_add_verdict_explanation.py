"""Add verdict_explanation column to agent_results table.

Stores a brief LLM-generated explanation of why the agent chose its verdict,
helping human reviewers understand what to focus on.

Revision ID: 009
Revises: 008
Create Date: 2026-04-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
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


def upgrade() -> None:
    if not _column_exists("agent_results", "verdict_explanation"):
        op.add_column(
            "agent_results",
            sa.Column("verdict_explanation", sa.Text, nullable=True),
        )


def downgrade() -> None:
    op.drop_column("agent_results", "verdict_explanation")
