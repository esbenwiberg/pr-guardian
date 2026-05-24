"""Add finding lifecycle columns to finding_dismissals.

Revision ID: 018
Revises: 017
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "finding_dismissals"
_LIFECYCLE_COLUMNS = [
    ("resolution_kind", sa.String(24)),
    ("fixed_by_sha", sa.String(64)),
    ("fixed_at", sa.DateTime(timezone=True)),
    ("verified_by", sa.String(256)),
    ("verified_at", sa.DateTime(timezone=True)),
    ("regressed_at", sa.DateTime(timezone=True)),
    ("regressed_from_sha", sa.String(64)),
]


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = :table AND column_name = :column"
            " AND table_schema = current_schema()"
        ),
        {"table": table, "column": column},
    )
    return result.scalar() is not None


def upgrade() -> None:
    for col_name, col_type in _LIFECYCLE_COLUMNS:
        if not _column_exists(_TABLE, col_name):
            op.add_column(_TABLE, sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    for col_name, _ in _LIFECYCLE_COLUMNS:
        if _column_exists(_TABLE, col_name):
            op.drop_column(_TABLE, col_name)
