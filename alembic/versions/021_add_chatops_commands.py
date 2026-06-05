"""Add ChatOps command idempotency table.

Revision ID: 021
Revises: 020
Create Date: 2026-06-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "chatops_commands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("pr_id", sa.String(64), nullable=False),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default=""),
        sa.Column("actor", sa.String(256), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="claimed"),
        sa.Column("status_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", _json_type(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "platform",
            "external_id",
            "command",
            name="uq_chatops_command_external",
        ),
    )
    op.create_index("ix_chatops_commands_platform", "chatops_commands", ["platform"])
    op.create_index("ix_chatops_commands_repo", "chatops_commands", ["repo"])
    op.create_index("ix_chatops_commands_pr_id", "chatops_commands", ["pr_id"])


def downgrade() -> None:
    op.drop_index("ix_chatops_commands_pr_id", table_name="chatops_commands")
    op.drop_index("ix_chatops_commands_repo", table_name="chatops_commands")
    op.drop_index("ix_chatops_commands_platform", table_name="chatops_commands")
    op.drop_table("chatops_commands")
