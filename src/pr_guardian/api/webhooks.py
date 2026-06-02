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
        repo, head_sha = _github_repo_and_sha(x_github_event, body)
        if not repo or not head_sha:
            return {"status": "ignored", "reason": "missing repo or head sha"}
        updated = await readiness.evaluate_candidates_for_sha(
            platform="github",
            repo=repo,
            head_sha=head_sha,
            source=f"github:{x_github_event}",
        )
        return {"status": "evaluated", "count": len(updated)}

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
    )
    log.info("webhook_candidate_evaluated", platform="ado", pr_id=pr.pr_id)
    return {"status": "candidate" if candidate else "ignored", "pr_id": pr.pr_id}
