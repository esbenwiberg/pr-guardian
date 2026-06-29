"""scan commit-range scope columns

Add `base_sha` / `head_sha` to `scans`. A recent_changes scan can now run over an
explicit `base..head` commit range (e.g. a nightly "everything since last night"
sweep) instead of only a time window. Recording the range makes a range scan
replayable and auditable; time-window scans leave both empty.

Revision ID: 004
Revises: 003
Create Date: 2026-06-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: startup create_all may add the columns from the model before
    # Alembic records this revision. Guard on the live schema so the migration
    # converges whether or not the reconcile already ran.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("scans")}
    if "base_sha" not in columns:
        op.add_column(
            "scans",
            sa.Column("base_sha", sa.String(length=64), nullable=False, server_default=""),
        )
    if "head_sha" not in columns:
        op.add_column(
            "scans",
            sa.Column("head_sha", sa.String(length=64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("scans", "head_sha")
    op.drop_column("scans", "base_sha")
