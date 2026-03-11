from __future__ import annotations

import asyncio
import base64
import difflib
import json

import httpx
import structlog

from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()

_MAX_CONCURRENT_FETCHES = 10


def _unified_diff(old: str, new: str, path: str) -> str:
    """Compute a unified diff between two file contents."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
    )


class ADOAdapter:
    """Azure DevOps platform adapter using REST API.

    Supports two auth modes (auto-detected by factory):
    - PAT: ``pat=<token>`` — Basic auth, user-scoped
    - Service principal: ``sp_auth=<ADOServicePrincipalAuth>`` — Bearer auth,
      org-scoped, auto-rotating via MSAL
    """

    def __init__(
        self,
        pat: str = "",
        org_url: str = "",
        sp_auth: object | None = None,  # ADOServicePrincipalAuth (optional import)
    ):
        self._pat = pat
        self._org_url = org_url.rstrip("/")
        self._sp_auth = sp_auth  # ADOServicePrincipalAuth | None
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._sp_auth is not None:
                # Service principal: Bearer token (MSAL handles caching/refresh)
                token = self._sp_auth.get_token()
                self._client = httpx.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )
            else:
                # PAT: Basic auth
                encoded = base64.b64encode(f":{self._pat}".encode()).decode()
                self._client = httpx.AsyncClient(
                    headers={
                        "Authorization": f"Basic {encoded}",
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )
        elif self._sp_auth is not None:
            # Refresh token on existing client (MSAL caches, so this is cheap)
            token = self._sp_auth.get_token()
            self._client.headers["Authorization"] = f"Bearer {token}"
        return self._client

    @staticmethod
    def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
        """Normalize ADO webhook payload to PlatformPR."""
        body = payload.body
        resource = body.get("resource", {})
        repo_info = resource.get("repository", {})
        project = repo_info.get("project", {})

        event_type = body.get("eventType", "")
        if "pullrequest" not in event_type.lower():
            return None

        return PlatformPR(
            platform=Platform.ADO,
            pr_id=str(resource.get("pullRequestId", "")),
            repo=repo_info.get("name", ""),
            repo_url=repo_info.get("remoteUrl", ""),
            source_branch=resource.get("sourceRefName", "").replace("refs/heads/", ""),
            target_branch=resource.get("targetRefName", "").replace("refs/heads/", ""),
            author=resource.get("createdBy", {}).get("uniqueName", ""),
            title=resource.get("title", ""),
            head_commit_sha=resource.get("lastMergeSourceCommit", {}).get("commitId", ""),
            org=body.get("resourceContainers", {}).get("collection", {}).get("baseUrl", ""),
            project=project.get("name", ""),
        )

    async def _fetch_file_content(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        project: str,
        repo: str,
        path: str,
        version: str,
        version_type: str = "branch",
    ) -> str | None:
        """Fetch a single file's content at a given version. Returns None on failure."""
        async with sem:
            url = (
                f"{self._org_url}/{project}/_apis/git/repositories/{repo}/items"
            )
            params = {
                "path": f"/{path}",
                "versionDescriptor.version": version,
                "versionDescriptor.versionType": version_type,
                "includeContent": "true",
                "api-version": "7.1",
            }
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "octet-stream" in content_type:
                    return None  # binary file
                return resp.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                log.debug("ado_file_fetch_failed", path=path, version=version, error=str(exc))
                return None

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/iterations"
        )
        resp = await client.get(url, params={"api-version": "7.1"})
        resp.raise_for_status()
        try:
            iterations = resp.json().get("value", [])
        except json.JSONDecodeError:
            log.error("ado_iterations_not_json", pr_id=pr.pr_id, body=resp.text[:200])
            return Diff()

        if not iterations:
            return Diff()

        last_iter = iterations[-1]
        last_iter_id = last_iter["id"]

        # Use commit SHAs from the iteration — branches may be deleted after merge
        source_sha = last_iter.get("sourceRefCommit", {}).get("commitId", "")
        target_sha = last_iter.get("targetRefCommit", {}).get("commitId", "")
        # Fall back to PR-level commit SHAs, then branch names
        source_version = source_sha or pr.head_commit_sha or pr.source_branch
        target_version = target_sha or pr.target_branch
        source_version_type = "commit" if (source_sha or pr.head_commit_sha) else "branch"
        target_version_type = "commit" if target_sha else "branch"

        log.debug(
            "ado_diff_versions",
            pr_id=pr.pr_id,
            source=source_version[:12],
            source_type=source_version_type,
            target=target_version[:12],
            target_type=target_version_type,
        )

        changes_url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/iterations/{last_iter_id}/changes"
        )
        resp = await client.get(changes_url, params={"api-version": "7.1"})
        resp.raise_for_status()

        try:
            change_entries = resp.json().get("changeEntries", [])
        except json.JSONDecodeError:
            log.error("ado_changes_not_json", pr_id=pr.pr_id, body=resp.text[:200])
            return Diff()

        diff_files: list[DiffFile] = []
        for change in change_entries:
            item = change.get("item", {})
            change_type = change.get("changeType") or "edit"
            status_map = {"add": "added", "delete": "deleted", "edit": "modified", "rename": "renamed"}
            raw_path = item.get("path") or ""
            diff_files.append(DiffFile(
                path=raw_path.lstrip("/"),
                status=status_map.get(change_type.lower(), "modified"),
                old_path=change.get("sourceServerItem"),
                additions=0,
                deletions=0,
            ))

        # Fetch file contents and compute patches using commit SHAs
        sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

        async def _enrich(df: DiffFile) -> None:
            if df.status == "added":
                content = await self._fetch_file_content(
                    client, sem, pr.project, pr.repo, df.path,
                    source_version, source_version_type,
                )
                if content is not None:
                    lines = content.splitlines(keepends=True)
                    df.patch = "".join(f"+{line}" for line in lines)
                    df.additions = len(lines)
            elif df.status == "deleted":
                content = await self._fetch_file_content(
                    client, sem, pr.project, pr.repo, df.path,
                    target_version, target_version_type,
                )
                if content is not None:
                    lines = content.splitlines(keepends=True)
                    df.patch = "".join(f"-{line}" for line in lines)
                    df.deletions = len(lines)
            else:
                old_path = df.old_path.lstrip("/") if df.old_path else df.path
                old_content, new_content = await asyncio.gather(
                    self._fetch_file_content(
                        client, sem, pr.project, pr.repo, old_path,
                        target_version, target_version_type,
                    ),
                    self._fetch_file_content(
                        client, sem, pr.project, pr.repo, df.path,
                        source_version, source_version_type,
                    ),
                )
                if old_content is not None and new_content is not None:
                    df.patch = _unified_diff(old_content, new_content, df.path)
                    for line in df.patch.splitlines():
                        if line.startswith("+") and not line.startswith("+++"):
                            df.additions += 1
                        elif line.startswith("-") and not line.startswith("---"):
                            df.deletions += 1

        await asyncio.gather(*[_enrich(df) for df in diff_files])

        log.info(
            "ado_diff_fetched",
            pr_id=pr.pr_id,
            files=len(diff_files),
            files_with_patch=sum(1 for f in diff_files if f.patch),
        )
        return Diff(files=diff_files)

    async def post_comment(self, pr: PlatformPR, body: str) -> None:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/threads"
        )
        resp = await client.post(
            url,
            json={
                "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
                "status": 1,
            },
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def approve_pr(self, pr: PlatformPR) -> None:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/reviewers/me"
        )
        resp = await client.put(
            url,
            json={"vote": 10},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def request_changes(self, pr: PlatformPR, body: str) -> None:
        client = self._get_client()
        # Vote -5 = "Rejected" in ADO
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/reviewers/me"
        )
        resp = await client.put(
            url,
            json={"vote": -5},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def add_label(self, pr: PlatformPR, label: str) -> None:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/labels"
        )
        resp = await client.post(
            url,
            json={"name": label},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def set_status(
        self, pr: PlatformPR, state: str, description: str, context: str = "pr-guardian"
    ) -> None:
        client = self._get_client()
        state_map = {"success": "succeeded", "failure": "failed", "pending": "pending"}
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/statuses"
        )
        resp = await client.post(
            url,
            json={
                "state": state_map.get(state, "notSet"),
                "description": description[:140],
                "context": {"name": context, "genre": "pr-guardian"},
            },
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        log.info("ado_request_reviewers", pr_id=pr.pr_id, group=group)
        # ADO uses reviewer IDs — would need group resolution via API
        # For now, log the intent

    # --- Scan-mode methods ---

    def _parse_repo(self, repo: str) -> tuple[str, str]:
        """Split 'project/repo' into (project, repo_name).

        ADO scan methods receive repo as 'project/repo'. If no slash is
        present, the repo string is used for both project and repo name.
        """
        if "/" in repo:
            project, _, repo_name = repo.partition("/")
            return project, repo_name
        return repo, repo

    async def fetch_recent_commits(
        self, repo: str, branch: str, since: str, until: str | None = None, per_page: int = 100,
    ) -> list[dict]:
        """Fetch commits on branch since a date (ISO 8601).

        Normalizes to GitHub-compatible dict shape for scan agents.
        """
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        url = f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/commits"
        params: dict = {
            "searchCriteria.fromDate": since,
            "searchCriteria.itemVersion.version": branch,
            "$top": per_page,
            "api-version": "7.1",
        }
        if until:
            params["searchCriteria.toDate"] = until

        all_commits: list[dict] = []
        skip = 0
        while True:
            params["$skip"] = skip
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            batch = resp.json().get("value", [])
            if not batch:
                break
            # Normalize each commit to GitHub-like shape
            for c in batch:
                all_commits.append({
                    "sha": c.get("commitId", ""),
                    "commit": {
                        "message": c.get("comment", ""),
                        "author": {
                            "name": c.get("author", {}).get("name", ""),
                            "email": c.get("author", {}).get("email", ""),
                            "date": c.get("author", {}).get("date", ""),
                        },
                        "committer": {
                            "name": c.get("committer", {}).get("name", ""),
                            "date": c.get("committer", {}).get("date", ""),
                        },
                    },
                    "author": {"login": c.get("author", {}).get("name", "")},
                })
            if len(batch) < per_page:
                break
            skip += len(batch)
        return all_commits

    async def fetch_merged_prs(
        self, repo: str, since: str, base: str = "main",
    ) -> list[dict]:
        """Fetch recently merged (completed) PRs.

        Normalizes to GitHub-compatible dict shape for scan agents.
        """
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        url = (
            f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/pullrequests"
        )
        params: dict = {
            "searchCriteria.status": "completed",
            "searchCriteria.targetRefName": f"refs/heads/{base}",
            "$top": 100,
            "api-version": "7.1",
        }
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        all_prs = resp.json().get("value", [])

        # Filter to PRs closed after `since`
        merged: list[dict] = []
        for pr in all_prs:
            closed_date = pr.get("closedDate", "")
            if closed_date and closed_date >= since:
                merged.append({
                    "number": pr.get("pullRequestId"),
                    "title": pr.get("title", ""),
                    "user": {"login": pr.get("createdBy", {}).get("uniqueName", "")},
                    "merged_at": closed_date,
                    "base": {"ref": base},
                    "_ado_project": project,
                    "_ado_repo": repo_name,
                })
        return merged

    async def fetch_file_content(
        self, repo: str, path: str, ref: str = "HEAD",
    ) -> str:
        """Fetch file content from the repo."""
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        sem = asyncio.Semaphore(1)

        # Map ref to ADO version type
        version = ref
        version_type = "branch"
        if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower()):
            version_type = "commit"
        elif ref == "HEAD":
            # ADO doesn't support HEAD — use default branch (omit version)
            version = ""
            version_type = "branch"

        if version:
            content = await self._fetch_file_content(
                client, sem, project, repo_name, path, version, version_type,
            )
        else:
            # No version specified — fetch from default branch
            url = f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/items"
            params: dict = {
                "path": f"/{path}",
                "includeContent": "true",
                "api-version": "7.1",
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "octet-stream" in ct:
                return ""
            content = resp.text

        return content or ""

    async def list_repo_files(
        self, repo: str, ref: str = "HEAD", path: str = "",
    ) -> list[str]:
        """List files in repo (recursive tree)."""
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        url = f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/items"
        params: dict = {
            "recursionLevel": "full",
            "api-version": "7.1",
        }
        if ref and ref != "HEAD":
            params["versionDescriptor.version"] = ref
            if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower()):
                params["versionDescriptor.versionType"] = "commit"
            else:
                params["versionDescriptor.versionType"] = "branch"
        if path:
            params["scopePath"] = f"/{path}"

        resp = await client.get(url, params=params)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        # Filter to files only (not folders), strip leading slash
        return [
            item["path"].lstrip("/")
            for item in items
            if not item.get("isFolder", False) and item.get("path")
        ]

    async def fetch_pr_files(
        self, repo: str, pr_id: int | str, project: str = "",
    ) -> list[dict]:
        """Fetch changed files for a PR.

        Uses the iterations/changes API and normalizes to GitHub-compatible shape.
        """
        client = self._get_client()
        if not project:
            project, repo = self._parse_repo(repo)

        # Get the last iteration
        iter_url = (
            f"{self._org_url}/{project}/_apis/git/repositories/{repo}"
            f"/pullRequests/{pr_id}/iterations"
        )
        resp = await client.get(iter_url, params={"api-version": "7.1"})
        resp.raise_for_status()
        iterations = resp.json().get("value", [])
        if not iterations:
            return []

        last_iter_id = iterations[-1]["id"]

        # Get changes for that iteration
        changes_url = (
            f"{self._org_url}/{project}/_apis/git/repositories/{repo}"
            f"/pullRequests/{pr_id}/iterations/{last_iter_id}/changes"
        )
        resp = await client.get(changes_url, params={"api-version": "7.1"})
        resp.raise_for_status()
        change_entries = resp.json().get("changeEntries", [])

        files: list[dict] = []
        for change in change_entries:
            item = change.get("item", {})
            raw_path = (item.get("path") or "").lstrip("/")
            files.append({
                "filename": raw_path,
                "additions": 0,
                "deletions": 0,
                "status": change.get("changeType", "edit").lower(),
            })
        return files

    async def fetch_commits_for_path(
        self, repo: str, path: str, per_page: int = 1, project: str = "",
    ) -> list[dict]:
        """Fetch recent commits that touched a specific file path.

        Normalizes to GitHub-compatible dict shape.
        """
        client = self._get_client()
        if not project:
            project, repo = self._parse_repo(repo)

        url = f"{self._org_url}/{project}/_apis/git/repositories/{repo}/commits"
        params: dict = {
            "searchCriteria.itemPath": f"/{path}",
            "$top": per_page,
            "api-version": "7.1",
        }
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        ado_commits = resp.json().get("value", [])

        # Normalize to GitHub shape
        return [
            {
                "sha": c.get("commitId", ""),
                "commit": {
                    "committer": {
                        "date": c.get("committer", {}).get("date", ""),
                    },
                },
            }
            for c in ado_commits
        ]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
