"""widen scan_findings free-text columns

`scan_findings.category` and `effort_estimate` hold LLM-generated free text but
were capped at varchar(64)/varchar(16). A scan agent emitted a category longer
than 64 chars, so the INSERT raised StringDataRightTruncationError and rolled
back the WHOLE scan save — 11 findings persisted as 0, the scan stuck at
`scan_report` with no agent results. Widen both to TEXT (matching `file` /
`description` / `suggestion`) so LLM output can't truncate-crash the save.

Revision ID: 005
Revises: 004
Create Date: 2026-06-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WIDEN = ("category", "effort_estimate")


def upgrade() -> None:
    # Idempotent: only alter columns still on a length-capped string type, so the
    # migration converges whether or not startup create_all already built a fresh
    # schema (where the model is already TEXT).
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"]: c["type"] for c in insp.get_columns("scan_findings")}
    for name in _WIDEN:
        col_type = cols.get(name)
        if col_type is not None and getattr(col_type, "length", None) is not None:
            op.alter_column(
                "scan_findings",
                name,
                type_=sa.Text(),
                existing_nullable=(name == "effort_estimate"),
            )


def downgrade() -> None:
    op.alter_column(
        "scan_findings", "effort_estimate", type_=sa.String(length=16), existing_nullable=True
    )
    op.alter_column(
        "scan_findings", "category", type_=sa.String(length=64), existing_nullable=False
    )
