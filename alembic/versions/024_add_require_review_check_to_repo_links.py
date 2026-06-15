"""Add require_review_check column to repo_links.

Revision ID: 024
Revises: 023
Create Date: 2026-06-15

Adds:
- require_review_check boolean on repo_links: whether linking / enabling
  auto-review enforces the guardian/review branch-protection merge gate.
  Defaults to true so existing links keep gate enforcement.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "repo_links",
        sa.Column(
            "require_review_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("repo_links", "require_review_check")
