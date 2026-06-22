from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from pr_guardian.core import readiness
from pr_guardian.models.pr import PlatformPR
from pr_guardian.platform.factory import normalize_webhook
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()


async def _load_dismissals(pr: PlatformPR) -> list[dict] | None:
    """Load active dismissals from DB for a PR, or None if DB unavailable."""
    try:
        from pr_guardian.persistence import storage

        return await storage.get_active_dismissals(
            pr.pr_id,
            pr.repo,
            pr.platform.value,
        )
    except Exception:
        return None


router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC signature."""
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _dev_webhook_bypass_enabled() -> bool:
    return os.environ.get("GUARDIAN_WEBHOOK_DEV_BYPASS", "").lower() in {"1", "true", "yes"}


def _require_github_secret(payload: bytes, signature: str) -> None:
    if _dev_webhook_bypass_enabled():
        return
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not _verify_github_signature(payload, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")


def _require_ado_secret(request: Request) -> None:
    if _dev_webhook_bypass_enabled():
        return
    secret = os.environ.get("ADO_WEBHOOK_SECRET", "")
    token = request.headers.get("x-ado-webhook-token", "")
    authorization = request.headers.get("authorization", "")
    bearer = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else ""
    )
    if not secret or not (
        hmac.compare_digest(token, secret) or hmac.compare_digest(bearer, secret)
    ):
        raise HTTPException(status_code=401, detail="Invalid Azure DevOps webhook token")


def _github_repo_and_sha(event_type: str, body: dict[str, Any]) -> tuple[str, str]:
    repo = (body.get("repository") or {}).get("full_name") or ""
    if event_type == "check_run":
        return repo, (body.get("check_run") or {}).get("head_sha") or ""
    if event_type == "check_suite":
        return repo, (body.get("check_suite") or {}).get("head_sha") or ""
    if event_type == "status":
        return repo, body.get("sha") or ""
    if event_type == "workflow_run":
        return repo, (body.get("workflow_run") or {}).get("head_sha") or ""
    return repo, ""


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    """Handle GitHub webhook events."""
    raw_body = await request.body()
    _require_github_secret(raw_body, x_hub_signature_256)

    body = await request.json()
    if x_github_event in {"check_run", "check_suite", "status", "workflow_run"}:
        # A `status` event Guardian itself authored (guardian/readiness,
        # guardian/review) must not re-trigger evaluation: posting that status
        # fires this very event, which would re-post it — a loop that exhausts
        # GitHub's 1000-statuses-per-context-per-SHA cap and 422s thereafter.
        if x_github_event == "status" and str(body.get("context") or "").startswith("guardian/"):
            return {"status": "ignored", "reason": "self-authored guardian status"}
        repo, head_sha = _github_repo_and_sha(x_github_event, body)
        if not repo or not head_sha:
            return {"status": "ignored", "reason": "missing repo or head sha"}
        updated = await readiness.evaluate_candidates_for_sha(
            platform="github",
            repo=repo,
            head_sha=head_sha,
            source=f"github:{x_github_event}",
            base_url=str(request.base_url).rstrip("/"),
        )
        return {"status": "evaluated", "count": len(updated)}

    if x_github_event == "issue_comment":
        action = body.get("action", "")
        issue = body.get("issue") or {}
        comment = body.get("comment") or {}
        repo_data = body.get("repository") or {}
        if action != "created" or not issue.get("pull_request"):
            return {"status": "ignored", "reason": "not a PR comment command"}

        from pr_guardian.core.github_chatops import handle_github_comment

        repo = repo_data.get("full_name") or ""
        pr_id = str(issue.get("number") or "")
        if not repo or not pr_id:
            return {"status": "ignored", "reason": "missing repo or PR number"}
        return await handle_github_comment(
            repo=repo,
            pr_id=pr_id,
            comment_id=str(comment.get("id") or ""),
            body=comment.get("body") or "",
            commenter=(comment.get("user") or {}).get("login") or "",
            author_association=comment.get("author_association") or "",
            pr_author=(issue.get("user") or {}).get("login") or "",
            source="github:issue_comment",
            base_url=str(request.base_url).rstrip("/"),
        )

    if x_github_event == "pull_request_review_comment":
        action = body.get("action", "")
        comment = body.get("comment") or {}
        pull_request = body.get("pull_request") or {}
        repo_data = body.get("repository") or {}
        in_reply_to = comment.get("in_reply_to_id")
        # Only act on freshly-created replies to an existing review comment.
        if action != "created" or not in_reply_to:
            return {"status": "ignored", "reason": "not a review-comment reply"}

        from pr_guardian.core.github_chatops import handle_github_review_comment_reply

        repo = repo_data.get("full_name") or ""
        pr_id = str(pull_request.get("number") or "")
        if not repo or not pr_id:
            return {"status": "ignored", "reason": "missing repo or PR number"}
        return await handle_github_review_comment_reply(
            repo=repo,
            pr_id=pr_id,
            comment_id=str(comment.get("id") or ""),
            in_reply_to_id=str(in_reply_to),
            body=comment.get("body") or "",
            commenter=(comment.get("user") or {}).get("login") or "",
            author_association=comment.get("author_association") or "",
            pr_author=(pull_request.get("user") or {}).get("login") or "",
            source="github:pull_request_review_comment",
            base_url=str(request.base_url).rstrip("/"),
        )

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event type: {x_github_event}"}

    payload = WebhookPayload(
        platform="github",
        event_type=x_github_event,
        headers=dict(request.headers),
        body=body,
    )

    pr = normalize_webhook(payload)
    action = body.get("action", "")
    if pr is None and action == "closed":
        pr = normalize_webhook(
            WebhookPayload(
                platform="github",
                event_type=x_github_event,
                headers=dict(request.headers),
                body={**body, "action": "reopened"},
            )
        )
    if not pr:
        return {"status": "ignored", "reason": "not a candidate PR event"}

    if action == "closed":
        count = await readiness.supersede_candidates_for_pr(
            pr,
            source="github:pull_request",
            reason="pr_merged" if (body.get("pull_request") or {}).get("merged") else "pr_closed",
        )
        return {"status": "superseded", "count": count, "pr_id": pr.pr_id}

    candidate = await readiness.create_or_update_candidate_from_pr(
        pr,
        source="github:pull_request",
        base_url=str(request.base_url).rstrip("/"),
    )
    log.info("webhook_candidate_evaluated", platform="github", pr_id=pr.pr_id)
    return {"status": "candidate" if candidate else "ignored", "pr_id": pr.pr_id}


@router.post("/ado")
async def ado_webhook(request: Request):
    """Handle Azure DevOps webhook events."""
    _require_ado_secret(request)
    body = await request.json()
    payload = WebhookPayload(
        platform="ado",
        event_type=body.get("eventType", ""),
        headers=dict(request.headers),
        body=body,
    )

    pr = normalize_webhook(payload)
    if not pr:
        return {"status": "ignored", "reason": "not a relevant PR event"}
    status = ((body.get("resource") or {}).get("status") or "").lower()
    if status in {"completed", "abandoned"}:
        count = await readiness.supersede_candidates_for_pr(
            pr,
            source="ado:pullrequest",
            reason="pr_merged" if status == "completed" else "pr_closed",
        )
        return {"status": "superseded", "count": count, "pr_id": pr.pr_id}

    candidate = await readiness.create_or_update_candidate_from_pr(
        pr,
        source="ado:pullrequest",
        base_url=str(request.base_url).rstrip("/"),
    )
    log.info("webhook_candidate_evaluated", platform="ado", pr_id=pr.pr_id)
    return {"status": "candidate" if candidate else "ignored", "pr_id": pr.pr_id}
