"""Add partial unique index enforcing at most one default GitHub PAT.

Revision ID: 012
Revises: 011
Create Date: 2026-05-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :idx"),
        {"idx": index_name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _index_exists("uq_github_pats_single_default"):
        op.create_index(
            "uq_github_pats_single_default",
            "github_pats",
            ["is_default"],
            unique=True,
            postgresql_where=sa.text("is_default = TRUE"),
        )


def downgrade() -> None:
    if _index_exists("uq_github_pats_single_default"):
        op.drop_index("uq_github_pats_single_default", table_name="github_pats")
