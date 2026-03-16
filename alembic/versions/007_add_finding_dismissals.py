"""Add finding_dismissals table for the feedback loop.

Authors can dismiss/comment on findings in the dashboard and trigger
re-reviews that feed dismissal context into agent prompts.

Revision ID: 007
Revises: 006
Create Date: 2026-03-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finding_dismissals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pr_id", sa.String(64), nullable=False, index=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("signature", sa.String(16), nullable=False, index=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("comment", sa.Text, server_default=""),
        sa.Column("source_finding", JSONB, server_default="{}"),
        sa.Column("active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_dismissals_pr_lookup",
        "finding_dismissals",
        ["repo", "pr_id", "platform", "active"],
    )
    op.create_index(
        "ix_dismissals_sig_match",
        "finding_dismissals",
        ["signature", "repo", "pr_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dismissals_sig_match", table_name="finding_dismissals")
    op.drop_index("ix_dismissals_pr_lookup", table_name="finding_dismissals")
    op.drop_table("finding_dismissals")
