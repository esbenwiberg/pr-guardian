from __future__ import annotations

import asyncio
import re

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from pr_guardian.core.orchestrator import run_review
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.factory import create_adapter

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["review"])

_GITHUB_PR_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_ADO_PR_RE = re.compile(
    r"https?://dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+)/pullrequest/(?P<number>\d+)"
)


class ReviewRequest(BaseModel):
    pr_url: str
    dry_run: bool = False
    post_comment: bool = False


class ReviewResponse(BaseModel):
    status: str
    pr_id: str
    repo: str
    platform: str
    decision: str | None = None
    summary: str | None = None
    risk_tier: str | None = None
    score: float | None = None


async def _run_review_background(
    pr: PlatformPR, adapter, post_comment: bool, base_url: str,
    dismissals: list[dict] | None = None,
) -> None:
    """Run the review pipeline in the background, logging any errors."""
    import traceback
    try:
        await run_review(pr, adapter, post_comment=post_comment, base_url=base_url, dismissals=dismissals)
    except Exception as e:
        log.error("background_review_failed", pr_id=pr.pr_id, error=str(e), traceback=traceback.format_exc())


@router.post("/review", response_model=ReviewResponse)
async def manual_review(req: ReviewRequest, request: Request):
    """Trigger a review for a PR by URL.

    Validates the PR and starts the review pipeline in the background.
    Returns immediately so the caller can track progress via the active reviews panel.
    """
    stub, platform_name = _parse_pr_url(req.pr_url)
    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to fetch PR info: {e}")

    if req.dry_run:
        return ReviewResponse(
            status="dry_run_accepted",
            pr_id=pr.pr_id,
            repo=pr.repo,
            platform=platform_name,
        )

    log.info("manual_review_started", platform=platform_name, pr_id=pr.pr_id, repo=pr.repo)

    base_url = str(request.base_url).rstrip("/")
    asyncio.create_task(_run_review_background(pr, adapter, req.post_comment, base_url))

    return ReviewResponse(
        status="queued",
        pr_id=pr.pr_id,
        repo=pr.repo,
        platform=platform_name,
    )


def _parse_pr_url(url: str) -> tuple[PlatformPR, str]:
    """Parse a PR URL into a PlatformPR stub and platform name."""
    gh = _GITHUB_PR_RE.match(url)
    if gh:
        owner, repo, number = gh.group("owner"), gh.group("repo"), gh.group("number")
        return PlatformPR(
            platform=Platform.GITHUB,
            pr_id=number,
            repo=f"{owner}/{repo}",
            repo_url=f"https://github.com/{owner}/{repo}.git",
            source_branch="",
            target_branch="",
            author="",
            title="",
            head_commit_sha="",
            org=owner,
        ), "github"

    ado = _ADO_PR_RE.match(url)
    if ado:
        org = ado.group("org")
        project = ado.group("project")
        repo = ado.group("repo")
        number = ado.group("number")
        return PlatformPR(
            platform=Platform.ADO,
            pr_id=number,
            repo=repo,
            repo_url=f"https://dev.azure.com/{org}/{project}/_git/{repo}",
            source_branch="",
            target_branch="",
            author="",
            title="",
            head_commit_sha="",
            org=org,
            project=project,
        ), "ado"

    raise HTTPException(
        status_code=400,
        detail="Unsupported PR URL format. Use GitHub or Azure DevOps PR URLs.",
    )


async def _hydrate_pr(adapter, stub: PlatformPR, platform: str) -> PlatformPR:
    """Fetch full PR metadata from the platform API to fill in the stub."""
    if platform == "github":
        client = adapter._get_client()
        resp = await client.get(f"/repos/{stub.repo}/pulls/{stub.pr_id}")
        resp.raise_for_status()
        data = resp.json()

        return PlatformPR(
            platform=Platform.GITHUB,
            pr_id=stub.pr_id,
            repo=stub.repo,
            repo_url=data.get("base", {}).get("repo", {}).get("clone_url", stub.repo_url),
            source_branch=data.get("head", {}).get("ref", ""),
            target_branch=data.get("base", {}).get("ref", ""),
            author=data.get("user", {}).get("login", ""),
            title=data.get("title", ""),
            head_commit_sha=data.get("head", {}).get("sha", ""),
            org=stub.org,
        )

    if platform == "ado":
        client = adapter._get_client()
        resp = await client.get(
            f"{adapter._org_url}/{stub.project}/_apis/git/repositories/{stub.repo}"
            f"/pullRequests/{stub.pr_id}",
            params={"api-version": "7.1"},
        )
        resp.raise_for_status()
        data = resp.json()
        return PlatformPR(
            platform=Platform.ADO,
            pr_id=stub.pr_id,
            repo=stub.repo,
            repo_url=stub.repo_url,
            source_branch=data.get("sourceRefName", "").replace("refs/heads/", ""),
            target_branch=data.get("targetRefName", "").replace("refs/heads/", ""),
            author=data.get("createdBy", {}).get("uniqueName", ""),
            title=data.get("title", ""),
            head_commit_sha=data.get("lastMergeSourceCommit", {}).get("commitId", ""),
            org=stub.org,
            project=stub.project,
        )

    return stub
