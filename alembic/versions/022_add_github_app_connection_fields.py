"""Add GitHub App Connection fields to connections table.

Revision ID: 022
Revises: 021
Create Date: 2026-06-06

Adds auth_kind, app_id, app_slug, installation_id, installation_account,
installation_target_type, encrypted_private_key, private_key_fingerprint,
and app_permissions to the connections table.

Existing GitHub PAT rows retain their encrypted_token values; auth_kind
is NULL for them. Operators who want GitHub App authentication must create
new Connections with auth_kind='github_app'.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column("connections", sa.Column("auth_kind", sa.String(32), nullable=True))
    op.add_column("connections", sa.Column("app_id", sa.String(64), nullable=True))
    op.add_column("connections", sa.Column("app_slug", sa.String(128), nullable=True))
    op.add_column("connections", sa.Column("installation_id", sa.String(64), nullable=True))
    op.add_column("connections", sa.Column("installation_account", sa.String(256), nullable=True))
    op.add_column(
        "connections", sa.Column("installation_target_type", sa.String(32), nullable=True)
    )
    op.add_column("connections", sa.Column("encrypted_private_key", sa.Text(), nullable=True))
    op.add_column(
        "connections", sa.Column("private_key_fingerprint", sa.String(128), nullable=True)
    )
    op.add_column("connections", sa.Column("app_permissions", _json_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("connections", "app_permissions")
    op.drop_column("connections", "private_key_fingerprint")
    op.drop_column("connections", "encrypted_private_key")
    op.drop_column("connections", "installation_target_type")
    op.drop_column("connections", "installation_account")
    op.drop_column("connections", "installation_id")
    op.drop_column("connections", "app_slug")
    op.drop_column("connections", "app_id")
    op.drop_column("connections", "auth_kind")
