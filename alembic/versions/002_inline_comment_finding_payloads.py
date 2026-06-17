"""inline comment finding payloads

Add a `findings` JSON column to `posted_inline_comments` so each posted Guardian
inline comment remembers the finding payload(s) it carried. This is what lets a
reply to that comment (`@guardian dismiss <status>: reason`) be mapped back to the
specific finding(s) and recorded as a dismissal. Also index `platform_comment_id`
for the reply lookup.

Revision ID: 002
Revises: 001
Create Date: 2026-06-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posted_inline_comments",
        sa.Column(
            "findings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        op.f("ix_posted_inline_comments_platform_comment_id"),
        "posted_inline_comments",
        ["platform_comment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_posted_inline_comments_platform_comment_id"),
        table_name="posted_inline_comments",
    )
    op.drop_column("posted_inline_comments", "findings")
