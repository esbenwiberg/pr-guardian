"""widen scan_agent_results.agent_name to TEXT

`scan_agent_results.agent_name` was capped at varchar(64). PR-review agents use
a fixed short name, but the deep ("fat nightly") scan repurposes this column as a
per-PR identity — ``PR #<n>: <title>`` — which routinely exceeds 64 chars. A long
PR title raised StringDataRightTruncationError on INSERT and rolled back the
WHOLE deep-scan save: the scan stuck at `scan_report` with 0 agent results and 0
findings persisted. Same failure mode (and fix) as migration 005 for
`scan_findings.category`. Widen to TEXT so a PR title can't truncate-crash the
save.

The index on the column is preserved; PR titles are far under the btree key
limit.

Revision ID: 006
Revises: 005
Create Date: 2026-06-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: only alter the column while it is still a length-capped string,
    # so the migration converges whether or not startup create_all already built a
    # fresh schema (where the model is already TEXT).
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"]: c["type"] for c in insp.get_columns("scan_agent_results")}
    col_type = cols.get("agent_name")
    if col_type is not None and getattr(col_type, "length", None) is not None:
        op.alter_column(
            "scan_agent_results",
            "agent_name",
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.alter_column(
        "scan_agent_results", "agent_name", type_=sa.String(length=64), existing_nullable=False
    )
