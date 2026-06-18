"""ReviewContext fixtures for HumanGateAgent tests.

Two scenarios:
- leaf_safe_context: CI/workflow-only change, leaf archmap classification — low structural risk.
- hub_destructive_context: a destructive migration on an archmap hub — high structural risk.
"""

from __future__ import annotations

from pathlib import Path

from pr_guardian.models.context import (
    ArchmapContext,
    ArchmapFile,
    ChangeProfile,
    FileRole,
    ReviewContext,
)
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR


def _base_pr(title: str, source_branch: str) -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="99",
        repo="acme/service",
        repo_url="https://github.com/acme/service",
        source_branch=source_branch,
        target_branch="main",
        author="dev",
        title=title,
        head_commit_sha="deadbeef",
    )


def leaf_safe_context() -> ReviewContext:
    """CI-only change touching a leaf file — no structural danger."""
    diff = Diff(
        files=[
            DiffFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=3,
                deletions=1,
                patch=(
                    "@@\n"
                    "-      runs-on: ubuntu-20.04\n"
                    "+      runs-on: ubuntu-22.04\n"
                    "+      timeout-minutes: 10\n"
                ),
            )
        ]
    )
    archmap = ArchmapContext(
        commit="deadbeef",
        files={
            ".github/workflows/ci.yml": ArchmapFile(
                path=".github/workflows/ci.yml",
                classification="leaf",
                ca=0,
                tca=0,
                instability=1.0,
                risk=1,
                overridden=False,
                reason="CI workflow file",
            )
        },
    )
    return ReviewContext(
        pr=_base_pr("chore: bump CI runner to ubuntu-22.04", "chore/ci-runner"),
        repo_path=Path("/tmp/repo"),
        diff=diff,
        changed_files=[".github/workflows/ci.yml"],
        lines_changed=4,
        language_map=LanguageMap(
            languages={"yaml": [".github/workflows/ci.yml"]},
            primary_language="yaml",
            language_count=1,
        ),
        primary_language="yaml",
        cross_stack=False,
        archmap=archmap,
        change_profile=ChangeProfile(
            file_roles={".github/workflows/ci.yml": {FileRole.INFRA}},
            has_production_changes=False,
        ),
    )


def hub_destructive_context() -> ReviewContext:
    """Destructive DB migration on an archmap hub — high structural danger."""
    diff = Diff(
        files=[
            DiffFile(
                path="alembic/versions/003_drop_user_tokens.py",
                status="added",
                additions=18,
                deletions=0,
                patch=(
                    "@@\n"
                    "+def upgrade():\n"
                    "+    op.drop_table('user_tokens')\n"
                    "+\n"
                    "+def downgrade():\n"
                    "+    pass  # irreversible\n"
                ),
            ),
            DiffFile(
                path="src/pr_guardian/core/orchestrator.py",
                status="modified",
                additions=5,
                deletions=2,
                patch=(
                    "@@\n"
                    "-    token = db.query(UserToken).filter_by(user_id=uid).first()\n"
                    "+    token = None  # tokens table removed\n"
                ),
            ),
        ]
    )
    archmap = ArchmapContext(
        commit="deadbeef",
        files={
            "src/pr_guardian/core/orchestrator.py": ArchmapFile(
                path="src/pr_guardian/core/orchestrator.py",
                classification="hub",
                ca=14,
                tca=42,
                instability=0.1,
                risk=9,
                overridden=False,
                reason="Core orchestrator imported by 14 modules",
                dependents=("src/api/reviews.py", "src/api/webhooks.py"),
            ),
            "alembic/versions/003_drop_user_tokens.py": ArchmapFile(
                path="alembic/versions/003_drop_user_tokens.py",
                classification="leaf",
                ca=0,
                tca=0,
                instability=1.0,
                risk=2,
                overridden=False,
                reason="Migration file",
            ),
        },
    )
    return ReviewContext(
        pr=_base_pr("feat: remove user_tokens table", "feat/remove-tokens"),
        repo_path=Path("/tmp/repo"),
        diff=diff,
        changed_files=[
            "alembic/versions/003_drop_user_tokens.py",
            "src/pr_guardian/core/orchestrator.py",
        ],
        lines_changed=25,
        language_map=LanguageMap(
            languages={
                "python": [
                    "alembic/versions/003_drop_user_tokens.py",
                    "src/pr_guardian/core/orchestrator.py",
                ]
            },
            primary_language="python",
            language_count=1,
        ),
        primary_language="python",
        cross_stack=False,
        archmap=archmap,
        change_profile=ChangeProfile(
            file_roles={
                "alembic/versions/003_drop_user_tokens.py": {FileRole.CONFIG},
                "src/pr_guardian/core/orchestrator.py": {FileRole.PRODUCTION},
            },
            has_production_changes=True,
            touches_data_layer=True,
        ),
    )
