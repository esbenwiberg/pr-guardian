from __future__ import annotations

import base64

import httpx
import structlog

from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()


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

    async def fetch_diff(self, pr: PlatformPR) -> Diff:
        client = self._get_client()
        url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/iterations"
        )
        resp = await client.get(url, params={"api-version": "7.1"})
        resp.raise_for_status()
        iterations = resp.json().get("value", [])

        if not iterations:
            return Diff()

        last_iter = iterations[-1]["id"]
        changes_url = (
            f"{self._org_url}/{pr.project}/_apis/git/repositories/{pr.repo}"
            f"/pullRequests/{pr.pr_id}/iterations/{last_iter}/changes"
        )
        resp = await client.get(changes_url, params={"api-version": "7.1"})
        resp.raise_for_status()

        diff_files: list[DiffFile] = []
        for change in resp.json().get("changeEntries", []):
            item = change.get("item", {})
            change_type = change.get("changeType", "edit")
            status_map = {"add": "added", "delete": "deleted", "edit": "modified", "rename": "renamed"}
            diff_files.append(DiffFile(
                path=item.get("path", "").lstrip("/"),
                status=status_map.get(change_type.lower(), "modified"),
                old_path=change.get("sourceServerItem"),
                additions=0,
                deletions=0,
            ))
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

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
