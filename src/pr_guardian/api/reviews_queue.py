"""Reviews queue API — backs the unified `/reviews` surface.

Merges PR reviews and repo scans into a single queue with `trigger_origin`
and `stale` flags. Falls back to demo data when no DB is configured so the
page is functional in dev sandboxes.
"""

from __future__ import annotations

import re
import asyncio
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pr_guardian.auth.dependencies import require_human_signed_in, require_profile_manager
from pr_guardian.auth.identity import Identity
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage

log = structlog.get_logger()
router = APIRouter(prefix="/api/reviews", tags=["reviews-queue"])


_GITHUB_PR_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<n>\d+)"
)
_ADO_PR_RE = re.compile(
    r"https?://dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+)/pullrequest/(?P<n>\d+)"
)
_GITHUB_REPO_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_ADO_REPO_URL_RE = re.compile(
    r"^https?://dev\.azure\.com/[^/]+/(?P<project>[^/]+)/_git/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_REPO_SHORT_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# ADO 3-segment shorthand: org/project/repo
_ADO_TRIPLE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


# ---------------------------------------------------------------------------
# Demo data — used when DB unavailable so the queue page is meaningful in dev.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


_DEMO_QUEUE = [
    {
        "id": "demo-candidate-124",
        "row_key": "candidate:demo-candidate-124",
        "subject_type": "candidate",
        "platform": "github",
        "title": "feat/auth",
        "repo": "demo/api",
        "author": "alice",
        "branch": "feat/auth",
        "pr_id": "124",
        "pr_url": "https://github.com/demo/api/pull/124",
        "state": "waiting",
        "reason": "checks_pending",
        "readiness": {
            "state": "waiting",
            "reason": "checks_pending",
            "snapshot": {
                "checks": {"total": 7, "passed": 4, "pending": 2, "failed": 0},
                "archmap": {"state": "waiting", "minutes_remaining": 6},
                "quiet_period": {"satisfied": True},
            },
        },
        "risk_tier": "medium",
        "findings": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "estimated_review_minutes": 0,
        "files_changed": 0,
        "trigger_origin": "readiness",
        "triggered_by": None,
        "stale": False,
        "started_at": _iso(_now() - timedelta(minutes=3)),
        "updated_at": _iso(_now() - timedelta(minutes=3)),
    },
    {
        "id": "demo-candidate-88",
        "row_key": "candidate:demo-candidate-88",
        "subject_type": "candidate",
        "platform": "ado",
        "title": "fix/billing",
        "repo": "demo/billing",
        "author": "bob",
        "branch": "fix/billing",
        "pr_id": "88",
        "pr_url": "https://dev.azure.com/demo/project/_git/billing/pullrequest/88",
        "state": "blocked",
        "reason": "checks_timeout",
        "readiness": {
            "state": "blocked",
            "reason": "checks_timeout",
            "snapshot": {
                "checks": {"total": 4, "passed": 3, "pending": 1, "failed": 0},
                "quiet_period": {"satisfied": True},
            },
        },
        "risk_tier": "medium",
        "findings": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "estimated_review_minutes": 0,
        "files_changed": 0,
        "trigger_origin": "readiness",
        "triggered_by": None,
        "stale": False,
        "started_at": _iso(_now() - timedelta(minutes=14)),
        "updated_at": _iso(_now() - timedelta(minutes=14)),
    },
    {
        "id": "demo-pr-482",
        "row_key": "review:demo-pr-482",
        "subject_type": "pr",
        "platform": "github",
        "title": "feat/auth-refactor",
        "repo": "demo/api",
        "author": "alice",
        "branch": "feature/auth-refactor",
        "decision": "human_review",
        "risk_tier": "high",
        "findings": {"critical": 0, "high": 3, "medium": 1, "low": 0},
        "estimated_review_minutes": 40,
        "files_changed": 51,
        "trigger_origin": "webhook",
        "triggered_by": None,
        "stale": True,
        "started_at": _iso(_now() - timedelta(minutes=12)),
    },
    {
        "id": "demo-pr-481",
        "row_key": "review:demo-pr-481",
        "subject_type": "pr",
        "platform": "github",
        "title": "fix/n-plus-one",
        "repo": "demo/api",
        "author": "bob",
        "branch": "fix/n-plus-one",
        "decision": "human_review",
        "risk_tier": "medium",
        "findings": {"critical": 0, "high": 0, "medium": 1, "low": 2},
        "estimated_review_minutes": 6,
        "files_changed": 3,
        "trigger_origin": "webhook",
        "triggered_by": None,
        "stale": False,
        "started_at": _iso(_now() - timedelta(hours=1)),
    },
    {
        "id": "demo-scan-legacy",
        "row_key": "review:demo-scan-legacy",
        "subject_type": "scan",
        "platform": "ado",
        "title": "scan/legacy-billing",
        "repo": "demo/legacy",
        "author": None,
        "branch": None,
        "decision": "human_review",
        "risk_tier": "high",
        "findings": {"critical": 0, "high": 4, "medium": 2, "low": 1},
        "estimated_review_minutes": 25,
        "files_changed": 184,
        "trigger_origin": "scan",
        "triggered_by": None,
        "stale": False,
        "started_at": _iso(_now() - timedelta(hours=4)),
    },
    {
        "id": "demo-pr-479",
        "row_key": "review:demo-pr-479",
        "subject_type": "pr",
        "platform": "github",
        "title": "refactor/billing-job",
        "repo": "demo/api",
        "author": "carol",
        "branch": "refactor/billing-job",
        "decision": "auto_approve",
        "risk_tier": "trivial",
        "findings": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "estimated_review_minutes": 0,
        "files_changed": 5,
        "trigger_origin": "manual",
        "triggered_by": "carol@example.com",
        "stale": False,
        "started_at": _iso(_now() - timedelta(hours=6)),
    },
]


# ---------------------------------------------------------------------------
# Shaping a queue row from a DB review record.
# ---------------------------------------------------------------------------


def _findings_breakdown(agent_results: list[dict[str, Any]] | None) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    if not agent_results:
        return counts
    for agent in agent_results:
        for f in agent.get("findings", []) or []:
            sev = (f.get("severity") or "").lower()
            if sev in counts:
                counts[sev] += 1
    return counts


def _estimated_minutes(findings: dict[str, int], files_changed: int) -> int:
    weight = (
        6 * findings["high"]
        + 8 * findings["critical"]
        + 3 * findings["medium"]
        + 1 * findings["low"]
    )
    return max(0, weight + max(0, files_changed // 5))


def _trigger_origin_of(row: dict[str, Any]) -> str:
    if row.get("subject_type") == "scan":
        return "scan"
    if row.get("triggered_by"):
        return "manual"
    return "webhook"


def _is_stale(row: dict[str, Any]) -> bool:
    return bool(row.get("stale"))


def _shape_review(
    row: dict[str, Any],
    pr_lookup: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    findings = _findings_breakdown(row.get("agent_results"))
    files_changed = row.get("files_changed") or 0
    pr_id = row.get("pr_id") or ""
    platform = row.get("platform") or ""
    repo = row.get("repo") or ""
    cached: dict[str, Any] = {}
    if pr_lookup and platform and repo and pr_id:
        cached = pr_lookup.get((platform, repo, str(pr_id)), {}) or {}

    # Fall back to the synced_prs cache when the review row hasn't been
    # hydrated yet (e.g. older rows created before update_review_pr_metadata).
    title = (row.get("title") or "").strip() or (cached.get("title") or "").strip()
    if not title:
        title = f"PR #{pr_id}" if pr_id else "untitled"
    author = row.get("author") or cached.get("author") or ""

    pr_status = cached.get("approval_status")
    return {
        "id": str(row.get("id") or pr_id),
        "row_key": f"review:{row.get('id') or pr_id}",
        "subject_type": "scan" if row.get("scan_id") else "pr",
        "platform": platform,
        "title": title,
        "repo": repo,
        "author": author,
        "branch": row.get("source_branch"),
        "decision": row.get("decision") or "pending",
        "risk_tier": row.get("risk_tier") or "medium",
        "findings": findings,
        "estimated_review_minutes": _estimated_minutes(findings, files_changed),
        "files_changed": files_changed,
        "trigger_origin": _trigger_origin_of(row),
        "triggered_by": row.get("triggered_by"),
        "stale": _is_stale(row),
        "started_at": row.get("started_at"),
        # Platform-side PR status (merged | approved | changes_requested | pending | draft).
        # Sourced from the synced_prs cache; None when the PR isn't in the cache.
        "pr_status": pr_status,
        "merged": pr_status == "merged",
    }


_VISIBLE_BLOCKED_CANDIDATE_REASONS = {
    "checks_failed",
    "checks_timeout",
    "fork_requires_manual_start",
    "repo_link_paused",
    "auto_review_disabled",
}
_VISIBLE_WAITING_CANDIDATE_REASONS = {
    "quiet_period",
    "checks_pending",
    "archmap_wait",
}
_HIDDEN_CANDIDATE_REASONS = {
    "draft",
    "platform_error",
    "status_write_failed",
    "profile_unavailable",
    "connection_unavailable",
    "connection_token_unavailable",
}


def _snapshot_bool(snapshot: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value: Any = snapshot
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value is True:
            return True
    return False


def _candidate_visible(candidate: dict[str, Any]) -> bool:
    state = candidate.get("state")
    reason = candidate.get("reason") or ""
    snapshot = candidate.get("readiness_snapshot") or {}
    if not (
        candidate.get("repo_link_id")
        and candidate.get("profile_id")
        and candidate.get("connection_id")
    ):
        return False
    if reason in _HIDDEN_CANDIDATE_REASONS:
        return False
    if state == "waiting":
        if reason == "draft" or _snapshot_bool(snapshot, "draft", "metadata.draft", "pr.draft"):
            return False
        return reason in _VISIBLE_WAITING_CANDIDATE_REASONS
    if state == "blocked":
        return reason in _VISIBLE_BLOCKED_CANDIDATE_REASONS
    return False


def _shape_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    snapshot = candidate.get("readiness_snapshot") or {}
    pr_id = str(candidate.get("pr_id") or "")
    repo = candidate.get("repo") or candidate.get("repo_name") or ""
    title = (
        snapshot.get("title")
        or snapshot.get("pr", {}).get("title")
        or snapshot.get("metadata", {}).get("title")
        or f"PR #{pr_id}"
    )
    author = (
        snapshot.get("author")
        or snapshot.get("pr", {}).get("author")
        or snapshot.get("metadata", {}).get("author")
        or ""
    )
    branch = (
        snapshot.get("source_branch")
        or snapshot.get("branch")
        or snapshot.get("pr", {}).get("source_branch")
        or snapshot.get("metadata", {}).get("source_branch")
        or ""
    )
    updated_at = candidate.get("updated_at") or candidate.get("created_at")
    state = candidate.get("state") or "waiting"
    reason = candidate.get("reason") or ""
    return {
        "id": str(candidate.get("id")),
        "row_key": f"candidate:{candidate.get('id')}",
        "subject_type": "candidate",
        "platform": candidate.get("platform") or "",
        "title": title,
        "repo": repo,
        "author": author,
        "branch": branch,
        "pr_id": pr_id,
        "pr_url": candidate.get("pr_url") or "",
        "head_sha": candidate.get("head_sha") or "",
        "repo_link_id": candidate.get("repo_link_id"),
        "profile_id": candidate.get("profile_id"),
        "connection_id": candidate.get("connection_id"),
        "connection_snapshot": candidate.get("connection_snapshot"),
        "state": state,
        "reason": reason,
        "readiness": {"state": state, "reason": reason, "snapshot": snapshot},
        "risk_tier": "medium",
        "findings": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "estimated_review_minutes": 0,
        "files_changed": 0,
        "trigger_origin": "readiness",
        "triggered_by": None,
        "stale": False,
        "started_at": updated_at,
        "updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# Queue endpoint
# ---------------------------------------------------------------------------


@router.get("/queue")
async def reviews_queue(request: Request):
    """Unified queue: PR reviews + repo scans in one list, sorted stale-first then newest-first."""
    try:
        reviews = await storage.list_reviews(limit=100)
    except Exception:
        reviews = []

    try:
        candidates = await storage.list_active_readiness_candidates(
            states=["waiting", "blocked"],
            limit=100,
        )
    except Exception:
        candidates = []

    if not reviews and not candidates:
        return JSONResponse(content={"items": _DEMO_QUEUE, "source": "demo"})

    # Bulk-fetch the synced_prs cache so we can (a) show platform-side state
    # (merged / approved / draft) and (b) backfill title/author for rows where
    # the review record never got hydrated.
    pr_keys = [
        (r.get("platform") or "", r.get("repo") or "", str(r.get("pr_id") or ""))
        for r in reviews
        if r.get("platform") and r.get("repo") and r.get("pr_id") and not r.get("scan_id")
    ]
    try:
        pr_lookup = await storage.get_synced_pr_lookup(pr_keys)
    except Exception:
        pr_lookup = {}

    items = [_shape_review(r, pr_lookup) for r in reviews]
    items.extend(_shape_candidate(c) for c in candidates if _candidate_visible(c))
    state_rank = {"blocked": 3, "waiting": 2}
    items.sort(
        key=lambda r: (
            state_rank.get(r.get("state") or "", 1),
            bool(r.get("stale")),
            r.get("updated_at") or r.get("started_at") or "",
            r.get("row_key") or r.get("id") or "",
        ),
        reverse=True,
    )
    return {"items": items, "source": "db"}


# ---------------------------------------------------------------------------
# Trigger endpoint — accepts PR URL or owner/repo shorthand.
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    url: str
    mode: str = "pr"  # "pr" or "scan"
    platform: str | None = None  # "github" | "ado" — overrides auto-detect for ambiguous shorthand
    selection: str = "all"  # "all" | "recent" — only used in scan mode
    max_files: int | None = None  # only used in scan mode; server-clamped


def _resolve_repo_scan_target(url: str, requested_platform: str | None) -> tuple[str, str]:
    """Resolve a user-typed repo identifier to (repo, platform) for a scan.

    Accepts:
      - https://github.com/owner/repo[.git]                            → github
      - https://dev.azure.com/org/project/_git/repo[.git]              → ado
      - owner/repo                                                     → github (or honor requested_platform="ado")
      - org/project/repo                                               → ado
    """
    # Full GitHub repo URL
    m = _GITHUB_REPO_URL_RE.match(url)
    if m:
        return f"{m.group('owner')}/{m.group('repo')}", "github"

    # Full ADO repo URL
    m = _ADO_REPO_URL_RE.match(url)
    if m:
        return f"{m.group('project')}/{m.group('repo')}", "ado"

    # ADO 3-segment shorthand: org/project/repo
    if _ADO_TRIPLE_RE.match(url):
        parts = url.split("/")
        return f"{parts[1]}/{parts[2]}", "ado"

    # 2-segment shorthand — ambiguous. Honor explicit platform, default github.
    if _REPO_SHORT_RE.match(url):
        if requested_platform == "ado":
            return url, "ado"
        return url, "github"

    raise HTTPException(
        status_code=400,
        detail="Unrecognised repo. Use owner/repo (GitHub), org/project/repo (ADO), or a full repo URL.",
    )


@router.post("/trigger")
async def trigger_review(req: TriggerRequest, request: Request):
    """Trigger a review (PR or repo scan). Returns {id, status} so the UI can redirect to /reviews/{id}/live."""
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    gh = _GITHUB_PR_RE.match(url)
    ado = _ADO_PR_RE.match(url)

    if gh or ado:
        # Reuse the existing review trigger so we don't fork logic.
        from pr_guardian.api.review import manual_review, ReviewRequest

        try:
            resp = await manual_review(
                ReviewRequest(pr_url=url, comment_mode="none"),
                request,
            )
            return {
                "id": resp.review_id or resp.pr_id,
                "review_id": resp.review_id,
                "pr_id": resp.pr_id,
                "status": resp.status,
                "platform": resp.platform,
                "repo": resp.repo,
            }
        except HTTPException:
            raise
        except Exception as e:
            log.error("trigger_pr_failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to trigger PR review: {e}")

    if req.mode == "scan":
        from pr_guardian.api.review import (
            manual_repo_review,
            RepoReviewRequest,
            REPO_REVIEW_MAX_FILES,
        )

        from pr_guardian.core.repo_review import SelectionMode

        repo, platform = _resolve_repo_scan_target(url, req.platform)
        selection: SelectionMode = "recent" if req.selection == "recent" else "all"
        try:
            resp = await manual_repo_review(
                RepoReviewRequest(
                    repo=repo,
                    platform=platform,
                    selection=selection,
                    max_files=req.max_files or REPO_REVIEW_MAX_FILES,
                )
            )
            return {
                "id": f"scan-{resp.repo.replace('/', '-')}",
                "status": resp.status,
                "platform": resp.platform,
                "repo": resp.repo,
                "selection": resp.selection,
                "max_files": resp.max_files,
            }
        except HTTPException:
            raise
        except Exception as e:
            log.error("trigger_scan_failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to trigger repo scan: {e}")

    raise HTTPException(
        status_code=400,
        detail="Unrecognised URL. Use a GitHub or ADO PR URL, or owner/repo for a repo scan.",
    )


# ---------------------------------------------------------------------------
# Finalize endpoint — Brief 05.
#
# Aggregates reviewer decisions, posts inline comments + summary + verdict to
# the platform, persists the full decision map in pipeline_log for audit, and
# returns the next queue item so the UI can auto-advance.
# ---------------------------------------------------------------------------

_DECISION_VALUES = {"accept", "fix", "dismiss"}
_VERDICT_VALUES = {"approve", "request_changes", "block"}
_COMMENT_MODES = {"inline", "summary", "none"}


class FinalizeRequest(BaseModel):
    decisions: dict[str, str] = Field(default_factory=dict)  # finding_id → accept|fix|dismiss
    comment_to_author: str = ""
    verdict: str = "approve"
    comment_mode: str = "inline"


class OverrideReadinessRequest(BaseModel):
    reason: str
    confirm: bool = False
    comment_mode: str = "summary"


class ManualBypassRequest(BaseModel):
    comment_mode: str = "summary"


def _candidate_pr(candidate: dict[str, Any]) -> PlatformPR:
    return PlatformPR(
        platform=Platform((candidate.get("platform") or "").lower()),
        pr_id=str(candidate.get("pr_id") or ""),
        repo=str(candidate.get("repo") or ""),
        repo_url=str(candidate.get("pr_url") or ""),
        source_branch="",
        target_branch="",
        author="",
        title="",
        head_commit_sha=str(candidate.get("head_sha") or ""),
        org=str(candidate.get("org_url") or candidate.get("repo_owner") or ""),
        project=str(candidate.get("project") or ""),
    )


async def _adapter_from_candidate(candidate: dict[str, Any]):
    from pr_guardian.platform.factory import create_adapter

    connection_id = candidate.get("connection_id")
    if not connection_id:
        return create_adapter(candidate["platform"])
    connection = await storage.get_connection(uuid_mod.UUID(str(connection_id)))
    if not connection or connection.get("archived_at"):
        raise HTTPException(409, "Candidate Connection is archived or inaccessible")
    token = await storage.get_connection_token(uuid_mod.UUID(str(connection_id)))
    if not token:
        raise HTTPException(409, "Candidate Connection has no accessible token")
    return create_adapter(
        candidate["platform"],
        token_override=token,
        org_url_override=connection.get("org_url") or None,
    )


async def _run_candidate_review(
    candidate: dict[str, Any],
    review_id: uuid_mod.UUID,
    adapter,
    *,
    comment_mode: str,
    manual_comment_override: bool,
) -> None:
    try:
        from pr_guardian.config.profile_resolver import resolve_profile_snapshot_config
        from pr_guardian.core.orchestrator import run_review

        resolved = await resolve_profile_snapshot_config(
            candidate.get("profile_snapshot"),
            candidate.get("connection_snapshot"),
        )
        await run_review(
            _candidate_pr(candidate),
            adapter,
            service_config=resolved.config,
            existing_review_db_id=review_id,
            comment_mode=comment_mode,
            manual_comment_override=manual_comment_override,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "candidate_manual_review_failed",
            candidate_id=candidate.get("id"),
            review_id=str(review_id),
            error=str(exc),
        )


@router.post("/candidates/{candidate_id}/start")
async def start_candidate_review_now(
    candidate_id: uuid_mod.UUID,
    body: ManualBypassRequest,
    identity: Identity = Depends(require_human_signed_in),
):
    """Start a manual review for a candidate without marking readiness success."""
    candidate = await storage.get_readiness_candidate_by_id(candidate_id)
    if not candidate:
        raise HTTPException(404, "Readiness candidate not found")
    adapter = await _adapter_from_candidate(candidate)
    pr = _candidate_pr(candidate)
    actor = identity.email or identity.display_name
    snapshot = {
        **(candidate.get("readiness_snapshot") or {}),
        "manual_bypass": {"actor": actor, "at": _now().isoformat()},
    }
    started = await storage.try_start_candidate_review(
        candidate_id,
        pr,
        source="manual_bypass",
        actor=actor,
        reason="manual_bypass",
        readiness_snapshot=snapshot,
        comment_mode=body.comment_mode,
        review_source="manual_bypass",
    )
    if started is None:
        raise HTTPException(409, "Candidate was already claimed or is no longer active")
    review_id, updated = started
    asyncio.create_task(
        _run_candidate_review(
            updated,
            review_id,
            adapter,
            comment_mode=body.comment_mode,
            manual_comment_override=True,
        )
    )
    return {
        "status": "queued",
        "review_id": str(review_id),
        "candidate_id": str(candidate_id),
        "readiness_marked_success": False,
        "source": "manual_bypass",
        "actor": actor,
    }


@router.post("/candidates/{candidate_id}/override")
async def override_candidate_readiness(
    candidate_id: uuid_mod.UUID,
    body: OverrideReadinessRequest,
    identity: Identity = Depends(require_profile_manager),
):
    """Mark readiness successful by authorized override and start one review."""
    if not body.confirm:
        raise HTTPException(400, "Override confirmation is required")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Override reason is required")
    candidate = await storage.get_readiness_candidate_by_id(candidate_id)
    if not candidate:
        raise HTTPException(404, "Readiness candidate not found")
    previous_snapshot = {
        "state": candidate.get("state"),
        "reason": candidate.get("reason"),
        "readiness_snapshot": candidate.get("readiness_snapshot") or {},
    }
    actor = identity.email or identity.display_name
    override_snapshot = {
        **(candidate.get("readiness_snapshot") or {}),
        "manual_override": {
            "actor": actor,
            "reason": reason,
            "at": _now().isoformat(),
            "previous": previous_snapshot,
        },
    }
    adapter = await _adapter_from_candidate(candidate)
    pr = _candidate_pr(candidate)
    started = await storage.try_start_candidate_review(
        candidate_id,
        pr,
        source="override",
        actor=actor,
        reason="manual_override",
        readiness_snapshot=override_snapshot,
        comment_mode=body.comment_mode,
        audit_event={
            "actor": actor,
            "action": "readiness.override",
            "target_type": "readiness_candidate",
            "target_id": candidate_id,
            "before": previous_snapshot,
            "after": {"reason": reason, "snapshot": override_snapshot},
        },
    )
    if started is None:
        raise HTTPException(409, "Candidate was already claimed or is no longer active")
    review_id, updated = started
    status_posted = True
    try:
        # Readiness statuses are always on; route through the readiness status
        # helper so endpoint code does not own platform-write semantics.
        from pr_guardian.core.readiness import _post_readiness_status

        status_posted = await _post_readiness_status(
            adapter, pr, "success", f"Guardian readiness overridden: {reason}"
        )
    except Exception as exc:  # noqa: BLE001
        status_posted = False
        log.warning(
            "readiness_override_status_failed",
            candidate_id=str(candidate_id),
            review_id=str(review_id),
            error=str(exc),
        )
    asyncio.create_task(
        _run_candidate_review(
            updated,
            review_id,
            adapter,
            comment_mode=body.comment_mode,
            manual_comment_override=True,
        )
    )
    return {
        "status": "queued",
        "review_id": str(review_id),
        "candidate_id": str(candidate_id),
        "readiness_marked_success": True,
        "source": "override",
        "actor": actor,
        "readiness_status_posted": status_posted,
        "audit_recorded": True,
    }


def _find_finding_by_id(review: dict[str, Any], finding_id: str) -> dict[str, Any] | None:
    """Locate a finding by its id within a review's agent_results."""
    for agent in review.get("agent_results") or []:
        for f in agent.get("findings") or []:
            if str(f.get("id") or "") == finding_id:
                return f
    return None


def _is_actionable_finding(finding: dict[str, Any]) -> bool:
    """Return whether an undecided finding should become a default fix request."""
    severity = str(finding.get("severity") or "").lower()
    certainty = str(finding.get("certainty") or "").lower()
    if severity in {"high", "critical"}:
        return True
    return severity == "medium" and certainty == "detected"


def _fallback_fix_findings(
    review: dict[str, Any],
    decisions: dict[str, str],
) -> list[dict[str, Any]]:
    """Use unresolved actionable findings when a changes-request verdict has no fixes."""
    findings: list[dict[str, Any]] = []
    for agent in review.get("agent_results") or []:
        for finding in agent.get("findings") or []:
            finding_id = str(finding.get("id") or "")
            if decisions.get(finding_id) in {"accept", "dismiss"}:
                continue
            if _is_actionable_finding(finding):
                findings.append(finding)
    return findings


async def _decisions_from_persisted_dismissals(review: dict[str, Any]) -> dict[str, str]:
    """Rebuild finish-review decisions from saved per-finding reviewer choices."""
    decisions: dict[str, str] = {}
    try:
        dismissals = await storage.get_active_dismissals(
            review.get("pr_id", ""),
            review.get("repo", ""),
            review.get("platform", ""),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("finalize_dismissal_lookup_failed", review_id=review.get("id"), error=str(exc))
        return decisions

    status_to_decision = {
        "acknowledged": "accept",
        "will_fix": "fix",
        "false_positive": "dismiss",
        "by_design": "dismiss",
    }
    dismissals_by_signature = {d.get("signature"): d for d in dismissals}
    for agent in review.get("agent_results") or []:
        agent_name = agent.get("agent_name", "")
        for finding in agent.get("findings") or []:
            finding_id = str(finding.get("id") or "")
            if not finding_id:
                continue
            signature = storage.finding_signature(
                finding.get("file", ""),
                finding.get("category", ""),
                agent_name,
            )
            dismissal = dismissals_by_signature.get(signature)
            status = dismissal.get("status") if dismissal else None
            decision = status_to_decision.get(status) if isinstance(status, str) else None
            if decision:
                decisions[finding_id] = decision
    return decisions


def _build_summary_comment(
    decisions: dict[str, str],
    fix_findings: list[dict[str, Any]],
    comment_to_author: str,
    verdict: str,
    *,
    include_fix_findings: bool = True,
) -> str:
    """Compose the summary comment posted alongside the verdict."""
    headline = {
        "approve": "**Reviewed and approved.**",
        "request_changes": "**Changes requested before this can merge.**",
        "block": "**Blocked — must not merge as-is.**",
    }.get(verdict, "**Reviewed.**")

    counts = {"accept": 0, "fix": 0, "dismiss": 0}
    for d in decisions.values():
        if d in counts:
            counts[d] += 1

    parts: list[str] = [headline]
    if comment_to_author.strip():
        parts.append(comment_to_author.strip())

    if any(counts.values()):
        bits = []
        if counts["accept"]:
            bits.append(f"{counts['accept']} accepted")
        if counts["fix"]:
            bits.append(f"{counts['fix']} fix requested")
        if counts["dismiss"]:
            bits.append(f"{counts['dismiss']} dismissed")
        parts.append("---")
        parts.append("Per-concern decisions: " + " · ".join(bits) + ".")

    if include_fix_findings and fix_findings:
        parts.append("**Fix-requested findings:**")
        for f in fix_findings:
            loc = f.get("file") or ""
            ln = f.get("line")
            where = f"{loc}:{ln}" if ln else loc
            title = (f.get("description") or "(no description)").split("\n", 1)[0][:140]
            parts.append(f"- {title}" + (f" — `{where}`" if where else ""))

    parts.append("_Posted from PR Guardian wrap-up._")
    return "\n\n".join(parts)


def _format_platform_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        resp_body = (exc.response.text or "")[:500]
        return (
            f"{type(exc).__name__}: HTTP {exc.response.status_code} "
            f"on {exc.request.url} — body={resp_body!r}"
        )
    return f"{type(exc).__name__}: {exc}"


def _is_github_formal_review_422(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    if exc.response.status_code != 422:
        return False
    url = str(exc.request.url)
    return "api.github.com/repos/" in url and "/pulls/" in url and url.endswith("/reviews")


async def _post_github_comment_fallback(
    adapter: Any,
    pr: Any,
    summary: str,
    actions: list[str],
    action_name: str,
    exc: Exception,
    review_id: str,
) -> str:
    formatted = _format_platform_error(exc)
    actions.append(f"{action_name}_rejected")
    log.warning(
        "github_formal_review_fallback_to_comment",
        review_id=review_id,
        action=action_name,
        error=formatted,
    )
    await adapter.post_comment(pr, summary)
    actions.append("post_comment_fallback")
    return formatted


async def _adapter_from_review_connection(review: dict[str, Any]):
    from pr_guardian.platform.factory import create_adapter, create_github_adapter

    connection_id = review.get("connection_id")
    platform = (review.get("platform") or "").lower()
    if connection_id:
        connection = await storage.get_connection(uuid_mod.UUID(str(connection_id)))
        if not connection or connection.get("archived_at"):
            raise HTTPException(409, "Stored Connection is archived or inaccessible")
        token = await storage.get_connection_token(uuid_mod.UUID(str(connection_id)))
        if not token:
            raise HTTPException(409, "Stored Connection has no accessible token")
        return create_adapter(
            platform,
            token_override=token,
            org_url_override=connection.get("org_url") or None,
        )
    if platform == "github":
        return await create_github_adapter(review.get("pat_name"))
    return create_adapter(platform)


@router.post("/{review_id}/finalize")
async def finalize_review(
    review_id: str,
    body: FinalizeRequest,
    identity: Identity = Depends(require_human_signed_in),
):
    """Post the reviewer's decisions, comment, and verdict back to the platform.

    Brief 05's main contract. Reuses the existing platform adapters so the
    inline-comment + verdict code paths are shared with the auto-pipeline.
    """
    if body.verdict not in _VERDICT_VALUES:
        raise HTTPException(400, f"Invalid verdict. Allowed: {sorted(_VERDICT_VALUES)}")
    if body.comment_mode not in _COMMENT_MODES:
        raise HTTPException(400, f"Invalid comment_mode. Allowed: {sorted(_COMMENT_MODES)}")
    for fid, dec in body.decisions.items():
        if dec not in _DECISION_VALUES:
            raise HTTPException(
                400, f"Decision for {fid!r} must be one of {sorted(_DECISION_VALUES)}"
            )

    # Load the review. Without a DB or for demo IDs we still want to look
    # like we did the right thing so the UI flow can be exercised end-to-end.
    try:
        rev_uuid = uuid_mod.UUID(review_id)
        review = await storage.get_review(rev_uuid)
    except (ValueError, Exception):
        review = None

    if review is None:
        # Demo / no-DB fallback: persist nothing but reply with a synthetic OK.
        return {
            "posted": False,
            "demo": True,
            "verdict": body.verdict,
            "decisions": body.decisions,
            "comment_mode": body.comment_mode,
            "next_id": None,
            "error": "Review not found — finalize recorded as demo only.",
        }

    persisted_decisions = await _decisions_from_persisted_dismissals(review)
    decisions = {**persisted_decisions, **body.decisions}

    # Translate decisions → inline comment set.
    fix_findings: list[dict[str, Any]] = []
    for fid, dec in decisions.items():
        if dec == "fix":
            f = _find_finding_by_id(review, fid)
            if f is not None:
                fix_findings.append(f)

    if body.verdict in {"request_changes", "block"} and not fix_findings:
        fix_findings = _fallback_fix_findings(review, decisions)
        for finding in fix_findings:
            finding_id = str(finding.get("id") or "")
            if finding_id and finding_id not in decisions:
                decisions[finding_id] = "fix"

    platform_str = (review.get("platform") or "").lower()
    actions: list[str] = []
    posted = True
    error: str | None = None
    platform_url = review.get("pr_url") or ""
    include_fix_findings_in_summary = body.comment_mode != "inline"

    if platform_str:
        from pr_guardian.models.findings import Finding, Severity, Certainty
        from pr_guardian.api.review import recover_org_project_from_pr_url

        # The reviews table does not persist org/project. ADO's reviewer-vote
        # and threads endpoints need the project segment, so recover it from
        # the stored pr_url. Without this the URL collapses to .../{org}//...
        # and ADO 400s with "A project name is required...".
        org_from_url, project_from_url = recover_org_project_from_pr_url(platform_url)

        pr = PlatformPR(
            platform=Platform(platform_str),
            pr_id=review.get("pr_id", ""),
            repo=review.get("repo", ""),
            repo_url=review.get("pr_url", ""),
            source_branch=review.get("source_branch", ""),
            target_branch=review.get("target_branch", ""),
            author=review.get("author", ""),
            title=review.get("title", ""),
            head_commit_sha=review.get("head_commit_sha", ""),
            org=review.get("org") or org_from_url,
            project=review.get("project") or project_from_url,
        )

        if platform_str == "ado" and not pr.project:
            raise HTTPException(
                422,
                "ADO review is missing the project segment and no pr_url is "
                "stored — cannot construct platform URLs.",
            )

        try:
            adapter = await _adapter_from_review_connection(review)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            # Inline comments — only for "fix" decisions, and only if mode requests them.
            if body.comment_mode == "inline" and fix_findings:
                inline_findings: list[Finding] = []
                for f in fix_findings:
                    sev = (f.get("severity") or "medium").lower()
                    try:
                        severity = Severity(sev)
                    except Exception:
                        severity = Severity.MEDIUM
                    try:
                        certainty = Certainty((f.get("certainty") or "suspected").lower())
                    except Exception:
                        certainty = Certainty.SUSPECTED
                    inline_findings.append(
                        Finding(
                            severity=severity,
                            certainty=certainty,
                            category=f.get("category", "other"),
                            language=f.get("language", "unknown"),
                            file=f.get("file", ""),
                            line=f.get("line"),
                            description=f.get("description", ""),
                            suggestion=f.get("suggestion", ""),
                        )
                    )
                if inline_findings:
                    posted_inline_ids = await adapter.post_inline_comments(pr, inline_findings)
                    if posted_inline_ids:
                        actions.append("post_inline_comments")
                    else:
                        include_fix_findings_in_summary = True
                        actions.append("post_inline_comments_skipped")

            summary = _build_summary_comment(
                decisions,
                fix_findings,
                body.comment_to_author,
                body.verdict,
                include_fix_findings=include_fix_findings_in_summary,
            )

            # Summary + verdict.
            if body.verdict == "approve":
                try:
                    await adapter.approve_pr(pr)
                    actions.append("approve_pr")
                    # Always post the summary unless the reviewer explicitly chose
                    # "none" — otherwise the PR has no record of who approved or
                    # which findings were addressed.
                    if body.comment_mode != "none":
                        await adapter.post_comment(pr, summary)
                        actions.append("post_comment")
                except Exception as exc:
                    if (
                        platform_str == "github"
                        and body.comment_mode != "none"
                        and _is_github_formal_review_422(exc)
                    ):
                        error = await _post_github_comment_fallback(
                            adapter, pr, summary, actions, "approve_pr", exc, review_id
                        )
                    else:
                        raise
            elif body.verdict == "request_changes":
                try:
                    await adapter.request_changes(pr, summary)
                    actions.append("request_changes")
                except Exception as exc:
                    if (
                        platform_str == "github"
                        and body.comment_mode != "none"
                        and _is_github_formal_review_422(exc)
                    ):
                        error = await _post_github_comment_fallback(
                            adapter, pr, summary, actions, "request_changes", exc, review_id
                        )
                    else:
                        raise
            elif body.verdict == "block":
                # GitHub has no first-class block. Use request_changes + an
                # advisory label hint in the comment body. ADO callers should
                # extend with vote: -10 if/when that's wired through.
                block_summary = summary + "\n\n_guardian:blocked — do not merge._"
                try:
                    await adapter.request_changes(pr, block_summary)
                    actions.append("request_changes")
                    actions.append("block_advisory")
                except Exception as exc:
                    if (
                        platform_str == "github"
                        and body.comment_mode != "none"
                        and _is_github_formal_review_422(exc)
                    ):
                        error = await _post_github_comment_fallback(
                            adapter, pr, block_summary, actions, "request_changes", exc, review_id
                        )
                        actions.append("block_advisory")
                    else:
                        raise
        except Exception as exc:  # noqa: BLE001
            posted = False
            error = _format_platform_error(exc)
            log.error("finalize_failed", review_id=review_id, error=error)

    # Always persist intent — even on platform failure.
    try:
        await storage.append_review_log_entry(
            rev_uuid,
            {
                "kind": "human_finalize",
                "verdict": body.verdict,
                "comment_mode": body.comment_mode,
                "decisions": decisions,
                "comment_to_author": body.comment_to_author,
                "fix_findings": [f.get("id") for f in fix_findings if f.get("id")],
                "platform_actions": actions,
                "posted": posted,
                "error": error,
                "actor_email": identity.email or identity.display_name,
                "at": _now().isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("finalize_log_persist_failed", review_id=review_id, error=str(exc))

    # Auto-advance: pick the next item still needing review.
    next_id: str | None = None
    try:
        rows = await storage.list_reviews(limit=20)
        for r in rows:
            rid = str(r.get("id") or "")
            if (
                rid
                and rid != review_id
                and (r.get("decision") in (None, "pending", "human_review"))
            ):
                next_id = rid
                break
    except Exception:
        next_id = None

    if not posted:
        return {"posted": False, "error": error, "actions": actions, "next_id": next_id}
    return {
        "posted": True,
        "verdict": body.verdict,
        "comment_mode": body.comment_mode,
        "decisions": decisions,
        "actions": actions,
        "platform_url": platform_url,
        "next_id": next_id,
    }
