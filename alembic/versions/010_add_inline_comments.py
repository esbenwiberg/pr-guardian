"""Add comment_mode to reviews and create posted_inline_comments table.

Revision ID: 010
Revises: 009
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "010"
down_revision: Union[str, None] = "009"
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


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :table"
        ),
        {"table": table},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("reviews", "comment_mode"):
        op.add_column(
            "reviews",
            sa.Column("comment_mode", sa.String(32), server_default="none", nullable=False),
        )

    if not _table_exists("posted_inline_comments"):
        op.create_table(
            "posted_inline_comments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("review_id", UUID(as_uuid=True),
                      sa.ForeignKey("reviews.id"), nullable=False),
            sa.Column("platform_comment_id", sa.String(256), nullable=False),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("pr_id", sa.String(64), nullable=False),
            sa.Column("repo", sa.String(256), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index(
            "ix_posted_inline_comments_review_id",
            "posted_inline_comments",
            ["review_id"],
        )


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
        {"name": index_name},
    )
    return result.scalar() is not None


def downgrade() -> None:
    if _table_exists("posted_inline_comments"):
        if _index_exists("ix_posted_inline_comments_review_id"):
            op.drop_index("ix_posted_inline_comments_review_id", "posted_inline_comments")
        op.drop_table("posted_inline_comments")
    if _column_exists("reviews", "comment_mode"):
        op.drop_column("reviews", "comment_mode")
