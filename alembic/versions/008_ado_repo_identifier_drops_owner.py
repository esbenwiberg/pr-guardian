"""strip the owner prefix from ADO readiness candidate repo identifiers

``create_readiness_candidate`` built every candidate's ``repo`` as
``f"{repo_owner}/{repo_name}"`` regardless of platform. That is the GitHub
convention. ADO addresses a repo as
``{org}/{project}/_apis/git/repositories/{repo}`` with no owner, so an ADO
candidate carrying one produced ``.../repositories/Owner/Repo/pullRequests/1`` —
two path segments in a one-segment slot. ADO has no such route and answers 404,
which is indistinguishable from "PR deleted", so the candidates failed forever
against a perfectly healthy PAT and never got reviewed.

``repo`` is written once at creation, so fixing the write path only helps new
candidates. This recomputes the stored identifier for existing ADO rows, and
clears ``repo_owner`` on ADO repo links so the field stops feeding values into a
platform that has no concept of one.

Revision ID: 008
Revises: 007
Create Date: 2026-09-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # Idempotent and self-limiting: only ADO rows whose `repo` still carries the
    # `owner/` prefix are touched, so a re-run (or a fresh schema built by
    # create_all, where these tables may not exist yet) is a no-op.
    if "readiness_candidates" in tables:
        op.execute(
            sa.text(
                """
                UPDATE readiness_candidates
                   SET repo = repo_name
                 WHERE platform = 'ado'
                   AND repo_name <> ''
                   AND repo <> repo_name
                """
            )
        )
    if "repo_links" in tables:
        op.execute(
            sa.text(
                """
                UPDATE repo_links
                   SET repo_owner = ''
                 WHERE platform = 'ado'
                   AND repo_owner <> ''
                """
            )
        )


def downgrade() -> None:
    # Irreversible by design. The pre-migration values were malformed — an ADO
    # repo identifier with an owner prefix addresses nothing — and the discarded
    # `repo_owner` had no meaning on an ADO link. Reconstructing either would
    # only restore the 404s.
    pass
