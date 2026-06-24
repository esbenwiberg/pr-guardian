from __future__ import annotations

import asyncio
import re
import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pr_guardian.config.profile_resolver import (
    ProfileResolutionError,
    ResolvedProfileConfig,
    resolve_profile_config,
)
from pr_guardian.core.orchestrator import run_review
from pr_guardian.core.repo_review import (
    DEFAULT_MAX_FILES as REPO_REVIEW_MAX_FILES,
    SelectionMode,
    build_synthetic_pr,
    build_repo_diff,
    clamp_max_files,
)
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.platform.factory import create_adapter, create_github_adapter
from pr_guardian.platform.protocol import PlatformAdapter

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["review"])

_GITHUB_PR_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_ADO_PR_RE = re.compile(
    r"https?://dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+)/pullrequest/(?P<number>\d+)",
    re.IGNORECASE,
)


def recover_org_project_from_pr_url(pr_url: str) -> tuple[str, str]:
    """Best-effort (org, project) recovery from a stored PR URL.

    The reviews table does not persist org/project columns. ADO platform
    calls need the project segment in the URL path, so callers that build
    a PlatformPR from a review row must recover it from the stored pr_url
    (or fail loudly). Returns ("", "") on any parsing failure.
    """
    if not pr_url:
        return "", ""
    try:
        stub, _ = _parse_pr_url(pr_url)
        return stub.org or "", stub.project or ""
    except Exception:
        return "", ""


async def fetch_live_head_sha(adapter: object, pr: PlatformPR) -> str | None:
    """Best-effort fetch of the PR's *current* head SHA from the platform.

    Human-finalize handlers build their PlatformPR from the stored review row,
    whose ``head_commit_sha`` is the SHA captured when the review ran. If the
    author pushed commits afterwards, posting the verdict's commit status to
    that stale SHA lands it on a dead commit while branch protection evaluates
    the live head — so ``guardian/review`` never goes green. Callers compare
    this live SHA against the stored one to detect that drift.

    Returns the live head SHA, or ``None`` if the adapter can't report it or
    the platform call fails — callers fall back to the stored SHA on ``None``
    (no worse than the historical behaviour, just not improved).
    """
    fetch = getattr(adapter, "fetch_pr_metadata", None)
    if fetch is None:
        return None
    try:
        meta = await fetch(pr)
    except Exception as exc:  # noqa: BLE001 — best-effort; never break finalize
        log.warning("live_head_fetch_failed", pr_id=pr.pr_id, repo=pr.repo, error=str(exc))
        return None
    return getattr(meta, "head_sha", None) or None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_url: str
    dry_run: bool = False
    comment_mode: Literal["none", "summary", "inline"] = "none"
    pat_name: str | None = None


class ReviewResponse(BaseModel):
    status: str
    pr_id: str
    repo: str
    platform: str
    review_id: str | None = None
    decision: str | None = None
    summary: str | None = None
    risk_tier: str | None = None
    score: float | None = None


async def _run_review_background(
    stub: PlatformPR,
    adapter,
    comment_mode: str,
    base_url: str,
    *,
    platform_name: str,
    pat_name: str | None = None,
    review_db_id: uuid.UUID | None = None,
    resolved_profile: ResolvedProfileConfig | None = None,
) -> None:
    """Hydrate the PR stub and run the full review pipeline in the background."""
    import traceback

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        log.error(
            "background_review_hydrate_failed", pr_id=stub.pr_id, repo=stub.repo, error=str(e)
        )
        return

    dismissals: list[dict] | None = None
    prev_review: dict | None = None
    try:
        from pr_guardian.persistence import storage
        from pr_guardian.persistence.storage import find_review_by_pr_url

        if review_db_id is not None:
            try:
                await storage.update_review_pr_metadata(review_db_id, pr)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "review_metadata_persist_failed",
                    review_id=str(review_db_id),
                    error=str(exc),
                )
        dismissals = await storage.get_active_dismissals(pr.pr_id, pr.repo, pr.platform.value)
        prev_review = await find_review_by_pr_url(pr.pr_url)
    except Exception:
        pass

    try:
        result = await run_review(
            pr,
            adapter,
            service_config=resolved_profile.config if resolved_profile else None,
            comment_mode=comment_mode,
            base_url=base_url,
            dismissals=dismissals,
            pat_name=pat_name,
            existing_review_db_id=review_db_id,
            manual_comment_override=True,
        )
        if result is not None and dismissals is not None:
            from pr_guardian.persistence.storage import infer_fixes, finding_signature as _fsig

            prev_sigs = {
                _fsig(f.get("file", ""), f.get("category", ""), ar["agent_name"])
                for ar in (prev_review or {}).get("agent_results", [])
                for f in ar.get("findings", [])
            }
            current_sigs = {
                _fsig(f.file, f.category, ar.agent_name)
                for ar in result.agent_results
                for f in ar.findings
            }
            await infer_fixes(pr.pr_id, prev_sigs, current_sigs, pr.head_commit_sha)
    except Exception as e:
        log.error(
            "background_review_failed",
            pr_id=pr.pr_id,
            error=str(e),
            traceback=traceback.format_exc(),
        )


@router.post("/review", response_model=ReviewResponse)
async def manual_review(req: ReviewRequest, request: Request):
    """Trigger a review for a PR by URL.

    Validates the PR URL and queues the review; hydration runs in the background.
    Returns immediately so the caller can track progress via the active reviews panel.
    """
    stub, platform_name = _parse_pr_url(req.pr_url)
    try:
        resolved_profile = await _resolve_review_profile(
            stub,
            platform_name,
            connection_name=req.pat_name,
            require_connection=True,
        )
    except ProfileResolutionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    adapter: PlatformAdapter
    try:
        adapter = await _create_adapter_for_resolution(
            platform_name,
            resolved_profile,
            fallback_pat_name=req.pat_name,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if req.dry_run:
        return ReviewResponse(
            status="dry_run_accepted",
            pr_id=stub.pr_id,
            repo=stub.repo,
            platform=platform_name,
        )

    log.info("manual_review_started", platform=platform_name, pr_id=stub.pr_id, repo=stub.repo)
    base_url = str(request.base_url).rstrip("/")

    review_db_id: uuid.UUID | None = None
    try:
        from pr_guardian.persistence import storage

        review_db_id = await storage.create_review_record(
            stub,
            comment_mode=req.comment_mode,
            pat_name=req.pat_name,
        )
        if review_db_id:
            await storage.set_review_provenance(
                review_db_id,
                **resolved_profile.review_provenance(review_source="manual"),
            )
    except Exception as e:
        log.warning("manual_review_db_create_failed", pr_id=stub.pr_id, error=str(e))

    asyncio.create_task(
        _run_review_background(
            stub,
            adapter,
            req.comment_mode,
            base_url,
            platform_name=platform_name,
            pat_name=req.pat_name,
            review_db_id=review_db_id,
            resolved_profile=resolved_profile,
        )
    )

    return ReviewResponse(
        status="queued",
        pr_id=stub.pr_id,
        repo=stub.repo,
        platform=platform_name,
        review_id=str(review_db_id) if review_db_id else None,
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


async def _resolve_review_profile(
    stub: PlatformPR,
    platform_name: str,
    *,
    connection_name: str | None = None,
    require_connection: bool = False,
) -> ResolvedProfileConfig:
    org_url = ""
    project = ""
    if platform_name == "ado":
        project = stub.project or ""
        org_url = f"https://dev.azure.com/{stub.org}" if stub.org else ""
    return await resolve_profile_config(
        platform=platform_name,
        repo=stub.repo,
        org_url=org_url,
        project=project,
        connection_name=connection_name,
        require_connection=require_connection,
    )


async def _create_adapter_for_resolution(
    platform_name: str,
    resolved_profile: ResolvedProfileConfig,
    *,
    fallback_pat_name: str | None = None,
) -> PlatformAdapter:
    if resolved_profile.connection_id:
        from pr_guardian.persistence import storage

        token = await storage.get_connection_token(resolved_profile.connection_id)
        org_url = ""
        if resolved_profile.connection_snapshot:
            org_url = resolved_profile.connection_snapshot.get("org_url") or ""
        return create_adapter(
            platform_name,
            token_override=token,
            org_url_override=org_url or None,
        )
    if platform_name == "github":
        return await create_github_adapter(fallback_pat_name)
    return create_adapter(platform_name)


class RepoReviewRequest(BaseModel):
    repo: str
    platform: str = "github"
    ref: str = "HEAD"
    max_files: int = REPO_REVIEW_MAX_FILES
    selection: Literal["all", "recent"] = "all"
    pat_name: str | None = None


class RepoReviewResponse(BaseModel):
    status: str
    repo: str
    platform: str
    ref: str
    selection: str
    max_files: int
    note: str = (
        "Repo review runs the full PR-review pipeline across every selected "
        "file in the repo at the given ref. Use selection='recent' on larger "
        "repos to focus on the most recently changed files."
    )


async def _run_repo_review_background(
    repo: str,
    platform: str,
    adapter,
    ref: str,
    max_files: int,
    *,
    selection: SelectionMode = "all",
    pat_name: str | None = None,
    resolved_profile: ResolvedProfileConfig | None = None,
) -> None:
    """Run a full repo review in the background.

    Creates a DB record immediately so the review appears in Active Reviews,
    then builds the diff and runs the pipeline. If diff-building fails the
    record is marked as error so the user sees *something* rather than silence.
    """
    import traceback
    from pr_guardian.core.orchestrator import get_storage
    from pr_guardian.core.events import ReviewEvent, event_bus

    synthetic_id = uuid.uuid4().hex[:12]
    pr = build_synthetic_pr(repo, platform, ref, synthetic_id)

    storage = get_storage()
    review_db_id: uuid.UUID | None = None

    # Create the DB record before expensive diff-building so the review
    # shows up in Active Reviews immediately and any failure is surfaced there.
    if storage:
        try:
            review_db_id = await storage.create_review_record(
                pr, comment_mode="none", pat_name=pat_name
            )
            if resolved_profile:
                await storage.set_review_provenance(
                    review_db_id,
                    **resolved_profile.review_provenance(review_source="repo_review"),
                )
            await storage.update_review_stage(review_db_id, "queued", "Building repo diff")
            event_bus.publish(
                ReviewEvent(
                    review_id=str(review_db_id),
                    pr_id=pr.pr_id,
                    repo=pr.repo,
                    stage="queued",
                    detail="Building repo diff",
                )
            )
        except Exception as e:
            log.warning("repo_review_db_create_failed", error=str(e))

    try:
        diff, meta = await build_repo_diff(
            adapter,
            repo,
            ref=ref,
            selection=selection,
            max_files=max_files,
        )
        log.info(
            "repo_review_diff_built",
            repo=repo,
            selection=selection,
            files_included=meta["files_included"],
            files_truncated=meta["files_truncated"],
            files_skipped_binary=meta["files_skipped_binary"],
            selection_capped=meta["selection_capped"],
            total_bytes=meta["total_bytes"],
        )

        # Surface the meta back to the UI via SSE + stage detail so reviewers
        # can see how many files were truncated / skipped / capped.
        bits = [f"{meta['files_included']} files"]
        if meta["selection_capped"]:
            bits.append("capped")
        if meta["files_truncated"]:
            bits.append(f"{meta['files_truncated']} truncated")
        if meta["files_skipped_binary"]:
            bits.append(f"{meta['files_skipped_binary']} binaries skipped")
        detail = ", ".join(bits)
        if storage and review_db_id:
            try:
                await storage.update_review_stage(review_db_id, "repo_diff_built", detail)
            except Exception:
                pass
        event_bus.publish(
            ReviewEvent(
                review_id=str(review_db_id) if review_db_id else "",
                pr_id=pr.pr_id,
                repo=pr.repo,
                stage="repo_diff_built",
                detail=detail,
                extra=meta,
            )
        )

        await run_review(
            pr,
            adapter,
            service_config=resolved_profile.config if resolved_profile else None,
            existing_review_db_id=review_db_id,
            post_comment=False,
            dismissals=None,
            diff_override=diff,
            skip_platform_side_effects=True,
        )
    except Exception as e:
        log.error(
            "repo_review_background_failed",
            repo=repo,
            error=str(e),
            traceback=traceback.format_exc(),
        )
        if storage and review_db_id:
            try:
                await storage.mark_review_failed(review_db_id, str(e))
            except Exception:
                pass
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

    Returns immediately (like a PR review) — the diff build and pipeline run
    in the background. Progress is visible in Active Reviews via SSE.
    """
    if req.platform not in ("github", "ado"):
        raise HTTPException(status_code=400, detail="Unsupported platform")

    repo = req.repo.strip()
    if not repo or "/" not in repo:
        raise HTTPException(
            status_code=400,
            detail="Repo must be in owner/repo (GitHub) or project/repo (ADO) format.",
        )

    adapter: PlatformAdapter
    try:
        resolved_profile = await resolve_profile_config(
            platform=req.platform,
            repo=repo,
            connection_name=req.pat_name,
            require_connection=True,
        )
        adapter = await _create_adapter_for_resolution(
            req.platform,
            resolved_profile,
            fallback_pat_name=req.pat_name,
        )
    except ProfileResolutionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    max_files = clamp_max_files(req.max_files)

    log.info(
        "manual_repo_review_started",
        platform=req.platform,
        repo=repo,
        ref=req.ref,
        selection=req.selection,
        max_files=max_files,
        clamped=(max_files != req.max_files),
    )

    asyncio.create_task(
        _run_repo_review_background(
            repo,
            req.platform,
            adapter,
            req.ref,
            max_files,
            selection=req.selection,
            pat_name=req.pat_name,
            resolved_profile=resolved_profile,
        )
    )

    return RepoReviewResponse(
        status="queued",
        repo=repo,
        platform=req.platform,
        ref=req.ref,
        selection=req.selection,
        max_files=max_files,
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
            body=data.get("body") or "",
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
                stub.project,
                stub.repo,
                source_branch,
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
            body=data.get("description") or "",
            org=stub.org,
            project=stub.project,
        )

    return stub
