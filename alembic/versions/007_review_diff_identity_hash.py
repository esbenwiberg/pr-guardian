"""add reviews.diff_identity_hash

Records the SHA-256 of a review's net three-dot diff (see ``Diff.identity_hash``)
so readiness can carry a prior auto-approve forward when a pure base-merge
("Update branch") produces a new head SHA but byte-identical reviewable content.
Without it, every base-merge re-arms ``guardian/review`` to needs-human-review and,
under ``strict`` branch protection, spins an update-branch → re-review treadmill
(issue #97).

Revision ID: 007
Revises: 006
Create Date: 2026-07-01

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: startup create_all may already have built the column on a fresh
    # schema, so only add it when missing (converges either way).
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("reviews")}
    if "diff_identity_hash" not in cols:
        op.add_column(
            "reviews",
            sa.Column(
                "diff_identity_hash",
                sa.String(length=64),
                nullable=False,
                server_default="",
            ),
        )
        op.create_index("ix_reviews_diff_identity_hash", "reviews", ["diff_identity_hash"])


def downgrade() -> None:
    op.drop_index("ix_reviews_diff_identity_hash", table_name="reviews")
    op.drop_column("reviews", "diff_identity_hash")
