"""readiness candidate readiness_synced flag

Add a `readiness_synced` boolean to `readiness_candidates`. A completed review
re-asserts `guardian/readiness=success`, but that write is best-effort: if it
fails, the candidate goes terminal (`reviewed`) and is never re-evaluated, so
the readiness check stays stranded at `pending` forever. The readiness
reconciler now re-asserts `readiness=success` for *reviewed* candidates whose
flag is unset, flipping it once the write is confirmed so it fires exactly once.

Existing reviewed rows are back-filled to `true`: their readiness check already
settled (or can be unstuck with a re-review), and marking them synced avoids a
deploy-time burst of redundant status writes across the whole reviewed backlog.

Revision ID: 003
Revises: 002
Create Date: 2026-06-22

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: startup create_all/reconcile can add the column from the model
    # before Alembic records this revision. Guard on the live schema so this
    # migration converges whether or not the reconcile already ran.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("readiness_candidates")}
    if "readiness_synced" not in columns:
        op.add_column(
            "readiness_candidates",
            sa.Column(
                "readiness_synced",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        # Back-fill existing reviewed candidates as already synced so the
        # reconciler doesn't re-post success across the historical backlog.
        op.execute(
            "UPDATE readiness_candidates SET readiness_synced = true "
            "WHERE state = 'reviewed'"
        )


def downgrade() -> None:
    op.drop_column("readiness_candidates", "readiness_synced")
