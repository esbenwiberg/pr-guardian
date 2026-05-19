"""Reviews queue API — backs the unified `/reviews` surface.

Merges PR reviews and repo scans into a single queue with `trigger_origin`
and `stale` flags. Falls back to demo data when no DB is configured so the
page is functional in dev sandboxes.
"""
from __future__ import annotations

import re
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
        "id": "demo-pr-482",
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
    weight = 6 * findings["high"] + 8 * findings["critical"] + 3 * findings["medium"] + 1 * findings["low"]
    return max(0, weight + max(0, files_changed // 5))


def _trigger_origin_of(row: dict[str, Any]) -> str:
    if row.get("subject_type") == "scan":
        return "scan"
    if row.get("triggered_by"):
        return "manual"
    return "webhook"


def _is_stale(row: dict[str, Any]) -> bool:
    return bool(row.get("stale"))


def _shape_review(row: dict[str, Any]) -> dict[str, Any]:
    findings = _findings_breakdown(row.get("agent_results"))
    files_changed = row.get("files_changed") or 0
    return {
        "id": str(row.get("id") or row.get("pr_id") or ""),
        "subject_type": "scan" if row.get("scan_id") else "pr",
        "platform": row.get("platform"),
        "title": row.get("title") or row.get("pr_id") or "untitled",
        "repo": row.get("repo") or "",
        "author": row.get("author"),
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

    if not reviews:
        return JSONResponse(content={"items": _DEMO_QUEUE, "source": "demo"})

    items = [_shape_review(r) for r in reviews]
    items.sort(key=lambda r: (not r["stale"], r["started_at"] or ""), reverse=True)
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
            manual_repo_review, RepoReviewRequest, REPO_REVIEW_MAX_FILES,
        )
        repo, platform = _resolve_repo_scan_target(url, req.platform)
        selection = req.selection if req.selection in ("all", "recent") else "all"
        try:
            resp = await manual_repo_review(RepoReviewRequest(
                repo=repo,
                platform=platform,
                selection=selection,
                max_files=req.max_files or REPO_REVIEW_MAX_FILES,
            ))
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


def _find_finding_by_id(review: dict[str, Any], finding_id: str) -> dict[str, Any] | None:
    """Locate a finding by its id within a review's agent_results."""
    for agent in review.get("agent_results") or []:
        for f in agent.get("findings") or []:
            if str(f.get("id") or "") == finding_id:
                return f
    return None


def _build_summary_comment(
    decisions: dict[str, str],
    fix_findings: list[dict[str, Any]],
    comment_to_author: str,
    verdict: str,
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
        if counts["accept"]: bits.append(f"{counts['accept']} accepted")
        if counts["fix"]:    bits.append(f"{counts['fix']} fix requested")
        if counts["dismiss"]: bits.append(f"{counts['dismiss']} dismissed")
        parts.append("---")
        parts.append("Per-concern decisions: " + " · ".join(bits) + ".")

    if fix_findings:
        parts.append("**Fix-requested findings:**")
        for f in fix_findings:
            loc = f.get("file") or ""
            ln = f.get("line")
            where = f"{loc}:{ln}" if ln else loc
            title = (f.get("description") or "(no description)").split("\n", 1)[0][:140]
            parts.append(f"- {title}" + (f" — `{where}`" if where else ""))

    parts.append("_Posted from PR Guardian wrap-up._")
    return "\n\n".join(parts)


@router.post("/{review_id}/finalize")
async def finalize_review(review_id: str, body: FinalizeRequest):
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
            raise HTTPException(400, f"Decision for {fid!r} must be one of {sorted(_DECISION_VALUES)}")

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

    # Translate decisions → inline comment set.
    fix_findings: list[dict[str, Any]] = []
    for fid, dec in body.decisions.items():
        if dec == "fix":
            f = _find_finding_by_id(review, fid)
            if f is not None:
                fix_findings.append(f)

    # Build verdict comment.
    summary = _build_summary_comment(body.decisions, fix_findings, body.comment_to_author, body.verdict)

    platform_str = (review.get("platform") or "").lower()
    actions: list[str] = []
    posted = True
    error: str | None = None
    platform_url = review.get("pr_url") or ""

    if platform_str:
        from pr_guardian.models.pr import Platform, PlatformPR
        from pr_guardian.platform.factory import create_adapter, create_github_adapter
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

        adapter = (
            await create_github_adapter()
            if platform_str == "github"
            else create_adapter(platform_str)
        )

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
                        certainty = Certainty((f.get("certainty") or "likely").lower())
                    except Exception:
                        certainty = Certainty.LIKELY
                    inline_findings.append(Finding(
                        severity=severity,
                        certainty=certainty,
                        category=f.get("category", "other"),
                        language=f.get("language", "unknown"),
                        file=f.get("file", ""),
                        line=f.get("line"),
                        description=f.get("description", ""),
                        suggestion=f.get("suggestion", ""),
                    ))
                if inline_findings:
                    await adapter.post_inline_comments(pr, inline_findings)
                    actions.append("post_inline_comments")

            # Summary + verdict.
            if body.verdict == "approve":
                await adapter.approve_pr(pr)
                actions.append("approve_pr")
                # Always post the summary unless the reviewer explicitly chose
                # "none" — otherwise the PR has no record of who approved or
                # which findings were addressed.
                if body.comment_mode != "none":
                    await adapter.post_comment(pr, summary)
                    actions.append("post_comment")
            elif body.verdict == "request_changes":
                await adapter.request_changes(pr, summary)
                actions.append("request_changes")
            elif body.verdict == "block":
                # GitHub has no first-class block. Use request_changes + an
                # advisory label hint in the comment body. ADO callers should
                # extend with vote: -10 if/when that's wired through.
                await adapter.request_changes(pr, summary + "\n\n_guardian:blocked — do not merge._")
                actions.append("request_changes")
                actions.append("block_advisory")
        except Exception as exc:  # noqa: BLE001
            posted = False
            error = f"{type(exc).__name__}: {exc}"
            log.error("finalize_failed", review_id=review_id, error=error)

    # Always persist intent — even on platform failure.
    try:
        await storage.append_review_log_entry(rev_uuid, {
            "kind": "human_finalize",
            "verdict": body.verdict,
            "comment_mode": body.comment_mode,
            "decisions": body.decisions,
            "comment_to_author": body.comment_to_author,
            "fix_findings": [f.get("id") for f in fix_findings if f.get("id")],
            "platform_actions": actions,
            "posted": posted,
            "error": error,
            "at": _now().isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("finalize_log_persist_failed", review_id=review_id, error=str(exc))

    # Auto-advance: pick the next item still needing review.
    next_id: str | None = None
    try:
        rows = await storage.list_reviews(limit=20)
        for r in rows:
            rid = str(r.get("id") or "")
            if rid and rid != review_id and (r.get("decision") in (None, "pending", "human_review")):
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
        "actions": actions,
        "platform_url": platform_url,
        "next_id": next_id,
    }
