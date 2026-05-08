"""Background worker that syncs open PRs from GitHub and ADO into the local DB."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import structlog

from pr_guardian.persistence import storage

log = structlog.get_logger()

_STALE_DAYS = 5


def _is_work_hours() -> bool:
    """True if current local hour is between 09:00 and 18:00."""
    return 9 <= datetime.now().hour < 18


def _gh_approval_status(reviews: list[dict]) -> str:
    """Compute approval status from GitHub review list (most recent per reviewer wins)."""
    latest: dict[str, str] = {}
    for r in reviews:
        login = r.get("user", {}).get("login", "")
        state = r.get("state", "")
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = state

    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if states == {"APPROVED"}:
        return "approved"
    return "pending"


def _normalize_github_pr(pr: dict, repo_full_name: str) -> dict:
    org = repo_full_name.split("/")[0] if "/" in repo_full_name else repo_full_name
    approval_status = "draft" if pr.get("draft") else _gh_approval_status(pr.get("_reviews", []))
    reviewers = [r["login"] for r in pr.get("requested_reviewers", [])]
    assignees = [a.get("login", "") for a in pr.get("assignees", []) if a.get("login")]
    return {
        "platform": "github",
        "pr_id": str(pr["number"]),
        "org": org,
        "project": "",
        "repo": repo_full_name,
        "title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "author_display": pr.get("user", {}).get("login", ""),
        "pr_url": pr.get("html_url", ""),
        "source_branch": pr.get("head", {}).get("ref", ""),
        "target_branch": pr.get("base", {}).get("ref", ""),
        "is_draft": bool(pr.get("draft", False)),
        "has_conflicts": pr.get("mergeable") is False,
        "approval_status": approval_status,
        "reviewers": reviewers,
        "assignees": assignees,
        "ci_status": pr.get("_ci_status", "unknown"),
        "comment_count": (pr.get("comments") or 0) + (pr.get("review_comments") or 0),
        "pr_created_at": pr.get("created_at"),
        "pr_updated_at": pr.get("updated_at"),
    }


def _ado_approval_status(reviewers: list[dict]) -> str:
    """Compute approval from ADO reviewer votes."""
    votes = [r.get("vote", 0) for r in reviewers]
    if any(v <= -5 for v in votes):
        return "changes_requested"
    if votes and all(v >= 10 for v in votes):
        return "approved"
    return "pending"


def _normalize_ado_pr(pr: dict, org_url: str, project: str, repo_name: str) -> dict:
    reviewers_raw = pr.get("reviewers", [])
    reviewers = [r.get("uniqueName", "") or r.get("displayName", "") for r in reviewers_raw]
    approval_status = _ado_approval_status(reviewers_raw)
    pr_id = str(pr.get("pullRequestId", ""))
    pr_url = (
        f"{org_url.rstrip('/')}/{project}/_git/{repo_name}/pullrequest/{pr_id}"
    )
    return {
        "platform": "ado",
        "pr_id": pr_id,
        "org": org_url,
        "project": project,
        "repo": repo_name,
        "title": pr.get("title", ""),
        "author": pr.get("createdBy", {}).get("uniqueName", ""),
        "author_display": pr.get("createdBy", {}).get("displayName", ""),
        "pr_url": pr_url,
        "source_branch": pr.get("sourceRefName", "").replace("refs/heads/", ""),
        "target_branch": pr.get("targetRefName", "").replace("refs/heads/", ""),
        "is_draft": bool(pr.get("isDraft", False)),
        "has_conflicts": pr.get("mergeStatus") == "conflicts",
        "approval_status": approval_status,
        "reviewers": [r for r in reviewers if r],
        "assignees": [],
        "ci_status": "unknown",
        "comment_count": 0,
        "pr_created_at": pr.get("creationDate"),
        "pr_updated_at": pr.get("creationDate"),
    }


async def _sync_github(token: str, pat_label: str = "env") -> None:
    from pr_guardian.platform.github import GitHubAdapter

    rules = await storage.list_exclusion_rules()

    adapter = GitHubAdapter(token=token)
    try:
        repos = await adapter.list_accessible_repos()
        log.info("github_sync_repos_discovered", count=len(repos), pat=pat_label)

        for repo_data in repos:
            repo = repo_data.get("full_name", "")
            if not repo:
                continue
            org = repo_data.get("owner", {}).get("login", "")
            if storage.repo_matches_rules(rules, "github", org, "", repo):
                log.debug("github_repo_skipped_by_rule", repo=repo)
                continue
            try:
                prs = await adapter.list_repo_open_prs(repo)
                if not prs:
                    continue
                await storage.upsert_sync_source(
                    platform="github",
                    org=org,
                    project="",
                    repo=repo,
                    repo_url=repo_data.get("clone_url", ""),
                )
                open_ids: list[str] = []
                for pr in prs:
                    pr_data = _normalize_github_pr(pr, repo)
                    await storage.upsert_synced_pr(pr_data)
                    open_ids.append(str(pr["number"]))
                await storage.delete_closed_prs("github", repo, "", open_ids)
                await storage.mark_sync_source_synced("github", repo)
                log.debug("github_repo_synced", repo=repo, open_prs=len(open_ids))
            except Exception as exc:
                log.warning("github_repo_sync_failed", repo=repo, error=str(exc))
    finally:
        await adapter.close()


async def _sync_ado(pat: str, org_url: str) -> None:
    from pr_guardian.platform.ado import ADOAdapter

    rules = await storage.list_exclusion_rules()

    adapter = ADOAdapter(pat=pat, org_url=org_url)
    try:
        projects = await adapter.list_projects()
        log.info("ado_sync_projects_discovered", count=len(projects))

        for proj in projects:
            project_name = proj.get("name", "")
            if not project_name:
                continue
            try:
                repos = await adapter.list_repos(project_name)
                for repo_data in repos:
                    repo_name = repo_data.get("name", "")
                    if not repo_name:
                        continue
                    if storage.repo_matches_rules(rules, "ado", org_url, project_name, repo_name):
                        log.debug("ado_repo_skipped_by_rule", project=project_name, repo=repo_name)
                        continue
                    try:
                        prs = await adapter.list_repo_open_prs(project_name, repo_name)
                        if not prs:
                            continue
                        await storage.upsert_sync_source(
                            platform="ado",
                            org=org_url,
                            project=project_name,
                            repo=repo_name,
                            repo_url=repo_data.get("remoteUrl", ""),
                        )
                        open_ids: list[str] = []
                        for pr in prs:
                            pr_data = _normalize_ado_pr(pr, org_url, project_name, repo_name)
                            await storage.upsert_synced_pr(pr_data)
                            open_ids.append(str(pr.get("pullRequestId", "")))
                        await storage.delete_closed_prs("ado", repo_name, project_name, open_ids)
                        await storage.mark_sync_source_synced("ado", repo_name, project=project_name)
                        log.debug("ado_repo_synced", project=project_name, repo=repo_name, open_prs=len(open_ids))
                    except Exception as exc:
                        log.warning("ado_repo_sync_failed", project=project_name, repo=repo_name, error=str(exc))
            except Exception as exc:
                log.warning("ado_project_sync_failed", project=project_name, error=str(exc))
    finally:
        await adapter.close()


async def _resolve_github_sync_tokens() -> list[tuple[str, str]]:
    """Return list of (label, token) pairs for every GitHub PAT to sync.

    Iterates all PATs in the DB so each org's repos are reachable. Falls back to
    GITHUB_TOKEN env var only when the DB list is empty (legacy single-PAT installs).
    """
    pats = await storage.list_github_pats()
    pairs: list[tuple[str, str]] = []
    for pat in pats:
        try:
            token = await storage.resolve_github_token(pat["name"])
        except LookupError as exc:
            log.warning("github_pat_resolve_failed", pat=pat["name"], error=str(exc))
            continue
        if token:
            pairs.append((pat["name"], token))
    if not pairs:
        env_token = os.environ.get("GITHUB_TOKEN", "")
        if env_token:
            pairs.append(("env", env_token))
    return pairs


async def run_pr_sync() -> None:
    """Single sync pass across all configured platforms."""
    ado_pat = os.environ.get("ADO_PAT", "")
    ado_org_url = os.environ.get("ADO_ORG_URL", "")

    tasks = []
    for label, token in await _resolve_github_sync_tokens():
        tasks.append(_sync_github(token, pat_label=label))
    if ado_pat and ado_org_url:
        tasks.append(_sync_ado(ado_pat, ado_org_url))

    if not tasks:
        log.debug("pr_sync_no_sources_configured")
        return

    log.info("pr_sync_start", sources=len(tasks))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.error("pr_sync_platform_error", error=str(r))
    log.info("pr_sync_done")


async def pr_sync_loop() -> None:
    """Long-running loop: sync every 5min during 09-18, 30min outside."""
    while True:
        try:
            await run_pr_sync()
        except Exception as exc:
            log.error("pr_sync_loop_error", error=str(exc))
        interval = 5 * 60 if _is_work_hours() else 30 * 60
        log.debug("pr_sync_sleeping", seconds=interval)
        await asyncio.sleep(interval)
