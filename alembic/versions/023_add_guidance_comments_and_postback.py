"""Add guidance_comments table and postback_meta column to reviews.

Revision ID: 023
Revises: 022
Create Date: 2026-06-07

Adds:
- guidance_comments table: tracks the sticky guidance comment ID posted per PR
- postback_meta JSON column on reviews: records which platform side effects ran
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "guidance_comments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("pr_id", sa.String(64), nullable=False),
        sa.Column("comment_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "repo", "pr_id", name="uq_guidance_comment_pr"),
    )
    op.create_index("ix_guidance_comments_platform", "guidance_comments", ["platform"])
    op.create_index("ix_guidance_comments_repo", "guidance_comments", ["repo"])
    op.create_index("ix_guidance_comments_pr_id", "guidance_comments", ["pr_id"])

    op.add_column("reviews", sa.Column("postback_meta", _json_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "postback_meta")
    op.drop_index("ix_guidance_comments_pr_id", table_name="guidance_comments")
    op.drop_index("ix_guidance_comments_repo", table_name="guidance_comments")
    op.drop_index("ix_guidance_comments_platform", table_name="guidance_comments")
    op.drop_table("guidance_comments")
