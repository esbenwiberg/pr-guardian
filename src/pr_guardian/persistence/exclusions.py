"""Persistence helpers for PR dashboard exclusion filters."""

from __future__ import annotations

import uuid
from fnmatch import fnmatchcase

import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pr_guardian.persistence.database import async_session
from pr_guardian.persistence.models import ExcludedRepoRow, ExclusionRuleRow, SyncedPRRow

log = structlog.get_logger()


async def list_excluded_repos() -> list[dict[str, object]]:
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(ExcludedRepoRow).order_by(ExcludedRepoRow.created_at.desc())
            )
        ).all()
        return [
            {
                "id": str(r.id),
                "platform": r.platform,
                "org": r.org,
                "project": r.project,
                "repo": r.repo,
                "excluded_by_email": r.excluded_by_email,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def add_excluded_repo(platform: str, org: str, project: str, repo: str, email: str) -> bool:
    """Add a repo exclusion. Returns False if it already exists."""
    async with async_session() as session:
        row = ExcludedRepoRow(
            platform=platform,
            org=org,
            project=project,
            repo=repo,
            excluded_by_email=email,
        )
        session.add(row)
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


async def remove_excluded_repo(exclusion_id: str) -> bool:
    """Remove a repo exclusion by UUID. Returns False if not found."""
    async with async_session() as session:
        result = await session.execute(
            sa_delete(ExcludedRepoRow).where(ExcludedRepoRow.id == uuid.UUID(exclusion_id))
        )
        await session.commit()
        return (result.rowcount or 0) > 0


def _rule_to_dict(row: ExclusionRuleRow) -> dict[str, object]:
    return {
        "id": str(row.id),
        "platform": row.platform,
        "org_pattern": row.org_pattern,
        "project_pattern": row.project_pattern,
        "repo_pattern": row.repo_pattern,
        "created_by_email": row.created_by_email,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def repo_matches_rules(
    rules: list[dict[str, object]],
    platform: str,
    org: str,
    project: str,
    repo: str,
) -> bool:
    """True if (platform, org, project, repo) matches any rule. Empty pattern = match-any."""
    for rule in rules:
        rule_platform = str(rule.get("platform") or "")
        if rule_platform and rule_platform != platform:
            continue
        org_pat = str(rule.get("org_pattern") or "")
        proj_pat = str(rule.get("project_pattern") or "")
        repo_pat = str(rule.get("repo_pattern") or "")
        if org_pat and not fnmatchcase(org or "", org_pat):
            continue
        if proj_pat and not fnmatchcase(project or "", proj_pat):
            continue
        if repo_pat and not fnmatchcase(repo or "", repo_pat):
            continue
        return True
    return False


async def list_exclusion_rules() -> list[dict[str, object]]:
    try:
        async with async_session() as session:
            rows = (
                await session.scalars(
                    select(ExclusionRuleRow).order_by(ExclusionRuleRow.created_at.desc())
                )
            ).all()
            return [_rule_to_dict(r) for r in rows]
    except Exception:
        log.warning("list_exclusion_rules_failed", hint="DB unavailable; returning empty list")
        return []


async def add_exclusion_rule(
    *,
    platform: str,
    org_pattern: str = "",
    project_pattern: str = "",
    repo_pattern: str = "",
    email: str = "",
) -> dict[str, object]:
    """Add a new exclusion rule. Returns the created rule dict."""
    async with async_session() as session:
        row = ExclusionRuleRow(
            platform=platform,
            org_pattern=org_pattern,
            project_pattern=project_pattern,
            repo_pattern=repo_pattern,
            created_by_email=email,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _rule_to_dict(row)


async def remove_exclusion_rule(rule_id: str) -> bool:
    """Remove an exclusion rule by UUID. Returns False if not found or rule_id is invalid."""
    try:
        parsed_id = uuid.UUID(rule_id)
    except ValueError:
        return False

    async with async_session() as session:
        result = await session.execute(
            sa_delete(ExclusionRuleRow).where(ExclusionRuleRow.id == parsed_id)
        )
        await session.commit()
        return (result.rowcount or 0) > 0


async def get_pr_filter_options() -> dict[str, list[str]]:
    """Return distinct platforms, orgs, projects, and repos for filter dropdowns."""

    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    SyncedPRRow.platform,
                    SyncedPRRow.org,
                    SyncedPRRow.project,
                    SyncedPRRow.repo,
                ).distinct()
            )
        ).fetchall()

    platforms: list[str] = []
    orgs: list[str] = []
    projects: list[str] = []
    repos: list[str] = []
    seen: dict[str, set[str]] = {
        "platform": set(),
        "org": set(),
        "project": set(),
        "repo": set(),
    }
    for platform, org, project, repo in rows:
        if platform and platform not in seen["platform"]:
            platforms.append(platform)
            seen["platform"].add(platform)
        if org and org not in seen["org"]:
            orgs.append(org)
            seen["org"].add(org)
        if project and project not in seen["project"]:
            projects.append(project)
            seen["project"].add(project)
        if repo and repo not in seen["repo"]:
            repos.append(repo)
            seen["repo"].add(repo)

    return {
        "platforms": sorted(platforms),
        "orgs": sorted(orgs),
        "projects": sorted(projects),
        "repos": sorted(repos),
    }
