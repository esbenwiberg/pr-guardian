from __future__ import annotations

import hashlib
import hmac
import os

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from pr_guardian.core.orchestrator import run_review
from pr_guardian.core.queue import ReviewQueue
from pr_guardian.platform.factory import create_adapter, normalize_webhook
from pr_guardian.platform.models import WebhookPayload

log = structlog.get_logger()
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Module-level queue (initialized in main.py lifespan)
review_queue = ReviewQueue()


def _verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC signature."""
    if not secret:
        return True  # No secret configured, skip verification
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    """Handle GitHub webhook events."""
    raw_body = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    if secret and not _verify_github_signature(raw_body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event type: {x_github_event}"}

    body = await request.json()
    payload = WebhookPayload(
        platform="github",
        event_type=x_github_event,
        headers=dict(request.headers),
        body=body,
    )

    pr = normalize_webhook(payload)
    if not pr:
        return {"status": "ignored", "reason": "not a relevant PR event"}

    if review_queue.is_duplicate(pr, pr.head_commit_sha):
        return {"status": "duplicate", "pr_id": pr.pr_id}

    adapter = create_adapter("github")
    base_url = str(request.base_url).rstrip("/")
    await review_queue.enqueue(pr, run_review(pr, adapter, base_url=base_url))

    log.info("webhook_accepted", platform="github", pr_id=pr.pr_id)
    return {"status": "queued", "pr_id": pr.pr_id}


@router.post("/ado")
async def ado_webhook(request: Request):
    """Handle Azure DevOps webhook events."""
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

    if review_queue.is_duplicate(pr, pr.head_commit_sha):
        return {"status": "duplicate", "pr_id": pr.pr_id}

    adapter = create_adapter("ado")
    base_url = str(request.base_url).rstrip("/")
    await review_queue.enqueue(pr, run_review(pr, adapter, base_url=base_url))

    log.info("webhook_accepted", platform="ado", pr_id=pr.pr_id)
    return {"status": "queued", "pr_id": pr.pr_id}
