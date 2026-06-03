"""Normalize ADO repo-link canonical keys where project was stored as a full URL.

Users sometimes paste the browser URL (https://dev.azure.com/org/ProjectName) into
the project field rather than just the short project name. This migration strips the
org-URL prefix and URL-decodes so every existing row matches what the sync loop now
generates via _normalize_ado_project().

Revision ID: 020
Revises: 019
Create Date: 2026-06-03
"""

from typing import Sequence, Union
from urllib.parse import unquote

from alembic import op
from sqlalchemy import text

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_project(project: str, org_url: str) -> str:
    stripped = project.strip()
    if not stripped.lower().startswith(("http://", "https://")):
        return stripped
    decoded = unquote(stripped)
    clean_org = org_url.lower().rstrip("/")
    if clean_org and decoded.lower().startswith(clean_org + "/"):
        return decoded[len(clean_org) + 1 :].strip("/")
    return decoded.rstrip("/").split("/")[-1]


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, org_url, project, repo_name FROM repo_links"
            " WHERE platform = 'ado' AND project LIKE 'http%'"
        )
    ).fetchall()

    for row in rows:
        org_url = (row.org_url or "").rstrip("/")
        norm_project = _normalize_project(row.project or "", org_url)
        canonical = (
            f"ado:{org_url.lower()}:{norm_project.lower().strip()}"
            f"/{(row.repo_name or '').lower().strip()}"
        )
        conn.execute(
            text(
                "UPDATE repo_links SET canonical_repo_key = :canonical,"
                " project = :project WHERE id = :id"
            ),
            {"canonical": canonical, "project": norm_project, "id": str(row.id)},
        )


def downgrade() -> None:
    # Normalization is lossy (we discard the URL prefix); no safe rollback.
    pass
