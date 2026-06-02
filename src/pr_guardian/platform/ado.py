from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os

import httpx
import structlog

from pr_guardian.models.findings import Finding
from pr_guardian.models.pr import Diff, DiffFile, FileStatus, Platform, PlatformPR
from pr_guardian.platform._utils import inline_comment_body
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import PlatformPRMetadata, PlatformReadinessSignal

log = structlog.get_logger()

_MAX_CONCURRENT_FETCHES = 10


def _unified_diff(old: str, new: str, path: str) -> str:
    """Compute a unified diff between two file contents."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


class ADOAdapter:
    """Azure DevOps platform adapter using REST API."""

    def __init__(self, pat: str = "", org_url: str = ""):
        self._pat = pat
        self._org_url = org_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            encoded = base64.b64encode(f":{self._pat}".encode()).decode()
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def resolve_branch_head(
        self,
        project: str,
        repo: str,
        branch: str,
    ) -> str:
        """Fetch the actual HEAD commit SHA of a branch via the refs API.

        More reliable than ``lastMergeSourceCommit`` on the PR object, which
        only updates after ADO re-evaluates the merge (can lag behind pushes).
        """
        client = self._get_client()
        resp = await client.get(
            f"{self._org_url}/{project}/_apis/git/repositories/{repo}/refs",
            params={
                "filter": f"heads/{branch}",
                "api-version": "7.1",
            },
        )
        resp.raise_for_status()
        refs = resp.json().get("value", [])
        if refs:
            return refs[0].get("objectId", "")
        return ""

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
            url = f"{self._org_url}/{project}/_apis/git/repositories/{repo}/items"
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
        iter_source_sha = last_iter.get("sourceRefCommit", {}).get("commitId", "")
        target_sha = last_iter.get("targetRefCommit", {}).get("commitId", "")

        # Detect stale iteration: ADO may not have created a new iteration for
        # the latest push yet, so the last iteration's sourceRefCommit lags
        # behind the real branch HEAD (pr.head_commit_sha, resolved via refs).
        iteration_stale = (
            pr.head_commit_sha and iter_source_sha and iter_source_sha != pr.head_commit_sha
        )

        if iteration_stale:
            log.warn(
                "ado_stale_iteration",
                pr_id=pr.pr_id,
                iter_sha=iter_source_sha[:12],
                branch_head=pr.head_commit_sha[:12],
                msg="Iteration behind branch HEAD — using commits diff API",
            )
            # Fall back to commits diff API for the file list — it uses
            # actual commit SHAs so it always reflects the latest push.
            target_version = target_sha or pr.target_branch
            target_version_type = "commit" if target_sha else "branch"
            # Resolve target branch HEAD when we only have a branch name
            if target_version_type == "branch":
                try:
                    t_head = await self.resolve_branch_head(
                        pr.project,
                        pr.repo,
                        target_version,
                    )
                    if t_head:
                        target_version = t_head
                        target_version_type = "commit"
                except Exception:
                    pass
            fallback_diff = await self.fetch_compare_diff(
                pr.repo,
                target_version,
                pr.head_commit_sha,
                project=pr.project,
            )
            diff_files = fallback_diff.files
            source_version = pr.head_commit_sha
            source_version_type = "commit"
        else:
            source_version = iter_source_sha or pr.head_commit_sha or pr.source_branch
            target_version = target_sha or pr.target_branch
            source_version_type = "commit" if (iter_source_sha or pr.head_commit_sha) else "branch"
            target_version_type = "commit" if target_sha else "branch"

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

            diff_files = []
            for change in change_entries:
                item = change.get("item", {})
                change_type = change.get("changeType") or "edit"
                status_map: dict[str, FileStatus] = {
                    "add": "added",
                    "delete": "deleted",
                    "edit": "modified",
                    "rename": "renamed",
                }
                raw_path = item.get("path") or ""
                diff_files.append(
                    DiffFile(
                        path=raw_path.lstrip("/"),
                        status=status_map.get(change_type.lower(), "modified"),
                        old_path=change.get("sourceServerItem"),
                        additions=0,
                        deletions=0,
                    )
                )

        log.debug(
            "ado_diff_versions",
            pr_id=pr.pr_id,
            source=source_version[:12],
            source_type=source_version_type,
            target=target_version[:12],
            target_type=target_version_type,
            stale_fallback=iteration_stale,
        )

        # Fetch file contents and compute patches using commit SHAs
        sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

        async def _enrich(df: DiffFile) -> None:
            if df.status == "added":
                content = await self._fetch_file_content(
                    client,
                    sem,
                    pr.project,
                    pr.repo,
                    df.path,
                    source_version,
                    source_version_type,
                )
                if content is not None:
                    lines = content.splitlines(keepends=True)
                    df.patch = "".join(f"+{line}" for line in lines)
                    df.additions = len(lines)
            elif df.status == "deleted":
                content = await self._fetch_file_content(
                    client,
                    sem,
                    pr.project,
                    pr.repo,
                    df.path,
                    target_version,
                    target_version_type,
                )
                if content is not None:
                    lines = content.splitlines(keepends=True)
                    df.patch = "".join(f"-{line}" for line in lines)
                    df.deletions = len(lines)
            else:
                old_path = df.old_path.lstrip("/") if df.old_path else df.path
                old_content, new_content = await asyncio.gather(
                    self._fetch_file_content(
                        client,
                        sem,
                        pr.project,
                        pr.repo,
                        old_path,
                        target_version,
                        target_version_type,
                    ),
                    self._fetch_file_content(
                        client,
                        sem,
                        pr.project,
                        pr.repo,
                        df.path,
                        source_version,
                        source_version_type,
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

    async def _get_current_user_id(self) -> str:
        """Return the authenticated user's GUID needed for /reviewers/{id} voting.

        ADO's /reviewers/{id} endpoint requires the real identity GUID — "me"
        is not a valid path segment and returns 400.

        Resolution order:
        1. ``ADO_REVIEWER_ID`` env var (explicit override — skips all HTTP calls).
        2. ``vssps.dev.azure.com/{org}/_apis/connectionData`` — dedicated identity
           host; this is what official ADO clients call.
        3. ``dev.azure.com/{org}/_apis/connectionData`` — legacy fallback; some
           Entra-tenanted orgs return 400/302 here even with a fully valid PAT.

        On HTTP failure we surface the response body in the raised error so the
        actual ADO error code (TF400813, VS401253, etc.) reaches the operator
        instead of just "Client error '400 Bad Request'".
        """
        override = os.environ.get("ADO_REVIEWER_ID", "").strip()
        if override:
            return override

        client = self._get_client()
        # https://dev.azure.com/{org} -> https://vssps.dev.azure.com/{org}
        vssps_url = self._org_url.replace(
            "https://dev.azure.com",
            "https://vssps.dev.azure.com",
            1,
        )
        candidates = [
            f"{vssps_url}/_apis/connectionData",
            f"{self._org_url}/_apis/connectionData",
        ]
        last_exc: httpx.HTTPStatusError | None = None
        for url in candidates:
            try:
                # connectionData is a preview-only resource — passing the
                # stable "7.1" returns VssInvalidPreviewVersionException.
                resp = await client.get(url, params={"api-version": "7.1-preview.1"})
                resp.raise_for_status()
                user = resp.json().get("authenticatedUser", {})
                user_id = user.get("id", "")
                # Anonymous user means the PAT didn't authenticate even though
                # the request returned 200 — treat as a failure so we fall through.
                if user_id and user_id != "00000000-0000-0000-0000-000000000000":
                    return user_id
                log.warning("ado_connectiondata_anonymous", url=url)
            except httpx.HTTPStatusError as exc:
                body = (exc.response.text or "")[:500]
                log.warning(
                    "ado_connectiondata_failed",
                    url=url,
                    status=exc.response.status_code,
                    body=body,
                )
                last_exc = exc

        if last_exc is not None:
            body = (last_exc.response.text or "")[:500]
            raise RuntimeError(
                "ADO connectionData failed on both vssps and dev.azure.com — "
                "cannot resolve reviewer identity for voting. "
                "Set ADO_REVIEWER_ID env var to your ADO user GUID to bypass. "
                f"Last response: HTTP {last_exc.response.status_code} body={body!r}"
            ) from last_exc
        raise RuntimeError(
            "ADO connectionData returned anonymous user from both hosts. "
            "PAT may be invalid or lack Identity (Read) scope. "
            "Set ADO_REVIEWER_ID env var to bypass."
        )

    async def approve_pr(self, pr: PlatformPR) -> None:
        client = self._get_client()
        reviewer_id = await self._get_current_user_id()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/reviewers/{reviewer_id}"
        )
        resp = await client.put(
            url,
            json={"vote": 10, "id": reviewer_id},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()

    async def request_changes(self, pr: PlatformPR, body: str) -> None:
        client = self._get_client()
        # Vote -5 = "Rejected" in ADO
        reviewer_id = await self._get_current_user_id()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/reviewers/{reviewer_id}"
        )
        resp = await client.put(
            url,
            json={"vote": -5, "id": reviewer_id},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()
        if body.strip():
            try:
                await self.post_comment(pr, body)
            except Exception as exc:
                log.error(
                    "ado_request_changes_comment_failed",
                    pr_id=pr.pr_id,
                    repo=pr.repo,
                    vote_cast=-5,
                    error=str(exc),
                )
                raise

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

    async def fetch_pr_metadata(self, pr: PlatformPR) -> PlatformPRMetadata:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}"
        )
        resp = await client.get(url, params={"api-version": "7.1"})
        resp.raise_for_status()
        data = resp.json()
        status = (data.get("status") or "").lower()
        source_repo = data.get("sourceRepository") or {}
        target_repo = data.get("repository") or {}
        source_repo_id = source_repo.get("id") or source_repo.get("name") or ""
        target_repo_id = target_repo.get("id") or target_repo.get("name") or pr.repo
        return PlatformPRMetadata(
            head_sha=data.get("lastMergeSourceCommit", {}).get("commitId") or pr.head_commit_sha,
            draft=bool(data.get("isDraft")),
            fork=bool(source_repo_id and source_repo_id != target_repo_id),
            closed=status in {"completed", "abandoned"},
            merged=status == "completed",
        )

    @staticmethod
    def _is_human_review_signal(signal: dict) -> bool:
        context = signal.get("context") or {}
        text = " ".join(
            str(part).lower()
            for part in (
                context.get("name", ""),
                context.get("genre", ""),
                signal.get("description", ""),
                signal.get("targetUrl", ""),
            )
        )
        return any(marker in text for marker in ("reviewer", "approval", "vote", "manual"))

    async def fetch_readiness_signals(self, pr: PlatformPR) -> list[PlatformReadinessSignal]:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/statuses"
        )
        resp = await client.get(url, params={"api-version": "7.1"})
        resp.raise_for_status()
        signals: list[PlatformReadinessSignal] = []
        for status in resp.json().get("value", []) or []:
            if self._is_human_review_signal(status):
                continue
            context = status.get("context") or {}
            name = context.get("name") or status.get("targetUrl") or "ado-status"
            signals.append(
                PlatformReadinessSignal(
                    name=name,
                    state=status.get("state") or "pending",
                    source=context.get("genre") or "ado_status",
                    url=status.get("targetUrl") or "",
                    description=status.get("description") or "",
                )
            )
        return signals

    async def set_readiness_status(self, pr: PlatformPR, state: str, description: str) -> None:
        await self.set_status(pr, state, description, context="guardian/readiness")

    async def set_review_status(self, pr: PlatformPR, state: str, description: str) -> None:
        await self.set_status(pr, state, description, context="guardian/review")

    async def find_archmap_artifact(self, pr: PlatformPR, head_sha: str) -> bool:
        client = self._get_client()
        artifact_name = f"archmap-{head_sha}"
        builds_url = f"{self._org_url}/{pr.project}/_apis/build/builds"
        builds_resp = await client.get(
            builds_url,
            params={
                "sourceVersion": head_sha,
                "queryOrder": "finishTimeDescending",
                "api-version": "7.1",
            },
        )
        builds_resp.raise_for_status()
        for build in builds_resp.json().get("value", []) or []:
            build_id = build.get("id")
            if build_id is None:
                continue
            artifact_resp = await client.get(
                f"{self._org_url}/{pr.project}/_apis/build/builds/{build_id}/artifacts",
                params={"artifactName": artifact_name, "api-version": "7.1"},
            )
            if artifact_resp.status_code == 404:
                continue
            artifact_resp.raise_for_status()
            value = artifact_resp.json().get("value")
            if isinstance(value, list):
                if any(item.get("name") == artifact_name for item in value):
                    return True
            elif artifact_resp.json().get("name") == artifact_name:
                return True
        return False

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        log.info("ado_request_reviewers", pr_id=pr.pr_id, group=group)
        # ADO uses reviewer IDs — would need group resolution via API
        # For now, log the intent

    async def post_inline_comments(
        self,
        pr: PlatformPR,
        findings: list[Finding],
        *,
        threshold: str = "MEDIUM",
    ) -> list[str]:
        client = self._get_client()
        grouped: dict[tuple[str, int], list[Finding]] = {}
        for f in findings:
            if f.line is None:
                continue
            grouped.setdefault((f.file, f.line), []).append(f)

        ids: list[str] = []
        for (file, line), group in grouped.items():
            body = inline_comment_body(group)
            file_path = f"/{file}" if not file.startswith("/") else file
            url = (
                f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
                f"/pullRequests/{pr.pr_id}/threads"
            )
            try:
                resp = await client.post(
                    url,
                    json={
                        "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
                        "status": 1,
                        "threadContext": {
                            "filePath": file_path,
                            "rightFileStart": {"line": line, "offset": 1},
                            "rightFileEnd": {"line": line, "offset": 200},
                        },
                    },
                    params={"api-version": "7.1"},
                )
                resp.raise_for_status()
                thread_id = resp.json().get("id")
                if thread_id is not None:
                    ids.append(str(thread_id))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422:
                    log.debug(
                        "ado_inline_comment_skipped",
                        file=file,
                        line=line,
                        reason="line_not_in_diff",
                    )
                else:
                    raise
        return ids

    async def delete_inline_comments(
        self,
        pr: PlatformPR,
        comment_ids: list[str],
    ) -> None:
        client = self._get_client()
        for thread_id in comment_ids:
            thread_url = (
                f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
                f"/pullRequests/{pr.pr_id}/threads/{thread_id}"
            )
            try:
                resp = await client.patch(
                    thread_url,
                    json={"status": 4},
                    params={"api-version": "7.1"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    log.debug("ado_delete_thread_not_found", thread_id=thread_id)
                    continue
                raise
            comments_url = thread_url + "/comments"
            resp2 = await client.post(
                comments_url,
                json={
                    "parentCommentId": 1,
                    "content": "This comment was superseded by a re-review.",
                    "commentType": 1,
                },
                params={"api-version": "7.1"},
            )
            try:
                resp2.raise_for_status()
            except httpx.HTTPStatusError:
                log.warning("ado_delete_reply_failed", thread_id=thread_id)

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
        self,
        repo: str,
        branch: str,
        since: str,
        until: str | None = None,
        per_page: int = 100,
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
                all_commits.append(
                    {
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
                    }
                )
            if len(batch) < per_page:
                break
            skip += len(batch)
        return all_commits

    async def fetch_merged_prs(
        self,
        repo: str,
        since: str,
        base: str = "main",
    ) -> list[dict]:
        """Fetch recently merged (completed) PRs.

        Normalizes to GitHub-compatible dict shape for scan agents.
        """
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        url = f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/pullrequests"
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
                merged.append(
                    {
                        "number": pr.get("pullRequestId"),
                        "title": pr.get("title", ""),
                        "user": {"login": pr.get("createdBy", {}).get("uniqueName", "")},
                        "created_at": pr.get("creationDate"),
                        "merged_at": closed_date,
                        "base": {"ref": base},
                        "_ado_project": project,
                        "_ado_repo": repo_name,
                    }
                )
        return merged

    async def fetch_file_content(
        self,
        repo: str,
        path: str,
        ref: str = "HEAD",
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
                client,
                sem,
                project,
                repo_name,
                path,
                version,
                version_type,
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
        self,
        repo: str,
        ref: str = "HEAD",
        path: str = "",
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

    async def list_recently_changed_files(
        self,
        repo: str,
        ref: str = "HEAD",
        limit: int = 300,
    ) -> list[str]:
        """Walk recent commits on ``ref`` and return up to ``limit`` unique paths.

        Lists commits newest-first via the ADO commits API, then for each commit
        fetches the changes (with file paths) in parallel. Stops early once we
        have enough unique paths.
        """
        client = self._get_client()
        project, repo_name = self._parse_repo(repo)
        commit_scan_limit = max(200, limit * 2)
        top = min(commit_scan_limit, 100)

        commits_url = f"{self._org_url}/{project}/_apis/git/repositories/{repo_name}/commits"

        # 1. List commit IDs newest-first
        ids: list[str] = []
        skip = 0
        while len(ids) < commit_scan_limit:
            params: dict = {
                "$top": top,
                "$skip": skip,
                "api-version": "7.1",
            }
            if ref and ref != "HEAD":
                params["searchCriteria.itemVersion.version"] = ref
                if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower()):
                    params["searchCriteria.itemVersion.versionType"] = "commit"
                else:
                    params["searchCriteria.itemVersion.versionType"] = "branch"
            resp = await client.get(commits_url, params=params)
            resp.raise_for_status()
            batch = resp.json().get("value", [])
            if not batch:
                break
            ids.extend(c.get("commitId", "") for c in batch if c.get("commitId"))
            if len(batch) < top:
                break
            skip += len(batch)
        ids = ids[:commit_scan_limit]

        # 2. Fetch per-commit changes in parallel
        sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
        results: list[list[str] | None] = [None] * len(ids)

        async def _fetch(idx: int, commit_id: str) -> None:
            async with sem:
                try:
                    url = (
                        f"{self._org_url}/{project}/_apis/git/repositories/"
                        f"{repo_name}/commits/{commit_id}/changes"
                    )
                    r = await client.get(url, params={"api-version": "7.1"})
                    r.raise_for_status()
                    changes = r.json().get("changes", []) or []
                    paths: list[str] = []
                    for ch in changes:
                        item = ch.get("item") or {}
                        if item.get("gitObjectType") == "tree":
                            continue
                        p = (item.get("path") or "").lstrip("/")
                        if p:
                            paths.append(p)
                    results[idx] = paths
                except Exception as e:
                    log.debug("ado_commit_changes_failed", commit=commit_id, error=str(e))
                    results[idx] = []

        await asyncio.gather(*(_fetch(i, c) for i, c in enumerate(ids)))

        # 3. Dedupe in commit order
        seen: set[str] = set()
        ordered: list[str] = []
        for files in results:
            if not files:
                continue
            for path in files:
                if path and path not in seen:
                    seen.add(path)
                    ordered.append(path)
                    if len(ordered) >= limit:
                        return ordered
        return ordered

    async def fetch_compare_diff(
        self,
        repo: str,
        base_sha: str,
        head_sha: str,
        project: str = "",
    ) -> Diff:
        """Fetch diff between two commits. ADO uses the commits diff API."""
        if not project:
            raise ValueError("ADO fetch_compare_diff requires a project")
        proj = project
        client = self._get_client()
        resp = await client.get(
            f"{self._org_url}/{proj}/_apis/git/repositories/{repo}/diffs/commits",
            params={
                "baseVersion": base_sha,
                "baseVersionType": "commit",
                "targetVersion": head_sha,
                "targetVersionType": "commit",
                "api-version": "7.1",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        diff_files: list[DiffFile] = []
        for change in data.get("changes", []):
            item = change.get("item", {})
            path = item.get("path", "").lstrip("/")
            if item.get("isFolder"):
                continue
            change_type = change.get("changeType", "edit").lower()
            status_map: dict[str, FileStatus] = {
                "add": "added",
                "delete": "deleted",
                "edit": "modified",
                "rename": "renamed",
            }
            diff_files.append(
                DiffFile(
                    path=path,
                    status=status_map.get(change_type, "modified"),
                    old_path=change.get("sourceServerItem", {}).get("path")
                    if change_type == "rename"
                    else None,
                )
            )
        return Diff(files=diff_files)

    async def fetch_pr_files(
        self,
        repo: str,
        pr_id: int | str,
        project: str = "",
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
            files.append(
                {
                    "filename": raw_path,
                    "additions": 0,
                    "deletions": 0,
                    "status": change.get("changeType", "edit").lower(),
                }
            )
        return files

    async def fetch_commits_for_path(
        self,
        repo: str,
        path: str,
        per_page: int = 1,
        project: str = "",
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

    async def fetch_pr_body_and_commits(
        self,
        pr: PlatformPR,
    ) -> tuple[str, list[str]]:
        """Fetch the PR description and commit messages for capability clustering.

        The body is skipped when already present on `pr` (populated by _hydrate_pr),
        avoiding a second GET to the same endpoint.
        """
        client = self._get_client()
        # None → body not yet fetched; "" → fetched but PR has no description.
        pr_body: str = pr.body if pr.body is not None else ""
        commit_messages: list[str] = []
        if pr.body is None:
            try:
                pr_url = (
                    f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
                    f"/pullRequests/{pr.pr_id}"
                )
                pr_resp = await client.get(pr_url, params={"api-version": "7.1"})
                pr_resp.raise_for_status()
                pr_body = pr_resp.json().get("description") or ""
            except Exception as exc:
                log.warning("ado_fetch_pr_body_failed", pr_id=pr.pr_id, error=str(exc))
        try:
            commits_url = (
                f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
                f"/pullRequests/{pr.pr_id}/commits"
            )
            commits_resp = await client.get(commits_url, params={"api-version": "7.1"})
            commits_resp.raise_for_status()
            commit_messages = [
                c.get("comment", "").split("\n")[0].strip()
                for c in commits_resp.json().get("value", [])
                if c.get("comment")
            ]
        except Exception as exc:
            log.warning("ado_fetch_pr_commits_failed", pr_id=pr.pr_id, error=str(exc))
        return pr_body, commit_messages

    async def list_projects(self) -> list[dict]:
        """List all projects in the ADO organization."""
        client = self._get_client()
        resp = await client.get(
            f"{self._org_url}/_apis/projects",
            params={"api-version": "7.1", "$top": 200},
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    async def list_repos(self, project: str) -> list[dict]:
        """List all git repos in a project."""
        client = self._get_client()
        resp = await client.get(
            f"{self._org_url}/{project}/_apis/git/repositories",
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    async def list_repo_open_prs(self, project: str, repo: str) -> list[dict]:
        """List active (open) PRs for a repo."""
        client = self._get_client()
        resp = await client.get(
            f"{self._org_url}/{project}/_apis/git/repositories/{repo}/pullrequests",
            params={
                "searchCriteria.status": "active",
                "$top": 100,
                "api-version": "7.1",
            },
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    async def create_work_item(
        self,
        project: str,
        title: str,
        body: str,
        work_item_type: str = "Bug",
    ) -> dict:
        """Create an ADO work item. Returns dict with 'id' and 'url'."""
        client = self._get_client()
        patch_doc = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": body},
        ]
        resp = await client.post(
            f"{self._org_url}/{project}/_apis/wit/workitems/${work_item_type}",
            content=json.dumps(patch_doc),
            headers={"Content-Type": "application/json-patch+json"},
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("_links", {}).get("html", {}).get("href", "")
        return {"id": data.get("id"), "url": url}

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
