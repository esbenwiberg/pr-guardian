"""Add trust_tier, trust_tier_details, and repo_risk_class columns.

Revision ID: 005
Revises: 004
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("repo_risk_class", sa.String(16), server_default="standard"))
    op.add_column("reviews", sa.Column("trust_tier", sa.String(32), server_default=""))
    op.add_column("reviews", sa.Column("trust_tier_details", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "trust_tier_details")
    op.drop_column("reviews", "trust_tier")
    op.drop_column("reviews", "repo_risk_class")
