"""Shared helper for posting and updating the sticky guidance comment on GitHub PRs.

Extracted here so both readiness and orchestrator can call it without a
cross-module private-function import.
"""

from __future__ import annotations

from typing import Any

import structlog

from pr_guardian.decision.actions import build_guidance_comment_body
from pr_guardian.models.pr import PlatformPR

log = structlog.get_logger(__name__)


async def upsert_guidance_comment(
    adapter: Any,
    pr: PlatformPR,
    state: str,
    *,
    review_url: str = "",
    storage: Any = None,
) -> str | None:
    """Post or update the sticky guidance comment on a GitHub PR.

    Returns the comment ID on success, or None if the adapter does not support
    guidance comments or if the call fails.
    """
    upsert_fn = getattr(adapter, "upsert_guidance_comment", None)
    if upsert_fn is None:
        return None
    body = build_guidance_comment_body(state, review_url=review_url)
    stored_id: str | None = None
    if storage:
        try:
            stored_id = await storage.load_guidance_comment_id(
                pr.platform.value, pr.repo, pr.pr_id
            )
        except Exception as e:
            log.warning("guidance_comment_id_load_failed", pr_id=pr.pr_id, error=str(e))
    try:
        comment_id = await upsert_fn(pr, body, stored_comment_id=stored_id)
        if storage and comment_id:
            try:
                await storage.save_guidance_comment_id(
                    pr.platform.value, pr.repo, pr.pr_id, comment_id
                )
            except Exception as e:
                log.warning("guidance_comment_id_save_failed", pr_id=pr.pr_id, error=str(e))
        return comment_id
    except Exception as e:
        log.warning("upsert_guidance_comment_failed", pr_id=pr.pr_id, error=str(e))
        return None
