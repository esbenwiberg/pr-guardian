"""Dashboard API: stats, review list, review detail, active reviews, and SSE stream."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pr_guardian.auth.dependencies import require_admin
from pr_guardian.auth.identity import Identity

from pr_guardian.agents.base import AGENT_OUTPUT_SCHEMA
from pr_guardian.agents.prompt_composer import CROSS_LANGUAGE_SECTION
from pr_guardian.config.loader import apply_global_settings, load_service_defaults
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import event_bus
from pr_guardian.decision.finding_triage import tag_findings_with_triage
from pr_guardian.llm.factory import create_llm_client
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.persistence.storage import finding_signature
from pr_guardian.platform.factory import create_adapter, create_github_adapter
from pr_guardian.wizard.capability_clusterer import (
    FileSummary,
    FindingSummary,
    cluster_capabilities,
)

log = structlog.get_logger()

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats():
    """Aggregate statistics for the dashboard overview."""
    try:
        return await storage.get_stats()
    except Exception:
        return {
            "total_reviews": 0,
            "active_reviews": 0,
            "decision_counts": {"auto_approve": 0, "human_review": 0, "reject": 0, "hard_block": 0},
            "risk_tier_counts": {},
            "severity_counts": {},
            "avg_score": 0,
            "avg_duration_ms": 0,
            "avg_cost_usd": 0.0,
            "total_cost_usd": 0,
            "top_repos": [],
            "cost_per_day": [],
        }


@router.get("/reviews")
async def dashboard_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    decision: str | None = Query(None),
    author: str | None = Query(None),
):
    """Paginated list of reviews with optional filters."""
    try:
        return await storage.list_reviews(limit=limit, offset=offset, repo=repo, decision=decision, author=author)
    except Exception:
        return []


@router.get("/my-reviews")
async def my_reviews(
    author: str = Query(..., description="PR author username"),
    limit: int = Query(10, ge=1, le=100),
    decision: str | None = Query(None),
):
    """Reviews for a specific author — most recent first.

    Useful for agents or CLI tools to pull a user's reviews
    and work through the findings.
    """
    try:
        return await storage.list_reviews(limit=limit, author=author, decision=decision)
    except Exception:
        return []


@router.get("/reviews/{review_id}")
async def dashboard_review_detail(review_id: uuid.UUID):
    """Full detail for a single review, enriched with dismissal data."""
    row = await storage.get_review(review_id)
    if not row:
        return {"error": "not found"}

    # Enrich findings with dismissal info
    try:
        dismissals = await storage.get_active_dismissals(
            row["pr_id"], row["repo"], row["platform"],
        )
        sig_map = {d["signature"]: d for d in dismissals}

        dismissal_count = 0
        for agent in row.get("agent_results", []):
            for f in agent.get("findings", []):
                sig = finding_signature(
                    f.get("file", ""), f.get("category", ""), agent["agent_name"],
                )
                match = sig_map.get(sig)
                f["dismissal"] = match
                if match:
                    dismissal_count += 1

        row["dismissal_count"] = dismissal_count

        # Tag every finding with a triage class (noise / fyi / decision) so
        # the wizard can decide what to surface vs. roll into an audit count.
        # Runs after dismissal enrichment because dismissed findings classify
        # as noise.
        triage_counts = tag_findings_with_triage(row.get("agent_results", []))
        row["triage_counts"] = triage_counts

        # Collect active dismissals that didn't match any current finding
        matched_sigs = {
            finding_signature(f.get("file", ""), f.get("category", ""), a["agent_name"])
            for a in row.get("agent_results", [])
            for f in a.get("findings", [])
            if (f.get("dismissal"))
        }
        unmatched_active = [d for d in dismissals if d["signature"] not in matched_sigs]

        # Also include archived (resolved) dismissals from prior reviews
        archived = await storage.get_archived_dismissals(
            row["pr_id"], row["repo"], row["platform"],
        )
        row["prior_dismissals"] = unmatched_active + archived
    except Exception:
        row["dismissal_count"] = 0
        row["prior_dismissals"] = []
        row.setdefault("triage_counts", {"noise": 0, "fyi": 0, "decision": 0})

    return row


def _parse_patch_lines(patch: str) -> list[dict]:
    """Parse a unified diff patch string into a flat list of annotated lines.

    Each entry has:
      new_ln  — new-file line number (None for pure deletions)
      old_ln  — old-file line number (None for pure additions)
      marker  — '+', '-', or ' '
      content — line text without the leading marker character
      type    — 'add', 'del', or 'ctx'
    """
    result: list[dict] = []
    new_ln = old_ln = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old_ln = int(m.group(1))
                new_ln = int(m.group(2))
            continue
        if raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            result.append({"new_ln": new_ln, "old_ln": None, "marker": "+", "content": raw[1:], "type": "add"})
            new_ln += 1
        elif raw.startswith("-"):
            result.append({"new_ln": new_ln, "old_ln": old_ln, "marker": "-", "content": raw[1:], "type": "del"})
            old_ln += 1
        else:
            content = raw[1:] if raw else ""
            result.append({"new_ln": new_ln, "old_ln": old_ln, "marker": " ", "content": content, "type": "ctx"})
            new_ln += 1
            old_ln += 1
    return result


def _extract_hunk(patch: str, target_line: int, context: int) -> list[dict]:
    """Return lines within [target_line - context, target_line + context] of the new file."""
    lo, hi = target_line - context, target_line + context
    out = []
    for ln in _parse_patch_lines(patch):
        pos = ln["new_ln"]  # del lines carry the new_ln they belong to (next add/ctx)
        if lo <= pos <= hi:
            out.append({
                "ln": ln["old_ln"] if ln["type"] == "del" else ln["new_ln"],
                "marker": ln["marker"],
                "content": ln["content"],
                "type": ln["type"],
            })
    return out


@router.get("/reviews/{review_id}/diff")
async def dashboard_review_diff(
    review_id: uuid.UUID,
    path: str | None = Query(None, description="Filter to a specific file path"),
    line: int | None = Query(None, ge=1, description="Target line number in the new file"),
    context: int = Query(3, ge=0, description="Lines of context around the target line"),
):
    """Fetch the PR diff for a review on-demand from the platform.

    Without query params: returns all files with their full patches.
    With path+line: extracts a structured hunk window around target_line ± context.
    """
    row = await storage.get_review(review_id)
    if not row:
        raise HTTPException(404, "Review not found")
    if not row.get("pr_url"):
        raise HTTPException(422, "Review has no PR URL — cannot fetch diff")

    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr

    stub, platform_name = _parse_pr_url(row["pr_url"])
    adapter = await create_github_adapter(row.get("pat_name")) if platform_name == "github" else create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    try:
        diff = await adapter.fetch_diff(pr)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch diff from platform: {e}")

    if path and line is not None:
        file_obj = next((f for f in diff.files if f.path == path), None)
        if not file_obj:
            raise HTTPException(404, f"File not found in diff: {path}")
        hunk_lines = _extract_hunk(file_obj.patch or "", line, context)
        return {"file": path, "line": line, "context": context, "lines": hunk_lines}

    return {
        "pr_id": row["pr_id"],
        "repo": row["repo"],
        "files": [
            {
                "path": f.path,
                "status": f.status,
                "old_path": f.old_path,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch,
            }
            for f in diff.files
        ],
    }


# ---------------------------------------------------------------------------
# Wizard capability clustering (Phase 3b)
# ---------------------------------------------------------------------------

# Cache the LLM clusterer's response per (review_id, head_commit_sha). A new
# commit on the PR invalidates the cache by changing the SHA, so the wizard
# always sees clusters that match what's actually in the diff.
_capability_cache: dict[tuple[str, str], dict] = {}


def _role_for_path(path: str) -> str:
    pl = path.lower()
    parts = pl.split("/")
    if any(p in {"test", "tests", "__tests__", "spec", "specs"} for p in parts) \
            or pl.endswith((".test.py", ".test.ts", ".test.js", ".spec.py", ".spec.ts", ".spec.js"))\
            or "_test." in pl or "_spec." in pl:
        return "TEST"
    if "dockerfile" in pl or ".github" in parts or "ci" in parts or "circleci" in parts:
        return "INFRA"
    if any(p in {"docs", "documentation"} for p in parts) or pl.endswith((".md", ".rst")):
        return "DOCS"
    if pl.endswith((".lock", "package-lock.json", "yarn.lock", "poetry.lock", "cargo.lock")):
        return "GENERATED"
    if pl.endswith((".cfg", ".ini", ".yaml", ".yml", ".toml", ".conf")) \
            and not pl.endswith(("pyproject.toml",)):
        return "CONFIG"
    return "PRODUCTION"


@router.get("/reviews/{review_id}/capabilities")
async def dashboard_review_capabilities(review_id: uuid.UUID):
    """Return LLM-clustered capabilities for the wizard view.

    Cached in-memory per (review_id, head_commit_sha). On cache miss, fetches
    the diff from the platform (best-effort), calls cluster_capabilities, and
    stores the result. When the platform diff fetch fails (e.g. expired creds),
    falls back to building file info from stored agent_results so the LLM call
    can still happen and return an AI-generated briefing.
    The wizard treats `source != "llm"` as a signal to fall back to its
    built-in path-prefix heuristic.
    """
    row = await storage.get_review(review_id)
    if not row:
        raise HTTPException(404, "Review not found")
    head = row.get("head_commit_sha") or ""
    cache_key = (str(review_id), head)
    cached = _capability_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cache": "hit"}

    if not row.get("pr_url"):
        raise HTTPException(422, "Review has no PR URL — cannot fetch diff")

    from pr_guardian.api.review import _hydrate_pr, _parse_pr_url
    from pr_guardian.models.pr import Diff

    stub, platform_name = _parse_pr_url(row["pr_url"])
    adapter = await create_github_adapter(row.get("pat_name")) if platform_name == "github" else create_adapter(platform_name)

    # Best-effort diff + context fetch — when platform credentials are
    # unavailable or the PR is gone, fall back to building a minimal file list
    # from the stored agent_results so the LLM can still cluster and brief.
    diff: Diff | None = None
    pr = None
    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as exc:
        log.warning("capabilities_hydrate_pr_failed", review_id=str(review_id), error=str(exc))

    if pr is not None:
        try:
            diff = await adapter.fetch_diff(pr)
        except Exception as exc:
            log.warning("capabilities_diff_fetch_failed", review_id=str(review_id), error=str(exc))

    # Fetch PR body and commit messages for the LLM briefing.  Best-effort —
    # fall back to the DB-stored summary so the briefing still has context.
    pr_body = ""
    commit_messages: list[str] = []
    if pr is not None:
        try:
            pr_body, commit_messages = await adapter.fetch_pr_body_and_commits(pr)
        except Exception as exc:
            log.warning("capabilities_pr_context_failed", review_id=str(review_id), error=str(exc))
    if not pr_body:
        # The DB does not store the original PR description, so fall back to
        # the AI-generated review summary which captures the same intent and
        # gives the LLM useful context for the briefing.
        pr_body = row.get("body") or row.get("summary") or ""

    findings_by_path: dict[str, list[dict]] = {}
    for agent in row.get("agent_results") or []:
        for f in agent.get("findings") or []:
            if f.get("dismissal") or not f.get("file"):
                continue
            findings_by_path.setdefault(f["file"], []).append(f)

    if diff is not None:
        files = [
            FileSummary(
                path=f.path,
                role=_role_for_path(f.path),
                locs=(f.additions or 0) + (f.deletions or 0),
                finding_count=len(findings_by_path.get(f.path, [])),
            )
            for f in diff.files
        ]
        file_patches = {
            f.path: (f.patch or "")
            for f in diff.files
            if f.patch
        }
    else:
        # Build a minimal file list from stored findings when diff is unavailable.
        # This allows the LLM to still cluster by file roles and finding patterns.
        all_file_paths: set[str] = set(findings_by_path.keys())
        files = [
            FileSummary(
                path=path,
                role=_role_for_path(path),
                locs=0,
                finding_count=len(findings_by_path.get(path, [])),
            )
            for path in sorted(all_file_paths)
        ]
        file_patches = {}

    findings = [
        FindingSummary(
            file=f.get("file", ""),
            severity=f.get("severity", "low"),
            category=f.get("category", "") or "",
        )
        for items in findings_by_path.values()
        for f in items
    ]

    config = await apply_global_settings(GuardianConfig(**load_service_defaults()))
    llm = create_llm_client(config)

    result = await cluster_capabilities(
        files=files,
        findings=findings,
        pr_title=row.get("title") or "",
        pr_body=pr_body,
        llm_client=llm,
        commit_messages=commit_messages,
        file_patches=file_patches,
    )

    response = {
        "source": result.source,
        "capabilities": [
            {"name": c.name, "intent": c.intent, "files": list(c.files), "layers": list(c.layers)}
            for c in result.capabilities
        ],
        "briefing": result.briefing,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
    }
    _capability_cache[cache_key] = response
    return {**response, "cache": "miss"}


class ResolvePRRequest(BaseModel):
    pr_url: str


@router.post("/resolve-pr")
async def resolve_pr(body: ResolvePRRequest):
    """Given a PR URL, check for an existing review or confirm the URL is valid.

    Returns { mode: 'existing', review_id } if a completed review is found,
    or { mode: 'live', pr_url } to browse the diff directly.
    """
    from pr_guardian.api.review import _parse_pr_url

    try:
        _parse_pr_url(body.pr_url)
    except Exception as e:
        raise HTTPException(400, str(e))

    try:
        review = await storage.find_review_by_pr_url(body.pr_url)
    except Exception:
        review = None

    if review:
        return {"mode": "existing", "review_id": str(review["id"])}
    return {"mode": "live", "pr_url": body.pr_url}


@router.get("/live-diff")
async def live_diff(pr_url: str = Query(..., description="PR URL to fetch diff for")):
    """Fetch the diff for an arbitrary PR URL without requiring a stored review."""
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    try:
        stub, platform_name = _parse_pr_url(pr_url)
    except Exception as e:
        raise HTTPException(400, str(e))

    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    try:
        diff = await adapter.fetch_diff(pr)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch diff from platform: {e}")

    return {
        "pr_url": pr_url,
        "pr_id": pr.pr_id,
        "repo": pr.repo,
        "title": pr.title,
        "author": pr.author,
        "source_branch": pr.source_branch,
        "target_branch": pr.target_branch,
        "files": [
            {
                "path": f.path,
                "status": f.status,
                "old_path": f.old_path,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch,
            }
            for f in diff.files
        ],
    }


@router.get("/active")
async def dashboard_active():
    """Currently in-progress reviews."""
    try:
        return await storage.get_active_reviews()
    except Exception:
        return []


@router.delete("/reviews/{review_id}")
async def dashboard_cancel_review(review_id: uuid.UUID):
    """Cancel/dismiss a stuck review, marking it as errored."""
    await storage.mark_review_failed(review_id, "Cancelled by user")
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Finding dismissals (feedback loop)
# ---------------------------------------------------------------------------

_VALID_DISMISSAL_STATUSES = {"by_design", "false_positive", "acknowledged", "will_fix"}


class DismissRequest(BaseModel):
    status: str
    comment: str = ""


class BatchDismissRequest(BaseModel):
    finding_ids: list[uuid.UUID]
    status: str
    comment: str = ""


@router.post("/findings/batch-dismiss")
async def batch_dismiss(body: BatchDismissRequest):
    """Dismiss multiple findings in one request."""
    if body.status not in _VALID_DISMISSAL_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {_VALID_DISMISSAL_STATUSES}")

    dismissed = 0
    not_found: list[str] = []
    signatures: list[str] = []

    for fid in body.finding_ids:
        review = await _find_review_for_finding(fid)
        if not review:
            not_found.append(str(fid))
            continue

        finding_dict, agent_name = review["_matched_finding"], review["_matched_agent"]
        await storage.upsert_dismissal(
            pr_id=review["pr_id"],
            repo=review["repo"],
            platform=review["platform"],
            finding=finding_dict,
            agent_name=agent_name,
            status=body.status,
            comment=body.comment,
        )
        sig = finding_signature(finding_dict["file"], finding_dict["category"], agent_name)
        signatures.append(sig)
        dismissed += 1

    return {"dismissed": dismissed, "not_found": not_found, "signatures": signatures}


@router.post("/findings/{finding_id}/dismiss")
async def dismiss_finding(finding_id: uuid.UUID, body: DismissRequest):
    """Dismiss a finding with a status and optional comment."""
    if body.status not in _VALID_DISMISSAL_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {_VALID_DISMISSAL_STATUSES}")

    # Look up the finding to get file/category/agent_name + PR context
    review = await _find_review_for_finding(finding_id)
    if not review:
        raise HTTPException(404, "Finding not found")

    finding_dict, agent_name = review["_matched_finding"], review["_matched_agent"]

    dismissal_id = await storage.upsert_dismissal(
        pr_id=review["pr_id"],
        repo=review["repo"],
        platform=review["platform"],
        finding=finding_dict,
        agent_name=agent_name,
        status=body.status,
        comment=body.comment,
    )
    return {
        "id": str(dismissal_id),
        "signature": finding_signature(finding_dict["file"], finding_dict["category"], agent_name),
    }


@router.delete("/dismissals/{dismissal_id}")
async def undismiss_finding(dismissal_id: uuid.UUID):
    """Remove a dismissal (un-dismiss)."""
    deleted = await storage.remove_dismissal(dismissal_id)
    if not deleted:
        raise HTTPException(404, "Dismissal not found")
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Human verdict submission (used by the wizard's final-step buttons)
# ---------------------------------------------------------------------------

_VALID_VERDICTS = {"approve", "approve_with_fixes", "decline"}


class SubmitVerdictRequest(BaseModel):
    verdict: str
    comment: str = ""


def _summarise_decisions(review_dict: dict) -> dict[str, int]:
    """Tally the per-finding dismissal statuses on this review's PR."""
    counts = {"acknowledged": 0, "will_fix": 0, "false_positive": 0, "by_design": 0}
    for agent in (review_dict.get("agent_results") or []):
        for f in (agent.get("findings") or []):
            d = f.get("dismissal")
            if d and d.get("status") in counts:
                counts[d["status"]] += 1
    return counts


def _build_verdict_body(verdict: str, reviewer_comment: str, decision_counts: dict[str, int]) -> str:
    """Compose the comment body posted to the platform alongside the verdict."""
    accepted = decision_counts.get("acknowledged", 0)
    fixes = decision_counts.get("will_fix", 0)
    dismissed = decision_counts.get("false_positive", 0) + decision_counts.get("by_design", 0)

    headline = {
        "approve": "**Reviewed and approved.**",
        "approve_with_fixes": "**Approved — with the follow-ups noted below.**",
        "decline": "**Changes requested before this can merge.**",
    }[verdict]

    parts: list[str] = [headline]
    if reviewer_comment.strip():
        parts.append(reviewer_comment.strip())

    summary_bits: list[str] = []
    if accepted: summary_bits.append(f"{accepted} accepted")
    if fixes: summary_bits.append(f"{fixes} fix{'es' if fixes != 1 else ''} requested")
    if dismissed: summary_bits.append(f"{dismissed} dismissed")
    if summary_bits:
        parts.append("---")
        parts.append("Per-concern decisions: " + " · ".join(summary_bits) + ".")
    parts.append("_Posted from PR Guardian wizard review._")
    return "\n\n".join(parts)


@router.post("/reviews/{review_id}/submit-verdict")
async def submit_verdict(review_id: uuid.UUID, body: SubmitVerdictRequest):
    """Post the reviewer's final verdict back to the platform (GitHub / ADO)
    and record it on the review for audit. Used by the wizard's final step."""
    if body.verdict not in _VALID_VERDICTS:
        raise HTTPException(400, f"Invalid verdict. Must be one of: {sorted(_VALID_VERDICTS)}")

    review = await storage.get_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")

    platform_str = (review.get("platform") or "").lower()
    if platform_str not in {p.value for p in Platform}:
        raise HTTPException(400, f"Review has no usable platform field: {platform_str!r}")

    pr = PlatformPR(
        platform=Platform(platform_str),
        pr_id=review.get("pr_id", ""),
        repo=review.get("repo", ""),
        repo_url=review.get("pr_url", ""),  # not strictly required by the methods we call
        source_branch=review.get("source_branch", ""),
        target_branch=review.get("target_branch", ""),
        author=review.get("author", ""),
        title=review.get("title", ""),
        head_commit_sha=review.get("head_commit_sha", ""),
        org=review.get("org", ""),
        project=review.get("project", ""),
    )

    adapter = await create_github_adapter() if platform_str == "github" else create_adapter(platform_str)
    decision_counts = _summarise_decisions(review)
    comment_body = _build_verdict_body(body.verdict, body.comment, decision_counts)

    actions: list[str] = []
    posted = True
    error: str | None = None
    try:
        if body.verdict == "approve":
            await adapter.approve_pr(pr)
            actions.append("approve_pr")
            if body.comment.strip():
                await adapter.post_comment(pr, comment_body)
                actions.append("post_comment")
        elif body.verdict == "approve_with_fixes":
            await adapter.approve_pr(pr)
            actions.append("approve_pr")
            await adapter.post_comment(pr, comment_body)
            actions.append("post_comment")
        elif body.verdict == "decline":
            await adapter.request_changes(pr, comment_body)
            actions.append("request_changes")
    except Exception as exc:  # noqa: BLE001 — surface any platform error to the caller
        posted = False
        error = f"{type(exc).__name__}: {exc}"
        log.error("submit_verdict_failed", review_id=str(review_id), error=error)

    # Always record the attempt on the review (even on platform failure) so the
    # audit trail captures intent.
    await storage.append_review_log_entry(review_id, {
        "kind": "human_verdict",
        "verdict": body.verdict,
        "reviewer_comment": body.comment,
        "decision_counts": decision_counts,
        "platform_actions": actions,
        "posted": posted,
        "error": error,
        "at": datetime.now(timezone.utc).isoformat(),
    })

    if not posted:
        raise HTTPException(502, f"Failed to post verdict to platform: {error}")

    return {
        "posted": True,
        "verdict": body.verdict,
        "platform_actions": actions,
        "decision_counts": decision_counts,
    }


@router.post("/reviews/{review_id}/re-review")
async def re_review(review_id: uuid.UUID, request: Request):
    """Focused re-review: re-evaluate original findings against incremental changes.

    Does NOT run the full pipeline. Instead:
    1. Fetches incremental diff (old commit → current HEAD)
    2. Collects non-dismissed findings from the original review
    3. Asks each agent to re-evaluate its own findings
    4. Returns kept/resolved/updated findings
    """
    review = await storage.get_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    if not review.get("pr_url"):
        raise HTTPException(422, "Review has no PR URL — cannot re-review")

    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr

    stub, platform_name = _parse_pr_url(review["pr_url"])
    adapter = await create_github_adapter(review.get("pat_name")) if platform_name == "github" else create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    base_url = str(request.base_url).rstrip("/")

    async def _run_bg():
        import traceback
        try:
            from pr_guardian.core.orchestrator import run_re_review
            from pr_guardian.persistence.storage import (
                finding_signature as _fsig,
                infer_fixes,
            )
            result = await run_re_review(
                pr, adapter, original_review=review,
                post_comment=True, base_url=base_url,
            )
            if result is None:
                return
            prev_sigs = {
                _fsig(f.get("file", ""), f.get("category", ""), ar["agent_name"])
                for ar in review.get("agent_results", [])
                for f in ar.get("findings", [])
            }
            current_sigs = {
                _fsig(f.file, f.category, ar.agent_name)
                for ar in result.agent_results
                for f in ar.findings
            }
            await infer_fixes(pr.pr_id, prev_sigs, current_sigs, pr.head_commit_sha)
        except Exception as e:
            log.error("re_review_failed", pr_id=pr.pr_id, error=str(e), traceback=traceback.format_exc())

    asyncio.create_task(_run_bg())

    # Count non-dismissed findings for the response
    active_findings = sum(
        len(a.get("findings", []))
        for a in review.get("agent_results", [])
    )
    return {
        "status": "queued",
        "pr_id": review["pr_id"],
        "mode": "re_evaluate",
        "findings_to_evaluate": active_findings,
    }


async def _find_review_for_finding(finding_id: uuid.UUID) -> dict | None:
    """Look up a finding by ID and return its review + matched finding dict + agent name."""
    from pr_guardian.persistence.database import async_session as get_session
    from pr_guardian.persistence.models import FindingRow, AgentResultRow, ReviewRow

    async with get_session() as session:
        from sqlalchemy import select as sel
        q = sel(FindingRow).where(FindingRow.id == finding_id)
        f_row = (await session.scalars(q)).first()
        if not f_row:
            return None

        ar_row = await session.get(AgentResultRow, f_row.agent_result_id)
        if not ar_row:
            return None

        r_row = await session.get(ReviewRow, ar_row.review_id)
        if not r_row:
            return None

        return {
            "pr_id": r_row.pr_id,
            "repo": r_row.repo,
            "platform": r_row.platform,
            "_matched_finding": {
                "file": f_row.file,
                "line": f_row.line,
                "category": f_row.category,
                "severity": f_row.severity,
                "certainty": f_row.certainty,
                "description": f_row.description,
            },
            "_matched_agent": ar_row.agent_name,
        }


@router.get("/events")
async def dashboard_events():
    """SSE stream of real-time review progress events."""

    async def generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        async for event in event_bus.subscribe():
            yield event.to_sse()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Scan dashboard endpoints
# ---------------------------------------------------------------------------


@router.get("/scans")
async def dashboard_scans(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: str | None = Query(None),
    scan_type: str | None = Query(None),
):
    """Paginated list of scans."""
    try:
        return await storage.list_scans(limit=limit, offset=offset, repo=repo, scan_type=scan_type)
    except Exception:
        return []


@router.get("/scans/{scan_id}")
async def dashboard_scan_detail(scan_id: uuid.UUID):
    """Full detail for a single scan."""
    try:
        row = await storage.get_scan(scan_id)
    except Exception:
        return {"error": "not found"}
    if not row:
        return {"error": "not found"}
    return row


@router.get("/scan-stats")
async def dashboard_scan_stats():
    """Aggregate scan statistics."""
    try:
        return await storage.get_scan_stats()
    except Exception:
        return {"total_scans": 0, "type_counts": {}, "severity_counts": {}, "total_cost_usd": 0, "avg_cost_usd": 0}


# ---------------------------------------------------------------------------
# Prompt management
# ---------------------------------------------------------------------------


class PromptUpdate(BaseModel):
    content: str


@router.get("/prompts")
async def list_prompts(identity: Identity = Depends(require_admin)):
    """All agent prompts with override status, plus shared system sections."""
    agents = await storage.get_all_prompts()
    return {
        "agents": agents,
        "output_schema": AGENT_OUTPUT_SCHEMA.strip(),
        "cross_language_section": CROSS_LANGUAGE_SECTION.strip(),
    }


@router.put("/prompts/{agent_name}")
async def update_prompt(agent_name: str, body: PromptUpdate, identity: Identity = Depends(require_admin)):
    """Create or update a prompt override for an agent."""
    await storage.set_prompt_override(agent_name, body.content)
    return {"status": "saved", "agent_name": agent_name}


@router.delete("/prompts/{agent_name}")
async def reset_prompt(agent_name: str, identity: Identity = Depends(require_admin)):
    """Delete a prompt override, reverting to the file default."""
    deleted = await storage.delete_prompt_override(agent_name)
    return {"status": "reset" if deleted else "no_override", "agent_name": agent_name}


# ---------------------------------------------------------------------------
# Settings (LLM provider)
# ---------------------------------------------------------------------------


def _mask_key(key: str | None) -> str:
    """Return masked version of an API key for display, or empty string."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


@router.get("/settings")
async def get_settings(identity: Identity = Depends(require_admin)):
    """Current LLM provider settings (API keys are masked)."""
    try:
        config = await storage.get_global_config()
    except Exception:
        config = {}

    active = config.get("llm.active_provider", "anthropic")

    # Anthropic key: DB override > env var
    anthropic_db_key = config.get("llm.anthropic.api_key", "")
    anthropic_env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_key = anthropic_db_key or anthropic_env_key

    # Azure AI Foundry
    azure_db_key = config.get("llm.azure_ai_foundry.api_key", "")
    azure_env_key = os.environ.get("AZURE_AI_FOUNDRY_API_KEY", "")
    azure_key = azure_db_key or azure_env_key
    azure_endpoint = config.get("llm.azure_ai_foundry.endpoint_url", "")

    return {
        "active_provider": active,
        "anthropic": {
            "api_key_masked": _mask_key(anthropic_key),
            "api_key_source": "settings" if anthropic_db_key else ("env" if anthropic_env_key else ""),
        },
        "azure_ai_foundry": {
            "endpoint_url": azure_endpoint,
            "api_key_masked": _mask_key(azure_key),
            "api_key_source": "settings" if azure_db_key else ("env" if azure_env_key else ""),
        },
    }


class SettingsUpdate(BaseModel):
    active_provider: str  # "anthropic" or "azure-ai-foundry"
    anthropic_api_key: str | None = None  # blank = keep existing
    azure_endpoint_url: str | None = None
    azure_api_key: str | None = None  # blank = keep existing


@router.put("/settings")
async def update_settings(body: SettingsUpdate, identity: Identity = Depends(require_admin)):
    """Update LLM provider settings."""
    if body.active_provider not in ("anthropic", "azure-ai-foundry"):
        return {"status": "error", "detail": "Invalid provider"}

    # Validate: switching to azure requires endpoint + key
    if body.active_provider == "azure-ai-foundry":
        try:
            existing = await storage.get_global_config()
        except Exception:
            existing = {}

        has_endpoint = body.azure_endpoint_url or existing.get("llm.azure_ai_foundry.endpoint_url")
        has_key = (
            body.azure_api_key
            or existing.get("llm.azure_ai_foundry.api_key")
            or os.environ.get("AZURE_AI_FOUNDRY_API_KEY")
        )
        if not has_endpoint:
            return {"status": "error", "detail": "Azure AI Foundry endpoint URL is required"}
        if not has_key:
            return {"status": "error", "detail": "Azure AI Foundry API key is required"}

    await storage.set_global_config("llm.active_provider", body.active_provider)

    if body.anthropic_api_key:
        await storage.set_global_config("llm.anthropic.api_key", body.anthropic_api_key)

    if body.azure_endpoint_url is not None:
        await storage.set_global_config("llm.azure_ai_foundry.endpoint_url", body.azure_endpoint_url)

    if body.azure_api_key:
        await storage.set_global_config("llm.azure_ai_foundry.api_key", body.azure_api_key)

    return {"status": "saved"}
