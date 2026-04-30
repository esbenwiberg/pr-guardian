from __future__ import annotations

import asyncio
import re
import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pr_guardian.core.orchestrator import run_review
from pr_guardian.core.repo_review import (
    DEFAULT_MAX_FILES as REPO_REVIEW_MAX_FILES,
    build_synthetic_pr,
    build_repo_diff,
)
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
    model_config = ConfigDict(extra="forbid")

    pr_url: str
    dry_run: bool = False
    comment_mode: Literal["none", "summary", "inline"] = "none"


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
    pr: PlatformPR, adapter, comment_mode: str, base_url: str,
    dismissals: list[dict] | None = None,
) -> None:
    """Run the review pipeline in the background, logging any errors."""
    import traceback
    try:
        await run_review(pr, adapter, comment_mode=comment_mode, base_url=base_url, dismissals=dismissals)
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

    # Load dismissals from previous reviews so agents respect them
    dismissals: list[dict] | None = None
    try:
        from pr_guardian.persistence import storage
        dismissals = await storage.get_active_dismissals(
            pr.pr_id, pr.repo, pr.platform.value,
        )
    except Exception:
        pass

    base_url = str(request.base_url).rstrip("/")
    asyncio.create_task(_run_review_background(pr, adapter, req.comment_mode, base_url, dismissals=dismissals))

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


class RepoReviewRequest(BaseModel):
    repo: str
    platform: str = "github"
    ref: str = "HEAD"
    max_files: int = REPO_REVIEW_MAX_FILES


class RepoReviewResponse(BaseModel):
    status: str
    repo: str
    platform: str
    ref: str
    note: str = (
        "Repo review runs the full PR-review pipeline across every file in the "
        "repo at the given ref. Intended for small repos only — cost and duration "
        "scale with total code size."
    )


async def _run_repo_review_background(pr: PlatformPR, adapter, diff) -> None:
    import traceback
    try:
        await run_review(
            pr,
            adapter,
            post_comment=False,
            dismissals=None,
            diff_override=diff,
            skip_platform_side_effects=True,
        )
    except Exception as e:
        log.error(
            "repo_review_background_failed",
            repo=pr.repo, error=str(e), traceback=traceback.format_exc(),
        )
    finally:
        if hasattr(adapter, "close"):
            try:
                await adapter.close()
            except Exception:
                pass


@router.post("/review/repo", response_model=RepoReviewResponse)
async def manual_repo_review(req: RepoReviewRequest):
    """Trigger a full-repo review.

    Treats the repository at ``ref`` as a synthetic PR where every file is new,
    then runs the standard PR review pipeline. Suitable for small repos only.

    The repo diff is built synchronously before returning so that errors
    (invalid token, repo too large, network failure) surface immediately as
    HTTP error responses rather than being silently swallowed in the background.
    """
    if req.platform not in ("github", "ado"):
        raise HTTPException(status_code=400, detail="Unsupported platform")

    repo = req.repo.strip()
    if not repo or "/" not in repo:
        raise HTTPException(
            status_code=400,
            detail="Repo must be in owner/repo (GitHub) or project/repo (ADO) format.",
        )

    try:
        adapter = create_adapter(req.platform)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info("manual_repo_review_started", platform=req.platform, repo=repo, ref=req.ref)

    # Build diff synchronously so any failure (bad credentials, repo too large,
    # network error) is returned as an HTTP error and shown in the modal.
    try:
        diff, meta = await build_repo_diff(
            adapter, repo, ref=req.ref, max_files=req.max_files,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch repo contents: {e}")

    log.info(
        "manual_repo_review_diff_built",
        repo=repo, files_included=meta["files_included"], total_bytes=meta["total_bytes"],
    )

    pr = build_synthetic_pr(repo, req.platform, req.ref, uuid.uuid4().hex[:12])

    asyncio.create_task(_run_repo_review_background(pr, adapter, diff))

    return RepoReviewResponse(
        status="queued",
        repo=repo,
        platform=req.platform,
        ref=req.ref,
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

        source_branch = data.get("sourceRefName", "").replace("refs/heads/", "")
        head_sha = data.get("lastMergeSourceCommit", {}).get("commitId", "")

        # lastMergeSourceCommit only updates after ADO re-evaluates the merge,
        # which can lag behind pushes.  The refs API reflects the real branch
        # HEAD immediately, so prefer it.
        try:
            branch_head = await adapter.resolve_branch_head(
                stub.project, stub.repo, source_branch,
            )
            if branch_head:
                if branch_head != head_sha:
                    log.info(
                        "hydrate_sha_refreshed",
                        lastMergeSource=head_sha[:12] if head_sha else "(empty)",
                        branch_head=branch_head[:12],
                    )
                head_sha = branch_head
        except Exception as exc:
            log.debug("hydrate_branch_head_failed", error=str(exc))

        return PlatformPR(
            platform=Platform.ADO,
            pr_id=stub.pr_id,
            repo=stub.repo,
            repo_url=stub.repo_url,
            source_branch=source_branch,
            target_branch=data.get("targetRefName", "").replace("refs/heads/", ""),
            author=data.get("createdBy", {}).get("uniqueName", ""),
            title=data.get("title", ""),
            head_commit_sha=head_sha,
            org=stub.org,
            project=stub.project,
        )

    return stub
