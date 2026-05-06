from __future__ import annotations

import base64

import httpx
import structlog

from pr_guardian.models.findings import Finding
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform._utils import inline_comment_body
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()


class GitHubAdapter:
    """GitHub platform adapter using REST API."""

    def __init__(self, token: str = "", app_id: str = "", private_key: str = ""):
        self._token = token
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers: dict[str, str] = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"token {self._token}"
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers=headers,
                timeout=30.0,
            )
        return self._client

    @staticmethod
    def normalize_webhook(payload: WebhookPayload) -> PlatformPR | None:
        """Normalize GitHub webhook payload to PlatformPR."""
        body = payload.body
        action = body.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            return None

        pr = body.get("pull_request", {})
        repo = body.get("repository", {})

        return PlatformPR(
            platform=Platform.GITHUB,
            pr_id=str(pr.get("number", "")),
            repo=repo.get("full_name", ""),
            repo_url=repo.get("clone_url", ""),
            source_branch=pr.get("head", {}).get("ref", ""),
            target_branch=pr.get("base", {}).get("ref", ""),
            author=pr.get("user", {}).get("login", ""),
            title=pr.get("title", ""),
            head_commit_sha=pr.get("head", {}).get("sha", ""),
            org=repo.get("owner", {}).get("login", ""),
            install_id=body.get("installation", {}).get("id"),
        )

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        client = self._get_client()
        resp = await client.get(
            f"/repos/{pr.repo}/pulls/{pr.pr_id}/files",
            params={"per_page": 300},
        )
        resp.raise_for_status()
        files_data = resp.json()

        diff_files: list[DiffFile] = []
        for f in files_data:
            status_map = {
                "added": "added", "removed": "deleted",
                "modified": "modified", "renamed": "renamed",
            }
            diff_files.append(DiffFile(
                path=f.get("filename", ""),
                status=status_map.get(f.get("status", ""), "modified"),
                old_path=f.get("previous_filename"),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch", ""),
            ))
        return Diff(files=diff_files)

    async def post_comment(self, pr: PlatformPR, body: str) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/issues/{pr.pr_id}/comments",
            json={"body": body},
        )
        resp.raise_for_status()

    async def approve_pr(self, pr: PlatformPR) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/pulls/{pr.pr_id}/reviews",
            json={"event": "APPROVE", "body": "PR Guardian: Auto-approved."},
        )
        resp.raise_for_status()

    async def request_changes(self, pr: PlatformPR, body: str) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/pulls/{pr.pr_id}/reviews",
            json={"event": "REQUEST_CHANGES", "body": body},
        )
        resp.raise_for_status()

    async def add_label(self, pr: PlatformPR, label: str) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/issues/{pr.pr_id}/labels",
            json={"labels": [label]},
        )
        resp.raise_for_status()

    async def set_status(
        self, pr: PlatformPR, state: str, description: str, context: str = "pr-guardian"
    ) -> None:
        client = self._get_client()
        state_map = {"success": "success", "failure": "failure", "pending": "pending"}
        resp = await client.post(
            f"/repos/{pr.repo}/statuses/{pr.head_commit_sha}",
            json={
                "state": state_map.get(state, "pending"),
                "description": description[:140],
                "context": context,
            },
        )
        resp.raise_for_status()

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/pulls/{pr.pr_id}/requested_reviewers",
            json={"team_reviewers": [group]},
        )
        resp.raise_for_status()

    # --- Scan-mode methods ---

    async def fetch_recent_commits(
        self, repo: str, branch: str, since: str, until: str | None = None, per_page: int = 100,
    ) -> list[dict]:
        """Fetch commits on branch since a date (ISO 8601)."""
        client = self._get_client()
        params: dict = {"sha": branch, "since": since, "per_page": per_page}
        if until:
            params["until"] = until

        all_commits: list[dict] = []
        page = 1
        while True:
            params["page"] = page
            resp = await client.get(f"/repos/{repo}/commits", params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_commits.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return all_commits

    async def fetch_merged_prs(
        self, repo: str, since: str, base: str = "main",
    ) -> list[dict]:
        """Fetch recently merged PRs (closed + merged_at >= since)."""
        client = self._get_client()
        params: dict = {
            "state": "closed",
            "base": base,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }
        resp = await client.get(f"/repos/{repo}/pulls", params=params)
        resp.raise_for_status()
        all_prs = resp.json()

        # Filter to actually merged PRs with merged_at >= since
        merged = []
        for pr in all_prs:
            merged_at = pr.get("merged_at")
            if merged_at and merged_at >= since:
                merged.append(pr)
        return merged

    async def fetch_file_content(
        self, repo: str, path: str, ref: str = "HEAD",
    ) -> str:
        """Fetch file content from the repo."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/contents/{path}",
            params={"ref": ref},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content", "")

    async def list_repo_files(
        self, repo: str, ref: str = "HEAD", path: str = "",
    ) -> list[str]:
        """List files in repo (recursive tree)."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        resp.raise_for_status()
        tree = resp.json().get("tree", [])
        return [item["path"] for item in tree if item.get("type") == "blob"]

    async def fetch_compare_diff(
        self, repo: str, base_sha: str, head_sha: str, project: str = "",
    ) -> Diff:
        """Fetch diff between two commits using the compare API."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/compare/{base_sha}...{head_sha}",
        )
        resp.raise_for_status()
        data = resp.json()

        diff_files: list[DiffFile] = []
        for f in data.get("files", []):
            status_map = {
                "added": "added", "removed": "deleted",
                "modified": "modified", "renamed": "renamed",
            }
            diff_files.append(DiffFile(
                path=f.get("filename", ""),
                status=status_map.get(f.get("status", ""), "modified"),
                old_path=f.get("previous_filename"),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch", ""),
            ))
        return Diff(files=diff_files)

    async def fetch_pr_files(
        self, repo: str, pr_id: int | str, project: str = "",
    ) -> list[dict]:
        """Fetch changed files for a PR."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/pulls/{pr_id}/files",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_commits_for_path(
        self, repo: str, path: str, per_page: int = 1, project: str = "",
    ) -> list[dict]:
        """Fetch recent commits touching a specific file."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/commits",
            params={"path": path, "per_page": per_page},
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_pr_body_and_commits(
        self, pr: PlatformPR,
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
                pr_resp = await client.get(f"/repos/{pr.repo}/pulls/{pr.pr_id}")
                pr_resp.raise_for_status()
                pr_body = pr_resp.json().get("body") or ""
            except Exception as exc:
                log.warning("github_fetch_pr_body_failed", pr_id=pr.pr_id, error=str(exc))
        try:
            commits_resp = await client.get(
                f"/repos/{pr.repo}/pulls/{pr.pr_id}/commits",
                params={"per_page": 50},
            )
            commits_resp.raise_for_status()
            commit_messages = [
                c.get("commit", {}).get("message", "").split("\n")[0].strip()
                for c in commits_resp.json()
                if c.get("commit", {}).get("message")
            ]
        except Exception as exc:
            log.warning("github_fetch_pr_commits_failed", pr_id=pr.pr_id, error=str(exc))
        return pr_body, commit_messages

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
            try:
                resp = await client.post(
                    f"/repos/{pr.repo}/pulls/{pr.pr_id}/reviews",
                    json={
                        "event": "COMMENT",
                        "comments": [{"path": file, "line": line, "body": body}],
                    },
                )
                resp.raise_for_status()
                for c in resp.json().get("comments", []):
                    if "id" in c:
                        ids.append(str(c["id"]))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422:
                    log.debug(
                        "github_inline_comment_skipped",
                        file=file, line=line, reason="line_not_in_diff",
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
        for comment_id in comment_ids:
            resp = await client.delete(
                f"/repos/{pr.repo}/pulls/comments/{comment_id}",
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    log.debug("github_delete_comment_not_found", comment_id=comment_id)
                else:
                    raise

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a GitHub issue. Returns dict with 'number' and 'html_url'."""
        client = self._get_client()
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        resp = await client.post(f"/repos/{repo}/issues", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {"number": data.get("number"), "url": data.get("html_url", "")}

    async def list_accessible_repos(self) -> list[dict]:
        """List repos the token has access to (owned + collaborated + org member)."""
        client = self._get_client()
        repos: list[dict] = []
        page = 1
        while len(repos) < 500:
            resp = await client.get(
                "/user/repos",
                params={"type": "all", "per_page": 100, "page": page, "sort": "pushed"},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return repos

    async def list_repo_open_prs(self, repo: str) -> list[dict]:
        """List open PRs for a repo, enriched with review approval state."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/pulls",
            params={"state": "open", "per_page": 100},
        )
        resp.raise_for_status()
        prs = resp.json()

        for pr in prs:
            try:
                rev_resp = await client.get(
                    f"/repos/{repo}/pulls/{pr['number']}/reviews",
                    params={"per_page": 100},
                )
                rev_resp.raise_for_status()
                pr["_reviews"] = rev_resp.json()
            except Exception:
                pr["_reviews"] = []
        return prs

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
