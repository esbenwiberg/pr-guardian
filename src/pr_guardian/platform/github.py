from __future__ import annotations

import base64
from io import BytesIO
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

import httpx
import structlog

from pr_guardian.models.findings import Finding
from pr_guardian.models.pr import Diff, DiffFile, FileStatus, Platform, PlatformPR
from pr_guardian.platform._utils import inline_comment_body
from pr_guardian.platform.models import WebhookPayload
from pr_guardian.platform.protocol import (
    InlinePostResult,
    PlatformPRMetadata,
    PlatformReadinessSignal,
)

if TYPE_CHECKING:
    from pr_guardian.platform.github_auth import GitHubAppAuth

log = structlog.get_logger()

_ARCHMAP_ARTIFACT_MAX_BYTES = 2_000_000
_ARCHMAP_ARTIFACT_FILE = "archmap.json"


def _compute_ci_status(
    runs: list[dict],
    statuses: list[dict] | None = None,
    *,
    combined_status_state: str | None = None,
) -> str:
    """Derive overall CI status from GitHub check runs and commit statuses."""
    statuses = statuses or []
    if not runs and not statuses and not combined_status_state:
        return "unknown"
    in_progress = any(r.get("status") != "completed" for r in runs)
    conclusions = {
        r.get("conclusion") for r in runs if r.get("status") == "completed" and r.get("conclusion")
    }
    status_states = {s.get("state") for s in statuses if s.get("state")}
    effective_status_states = {combined_status_state} if combined_status_state else status_states
    failure_conclusions = {"failure", "timed_out", "action_required", "cancelled", "startup_failure"}
    failure_states = {"failure", "error"}
    if any(c in failure_conclusions for c in conclusions):
        return "failure"
    if any(s in failure_states for s in effective_status_states):
        return "failure"
    if in_progress or "pending" in effective_status_states:
        return "pending"
    has_checks = bool(runs)
    has_statuses = bool(statuses) or combined_status_state is not None
    checks_success = (not has_checks) or (
        conclusions and all(c in ("success", "neutral", "skipped") for c in conclusions)
    )
    statuses_success = (not has_statuses) or (
        effective_status_states.issubset({"success"})
    )
    if (has_checks or has_statuses) and checks_success and statuses_success:
        return "success"
    return "unknown"


def _extract_archmap_json(zip_bytes: bytes) -> str | None:
    """Extract archmap.json from a GitHub Actions artifact zip."""
    try:
        with ZipFile(BytesIO(zip_bytes)) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename.endswith(_ARCHMAP_ARTIFACT_FILE)
            ]
            if not matches:
                return None
            info = matches[0]
            if info.file_size > _ARCHMAP_ARTIFACT_MAX_BYTES:
                raise ValueError("archmap artifact is too large")
            return archive.read(info).decode("utf-8", errors="replace")
    except BadZipFile:
        return None


class GitHubAdapter:
    """GitHub platform adapter using REST API.

    Accepts either:
    - ``app_auth``: a GitHubAppAuth instance that mints installation tokens
      (preferred for all runtime GitHub App paths).
    - ``token``: a static PAT or test token (used in tests and legacy paths).

    When ``app_auth`` is provided the HTTP client uses Bearer auth that calls
    ``app_auth.get_token()`` on every request, transparently refreshing the
    installation token before expiry.
    """

    def __init__(self, token: str = "", *, app_auth: GitHubAppAuth | None = None):
        self._token = token
        self._app_auth = app_auth
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            from pr_guardian.platform.github_auth import _InstallationBearerAuth

            headers: dict[str, str] = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._app_auth is not None:
                self._client = httpx.AsyncClient(
                    base_url="https://api.github.com",
                    headers=headers,
                    auth=_InstallationBearerAuth(self._app_auth.get_token),
                    timeout=30.0,
                )
            else:
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
            status_map: dict[str, FileStatus] = {
                "added": "added",
                "removed": "deleted",
                "modified": "modified",
                "renamed": "renamed",
            }
            diff_files.append(
                DiffFile(
                    path=f.get("filename", ""),
                    status=status_map.get(f.get("status", ""), "modified"),
                    old_path=f.get("previous_filename"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch", ""),
                )
            )
        return Diff(files=diff_files)

    async def fetch_archmap_artifact(self, pr: PlatformPR) -> str | None:
        """Download archmap-<head_sha> from GitHub Actions artifacts if it exists."""
        if not pr.head_commit_sha:
            return None

        client = self._get_client()
        artifact_name = f"archmap-{pr.head_commit_sha}"
        resp = await client.get(
            f"/repos/{pr.repo}/actions/artifacts",
            params={"name": artifact_name, "per_page": 100},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        artifacts = [
            artifact
            for artifact in resp.json().get("artifacts", [])
            if artifact.get("name") == artifact_name and not artifact.get("expired", False)
        ]
        if not artifacts:
            return None

        artifacts.sort(key=lambda artifact: artifact.get("created_at", ""), reverse=True)
        artifact_id = artifacts[0].get("id")
        if artifact_id is None:
            return None

        zip_resp = await client.get(
            f"/repos/{pr.repo}/actions/artifacts/{artifact_id}/zip",
            follow_redirects=True,
        )
        if zip_resp.status_code == 404:
            return None
        zip_resp.raise_for_status()
        return _extract_archmap_json(zip_resp.content)

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
        self,
        pr: PlatformPR,
        state: str,
        description: str,
        context: str = "pr-guardian",
        target_url: str = "",
    ) -> None:
        client = self._get_client()
        state_map = {"success": "success", "failure": "failure", "pending": "pending"}
        payload = {
            "state": state_map.get(state, "pending"),
            "description": description[:140],
            "context": context,
        }
        if target_url:
            payload["target_url"] = target_url
        resp = await client.post(
            f"/repos/{pr.repo}/statuses/{pr.head_commit_sha}",
            json=payload,
        )
        resp.raise_for_status()

    async def fetch_pr_metadata(self, pr: PlatformPR) -> PlatformPRMetadata:
        client = self._get_client()
        resp = await client.get(f"/repos/{pr.repo}/pulls/{pr.pr_id}")
        resp.raise_for_status()
        data = resp.json()
        head = data.get("head", {}) or {}
        base = data.get("base", {}) or {}
        head_repo = head.get("repo") or {}
        base_repo = base.get("repo") or {}
        head_full_name = head_repo.get("full_name") or ""
        base_full_name = base_repo.get("full_name") or pr.repo
        return PlatformPRMetadata(
            head_sha=head.get("sha") or pr.head_commit_sha,
            draft=bool(data.get("draft")),
            fork=bool(head_full_name and head_full_name != base_full_name),
            closed=data.get("state") == "closed",
            merged=bool(data.get("merged")),
        )

    async def fetch_pr(self, repo: str, pr_id: str | int) -> PlatformPR:
        """Fetch a full GitHub PR as Guardian's normalized PlatformPR."""
        client = self._get_client()
        resp = await client.get(f"/repos/{repo}/pulls/{pr_id}")
        resp.raise_for_status()
        data = resp.json()
        owner = repo.split("/", 1)[0] if "/" in repo else ""
        return PlatformPR(
            platform=Platform.GITHUB,
            pr_id=str(pr_id),
            repo=repo,
            repo_url=data.get("base", {}).get("repo", {}).get("clone_url", ""),
            source_branch=data.get("head", {}).get("ref", ""),
            target_branch=data.get("base", {}).get("ref", ""),
            author=data.get("user", {}).get("login", ""),
            title=data.get("title", ""),
            head_commit_sha=data.get("head", {}).get("sha", ""),
            body=data.get("body") or "",
            org=owner,
        )

    async def list_issue_comments(self, repo: str, pr_id: str | int) -> list[dict]:
        """List PR conversation comments from GitHub's issue-comments API."""
        client = self._get_client()
        comments: list[dict] = []
        page = 1
        while page <= 10:
            resp = await client.get(
                f"/repos/{repo}/issues/{pr_id}/comments",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return comments

    async def fetch_readiness_signals(self, pr: PlatformPR) -> list[PlatformReadinessSignal]:
        client = self._get_client()
        signals: list[PlatformReadinessSignal] = []
        # Check runs (Checks API) give full fidelity — every app's check run, not just
        # GitHub Actions. But the Checks API is unreadable by fine-grained PATs (GitHub
        # offers no Checks permission for them). When that read is denied, fall back to
        # the Actions API, which a fine-grained PAT *can* read with the Actions scope.
        # The fallback only sees GitHub Actions runs, not third-party app check runs
        # (e.g. CodeRabbit) — a known gap that disappears under GitHub App auth.
        try:
            checks_resp = await client.get(
                f"/repos/{pr.repo}/commits/{pr.head_commit_sha}/check-runs",
                params={"per_page": 100},
            )
            checks_resp.raise_for_status()
            for run in checks_resp.json().get("check_runs", []) or []:
                name = run.get("name") or run.get("app", {}).get("name") or "check"
                if run.get("status") == "completed":
                    state = run.get("conclusion") or "unknown"
                else:
                    state = run.get("status") or "pending"
                signals.append(
                    PlatformReadinessSignal(
                        name=name,
                        state=state,
                        source="check_run",
                        url=run.get("html_url") or "",
                        description=run.get("output", {}).get("title") or "",
                    )
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 404):
                raise
            log.warning(
                "github_check_runs_unreadable_using_actions_fallback",
                repo=pr.repo,
                head_sha=pr.head_commit_sha,
                status=exc.response.status_code,
            )
            signals.extend(await self._actions_run_signals(client, pr))

        statuses_resp = await client.get(f"/repos/{pr.repo}/commits/{pr.head_commit_sha}/status")
        statuses_resp.raise_for_status()
        for status in statuses_resp.json().get("statuses", []) or []:
            signals.append(
                PlatformReadinessSignal(
                    name=status.get("context") or "status",
                    state=status.get("state") or "pending",
                    source="status",
                    url=status.get("target_url") or "",
                    description=status.get("description") or "",
                )
            )
        return signals

    async def _actions_run_signals(
        self, client: httpx.AsyncClient, pr: PlatformPR
    ) -> list[PlatformReadinessSignal]:
        """Read GitHub Actions workflow runs as a stand-in for check runs when the
        Checks API is unreadable (fine-grained PAT). Needs only the Actions read scope.
        Runs are treated as ``check_run`` signals so downstream gating and the
        ``ignored_checks`` filter handle them identically to real check runs. If the
        Actions scope is *also* missing, degrade to statuses-only rather than wedge."""
        try:
            resp = await client.get(
                f"/repos/{pr.repo}/actions/runs",
                params={"head_sha": pr.head_commit_sha, "per_page": 100},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 404):
                raise
            log.warning(
                "github_actions_runs_unreadable_statuses_only",
                repo=pr.repo,
                head_sha=pr.head_commit_sha,
                status=exc.response.status_code,
            )
            return []
        signals: list[PlatformReadinessSignal] = []
        for run in resp.json().get("workflow_runs", []) or []:
            name = run.get("name") or run.get("display_title") or "workflow"
            if run.get("status") == "completed":
                state = run.get("conclusion") or "unknown"
            else:
                state = run.get("status") or "pending"
            signals.append(
                PlatformReadinessSignal(
                    name=name,
                    state=state,
                    source="check_run",
                    url=run.get("html_url") or "",
                    description="",
                )
            )
        return signals

    async def upsert_guidance_comment(
        self,
        pr: PlatformPR,
        body: str,
        *,
        stored_comment_id: str | None = None,
    ) -> str:
        """Create or update the sticky guidance comment on a PR.

        Recovery order:
        1. If stored_comment_id is given, attempt PATCH; skip recovery on 404.
        2. If no stored ID or the stored comment was deleted, scan PR comments
           for the hidden marker and patch that comment.
        3. If not found at all, create a new comment.

        Returns the platform comment ID (new or existing).
        """
        from pr_guardian.decision.actions import GUIDANCE_MARKER

        client = self._get_client()

        if stored_comment_id:
            resp = await client.patch(
                f"/repos/{pr.repo}/issues/comments/{stored_comment_id}",
                json={"body": body},
            )
            if resp.status_code != 404:
                resp.raise_for_status()
                return stored_comment_id
            # 404: comment was deleted — fall through to search/recreate

        # Scan existing PR comments for the hidden marker
        comments = await self.list_issue_comments(pr.repo, pr.pr_id)
        for c in comments:
            if GUIDANCE_MARKER in (c.get("body") or ""):
                found_id = str(c["id"])
                resp = await client.patch(
                    f"/repos/{pr.repo}/issues/comments/{found_id}",
                    json={"body": body},
                )
                resp.raise_for_status()
                return found_id

        # No existing comment — create one
        resp = await client.post(
            f"/repos/{pr.repo}/issues/{pr.pr_id}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return str(resp.json()["id"])

    async def set_readiness_status(self, pr: PlatformPR, state: str, description: str) -> None:
        await self.set_status(pr, state, description, context="guardian/readiness")

    async def set_review_status(
        self, pr: PlatformPR, state: str, description: str, target_url: str = ""
    ) -> None:
        await self.set_status(
            pr, state, description, context="guardian/review", target_url=target_url
        )

    async def find_archmap_artifact(self, pr: PlatformPR, head_sha: str) -> bool:
        client = self._get_client()
        artifact_name = f"archmap-{head_sha}"
        resp = await client.get(
            f"/repos/{pr.repo}/actions/artifacts",
            params={"name": artifact_name, "per_page": 1},
        )
        resp.raise_for_status()
        artifacts = resp.json().get("artifacts", []) or []
        return any(
            a.get("name") == artifact_name and not a.get("expired", False) for a in artifacts
        )

    async def request_reviewers(self, pr: PlatformPR, group: str) -> None:
        client = self._get_client()
        resp = await client.post(
            f"/repos/{pr.repo}/pulls/{pr.pr_id}/requested_reviewers",
            json={"team_reviewers": [group]},
        )
        resp.raise_for_status()

    # --- Scan-mode methods ---

    async def fetch_recent_commits(
        self,
        repo: str,
        branch: str,
        since: str,
        until: str | None = None,
        per_page: int = 100,
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
        self,
        repo: str,
        since: str,
        base: str = "main",
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
        self,
        repo: str,
        path: str,
        ref: str = "HEAD",
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
        self,
        repo: str,
        ref: str = "HEAD",
        path: str = "",
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

    async def list_recently_changed_files(
        self,
        repo: str,
        ref: str = "HEAD",
        limit: int = 300,
    ) -> list[str]:
        """Walk recent commits on ``ref`` and return up to ``limit`` unique paths.

        Strategy: list commits newest-first, fetch each commit's file list in
        parallel (concurrency-limited), accumulate unique paths in commit order.
        Stops early once we have enough paths.
        """
        import asyncio

        client = self._get_client()
        commit_scan_limit = max(200, limit * 2)
        per_page = 100

        # 1. List commit SHAs newest-first
        shas: list[str] = []
        page = 1
        params_base: dict = {"per_page": per_page}
        if ref and ref != "HEAD":
            params_base["sha"] = ref
        while len(shas) < commit_scan_limit:
            params = dict(params_base, page=page)
            resp = await client.get(f"/repos/{repo}/commits", params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            shas.extend(c.get("sha", "") for c in batch if c.get("sha"))
            if len(batch) < per_page:
                break
            page += 1
        shas = shas[:commit_scan_limit]

        # 2. Fetch detail (with files[]) in parallel, bounded concurrency
        sem = asyncio.Semaphore(8)
        results: list[list[str] | None] = [None] * len(shas)

        async def _fetch(idx: int, sha: str) -> None:
            async with sem:
                try:
                    r = await client.get(f"/repos/{repo}/commits/{sha}")
                    r.raise_for_status()
                    files = r.json().get("files", []) or []
                    results[idx] = [f.get("filename", "") for f in files if f.get("filename")]
                except Exception as e:
                    log.debug("github_commit_detail_failed", sha=sha, error=str(e))
                    results[idx] = []

        await asyncio.gather(*(_fetch(i, s) for i, s in enumerate(shas)))

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
        """Fetch diff between two commits using the compare API."""
        client = self._get_client()
        resp = await client.get(
            f"/repos/{repo}/compare/{base_sha}...{head_sha}",
        )
        resp.raise_for_status()
        data = resp.json()

        diff_files: list[DiffFile] = []
        for f in data.get("files", []):
            status_map: dict[str, FileStatus] = {
                "added": "added",
                "removed": "deleted",
                "modified": "modified",
                "renamed": "renamed",
            }
            diff_files.append(
                DiffFile(
                    path=f.get("filename", ""),
                    status=status_map.get(f.get("status", ""), "modified"),
                    old_path=f.get("previous_filename"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch", ""),
                )
            )
        return Diff(files=diff_files)

    async def fetch_pr_files(
        self,
        repo: str,
        pr_id: int | str,
        project: str = "",
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
        self,
        repo: str,
        path: str,
        per_page: int = 1,
        project: str = "",
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
    ) -> InlinePostResult:
        client = self._get_client()
        grouped: dict[tuple[str, int], list[Finding]] = {}
        skipped: list[Finding] = []
        for f in findings:
            if f.line is None:
                skipped.append(f)
                continue
            grouped.setdefault((f.file, f.line), []).append(f)

        commit_id = pr.head_commit_sha
        if not commit_id:
            resp = await client.get(f"/repos/{pr.repo}/pulls/{pr.pr_id}")
            resp.raise_for_status()
            commit_id = resp.json().get("head", {}).get("sha", "")
        if not commit_id:
            log.warning("github_inline_comments_missing_head_sha", pr_id=pr.pr_id, repo=pr.repo)
            return InlinePostResult(posted_ids=[], skipped=list(findings))

        ids: list[str] = []
        for (file, line), group in grouped.items():
            body = inline_comment_body(group)
            try:
                resp = await client.post(
                    f"/repos/{pr.repo}/pulls/{pr.pr_id}/comments",
                    json={
                        "body": body,
                        "commit_id": commit_id,
                        "path": file,
                        "line": line,
                        "side": "RIGHT",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                comments = data.get("comments", [])
                if "id" in data and not comments:
                    ids.append(str(data["id"]))
                for c in comments:
                    if "id" in c:
                        ids.append(str(c["id"]))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422:
                    log.warning(
                        "github_inline_comment_skipped",
                        file=file,
                        line=line,
                        reason="line_not_in_diff",
                    )
                    skipped.extend(group)
                else:
                    raise
        return InlinePostResult(posted_ids=ids, skipped=skipped)

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
        """List open PRs for a repo, enriched with review approval state and CI status."""
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

            head_sha = pr.get("head", {}).get("sha", "")
            if head_sha:
                check_runs: list[dict] = []
                commit_statuses: list[dict] = []
                combined_state: str | None = None
                try:
                    ci_resp = await client.get(
                        f"/repos/{repo}/commits/{head_sha}/check-runs",
                        params={"per_page": 100},
                    )
                    if ci_resp.status_code == 200:
                        check_runs = ci_resp.json().get("check_runs", [])
                except Exception:
                    check_runs = []
                try:
                    status_resp = await client.get(f"/repos/{repo}/commits/{head_sha}/status")
                    if status_resp.status_code == 200:
                        status_data = status_resp.json()
                        commit_statuses = status_data.get("statuses", []) or []
                        total_statuses = status_data.get("total_count", len(commit_statuses))
                        if total_statuses:
                            combined_state = status_data.get("state")
                except Exception:
                    commit_statuses = []
                pr["_ci_status"] = _compute_ci_status(
                    check_runs,
                    commit_statuses,
                    combined_status_state=combined_state,
                )
            else:
                pr["_ci_status"] = "unknown"
        return prs

    async def add_pr_reviewer(self, repo: str, pr_id: str, username: str) -> None:
        """Request review from a specific user on a PR."""
        client = self._get_client()
        resp = await client.post(
            f"/repos/{repo}/pulls/{pr_id}/requested_reviewers",
            json={"reviewers": [username]},
        )
        if resp.status_code not in (200, 201, 422):
            resp.raise_for_status()

    async def add_pr_assignee(self, repo: str, pr_id: str, username: str) -> None:
        """Add a user as an assignee on a PR."""
        client = self._get_client()
        resp = await client.post(
            f"/repos/{repo}/issues/{pr_id}/assignees",
            json={"assignees": [username]},
        )
        if resp.status_code not in (200, 201):
            resp.raise_for_status()

    async def create_issue_comment_reaction(
        self, repo: str, comment_id: str | int, content: str
    ) -> None:
        """Add a reaction to an issue comment. 200 (already exists) and 201 are both success."""
        client = self._get_client()
        resp = await client.post(
            f"/repos/{repo}/issues/comments/{comment_id}/reactions",
            json={"content": content},
        )
        resp.raise_for_status()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
