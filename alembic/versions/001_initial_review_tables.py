"""Initial review tables.

Revision ID: 001
Revises: None
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pr_id", sa.String(64), index=True, nullable=False),
        sa.Column("repo", sa.String(256), index=True, nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("author", sa.String(128), server_default=""),
        sa.Column("title", sa.Text, server_default=""),
        sa.Column("source_branch", sa.String(256), server_default=""),
        sa.Column("target_branch", sa.String(256), server_default=""),
        sa.Column("head_commit_sha", sa.String(64), server_default=""),
        sa.Column("risk_tier", sa.String(16), server_default=""),
        sa.Column("repo_risk_class", sa.String(16), server_default="standard"),
        sa.Column("combined_score", sa.Float, server_default="0"),
        sa.Column("decision", sa.String(32), server_default="pending"),
        sa.Column("mechanical_passed", sa.Boolean, server_default="true"),
        sa.Column("override_reasons", JSONB, server_default="[]"),
        sa.Column("summary", sa.Text, server_default=""),
        sa.Column("stage", sa.String(32), server_default="queued"),
        sa.Column("stage_detail", sa.Text, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
    )

    op.create_table(
        "mechanical_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "review_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("severity", sa.String(16), server_default="info"),
        sa.Column("findings", JSONB, server_default="[]"),
        sa.Column("error", sa.Text, nullable=True),
    )

    op.create_table(
        "agent_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "review_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(64), index=True, nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("languages_reviewed", JSONB, server_default="[]"),
        sa.Column("error", sa.Text, nullable=True),
    )

    op.create_table(
        "findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("certainty", sa.String(16), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("file", sa.Text, nullable=False),
        sa.Column("line", sa.Integer, nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("suggestion", sa.Text, server_default=""),
        sa.Column("cwe", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_table("agent_results")
    op.drop_table("mechanical_results")
    op.drop_table("reviews")
