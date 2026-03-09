from __future__ import annotations

import base64

import httpx
import structlog

from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()


class GitHubAdapter:
    """GitHub platform adapter using REST API."""

    def __init__(self, token: str = "", app_id: str = "", private_key: str = ""):
        self._token = token
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers={
                    "Authorization": f"token {self._token}",
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
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

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
