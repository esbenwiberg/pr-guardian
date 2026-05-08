"""Background worker that syncs open PRs from GitHub and ADO into the local DB."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import structlog

from pr_guardian.persistence import storage

log = structlog.get_logger()

_STALE_DAYS = 5
_MERGED_RETENTION_DAYS = 7


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


def _normalize_github_merged_pr(pr: dict, repo_full_name: str) -> dict:
    """Build a synced-PR dict for a merged GitHub PR (overrides status + timestamps)."""
    base = _normalize_github_pr(pr, repo_full_name)
    base["approval_status"] = "merged"
    base["is_draft"] = False
    base["has_conflicts"] = False
    merged_at = pr.get("merged_at") or pr.get("updated_at")
    if merged_at:
        base["pr_updated_at"] = merged_at
    return base


def _normalize_ado_merged_pr(
    pr: dict, org_url: str, project: str, repo_name: str
) -> dict:
    """Build a synced-PR dict for a merged ADO PR.

    ``pr`` here is the GitHub-shaped dict returned by ``ADOAdapter.fetch_merged_prs``,
    not a raw ADO payload, so we can't reuse ``_normalize_ado_pr``.
    """
    pr_id = str(pr.get("number", ""))
    pr_url = (
        f"{org_url.rstrip('/')}/{project}/_git/{repo_name}/pullrequest/{pr_id}"
    )
    author = pr.get("user", {}).get("login", "")
    merged_at = pr.get("merged_at")
    created_at = pr.get("created_at") or merged_at
    return {
        "platform": "ado",
        "pr_id": pr_id,
        "org": org_url,
        "project": project,
        "repo": repo_name,
        "title": pr.get("title", ""),
        "author": author,
        "author_display": author,
        "pr_url": pr_url,
        "source_branch": "",
        "target_branch": pr.get("base", {}).get("ref", ""),
        "is_draft": False,
        "has_conflicts": False,
        "approval_status": "merged",
        "reviewers": [],
        "assignees": [],
        "ci_status": "unknown",
        "comment_count": 0,
        "pr_created_at": created_at,
        "pr_updated_at": merged_at,
    }


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

        since = (
            datetime.now(timezone.utc) - timedelta(days=_MERGED_RETENTION_DAYS)
        ).isoformat()
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
                default_branch = repo_data.get("default_branch") or "main"
                try:
                    merged_prs = await adapter.fetch_merged_prs(
                        repo, since=since, base=default_branch
                    )
                except Exception as exc:
                    log.warning(
                        "github_fetch_merged_failed", repo=repo, error=str(exc)
                    )
                    merged_prs = []

                synced_pr_ids: list[str] = []
                if prs or merged_prs:
                    await storage.upsert_sync_source(
                        platform="github",
                        org=org,
                        project="",
                        repo=repo,
                        repo_url=repo_data.get("clone_url", ""),
                    )
                    for pr in prs:
                        await storage.upsert_synced_pr(_normalize_github_pr(pr, repo))
                        synced_pr_ids.append(str(pr["number"]))
                    for pr in merged_prs:
                        await storage.upsert_synced_pr(
                            _normalize_github_merged_pr(pr, repo)
                        )
                        synced_pr_ids.append(str(pr["number"]))
                    await storage.mark_sync_source_synced("github", repo)
                await storage.delete_closed_prs("github", repo, "", synced_pr_ids)
                log.debug(
                    "github_repo_synced",
                    repo=repo,
                    open_prs=len(prs),
                    merged_prs=len(merged_prs),
                )
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

        since = (
            datetime.now(timezone.utc) - timedelta(days=_MERGED_RETENTION_DAYS)
        ).isoformat()
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
                        default_branch = (
                            (repo_data.get("defaultBranch") or "")
                            .replace("refs/heads/", "")
                            or "main"
                        )
                        try:
                            merged_prs = await adapter.fetch_merged_prs(
                                f"{project_name}/{repo_name}",
                                since=since,
                                base=default_branch,
                            )
                        except Exception as exc:
                            log.warning(
                                "ado_fetch_merged_failed",
                                project=project_name,
                                repo=repo_name,
                                error=str(exc),
                            )
                            merged_prs = []

                        synced_pr_ids: list[str] = []
                        if prs or merged_prs:
                            await storage.upsert_sync_source(
                                platform="ado",
                                org=org_url,
                                project=project_name,
                                repo=repo_name,
                                repo_url=repo_data.get("remoteUrl", ""),
                            )
                            for pr in prs:
                                await storage.upsert_synced_pr(
                                    _normalize_ado_pr(
                                        pr, org_url, project_name, repo_name
                                    )
                                )
                                synced_pr_ids.append(str(pr.get("pullRequestId", "")))
                            for pr in merged_prs:
                                await storage.upsert_synced_pr(
                                    _normalize_ado_merged_pr(
                                        pr, org_url, project_name, repo_name
                                    )
                                )
                                synced_pr_ids.append(str(pr.get("number", "")))
                            await storage.mark_sync_source_synced(
                                "ado", repo_name, project=project_name
                            )
                        await storage.delete_closed_prs(
                            "ado", repo_name, project_name, synced_pr_ids
                        )
                        log.debug(
                            "ado_repo_synced",
                            project=project_name,
                            repo=repo_name,
                            open_prs=len(prs),
                            merged_prs=len(merged_prs),
                        )
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
    try:
        purged = await storage.purge_old_merged_prs(_MERGED_RETENTION_DAYS)
        if purged:
            log.info("pr_sync_purged_merged", count=purged)
    except Exception as exc:
        log.warning("pr_sync_purge_failed", error=str(exc))
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
