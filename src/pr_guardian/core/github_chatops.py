from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import structlog

from pr_guardian.models.pr import PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.github import GitHubAdapter

log = structlog.get_logger()

_GUARDIAN_COMMAND = "guardian"
_RE_REVIEW_COMMAND = "re-review"  # kept for backward-compat in existing DB records

# Match @guardian or @pr-guardian (with optional " re-review" suffix).
# Negative lookaheads prevent matching @guardian-app or @guardianstuff.
_COMMAND_RE = re.compile(r"(?im)(?:^|\s)@(?:guardian|pr-guardian)(?:\s+re-review)?(?!\w)(?!-)")

# Legacy re-review-only pattern — kept so is_github_re_review_command stays compatible.
_MENTION_REVIEW_RE = re.compile(r"(?im)(?:^|\s)@pr-guardian(?:\s+|[:,]\s*)re-review\b")

_TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def is_github_command(body: str) -> bool:
    """Return True when a GitHub PR comment contains any Guardian command (@guardian or @pr-guardian)."""
    return bool(_COMMAND_RE.search(body or ""))


def is_github_re_review_command(body: str) -> bool:
    """Return whether a GitHub PR comment asks Guardian to re-review (legacy alias form only)."""
    return bool(_MENTION_REVIEW_RE.search(body or ""))


def _is_authorized(commenter: str, author_association: str, pr_author: str) -> bool:
    association = (author_association or "").upper()
    if association in _TRUSTED_ASSOCIATIONS:
        return True
    return bool(commenter and pr_author and commenter.lower() == pr_author.lower())


async def _add_eyes_reaction(adapter: GitHubAdapter, repo: str, comment_id: str) -> None:
    """Add an eyes reaction to a comment. Logs errors without propagating them."""
    react_fn = getattr(adapter, "create_issue_comment_reaction", None)
    if react_fn is None:
        return
    try:
        await react_fn(repo, comment_id, "eyes")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "github_chatops_reaction_failed",
            repo=repo,
            comment_id=comment_id,
            error=str(exc),
        )


async def _fresh_adapter_for_review(review: dict[str, Any]) -> GitHubAdapter:
    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    connection_id = review.get("connection_id")
    if connection_id:
        connection = await storage.get_connection(uuid.UUID(str(connection_id)))
        if connection is not None:
            return await build_github_adapter_from_connection(connection)

    raise ValueError(
        f"No GitHub App Connection found for review {review.get('id')}; "
        "GITHUB_TOKEN env fallback has been removed"
    )


async def _adapter_for_repo_link(repo_link: dict[str, Any]) -> GitHubAdapter:
    """Build a GitHub adapter from a repo link's connection."""
    from pr_guardian.platform.github_auth import build_github_adapter_from_connection

    connection_id = repo_link.get("connection_id")
    if not connection_id:
        raise ValueError("Repo link has no connection_id")
    connection = await storage.get_connection(uuid.UUID(str(connection_id)))
    if connection is None:
        raise ValueError(f"Connection {connection_id} not found")
    return await build_github_adapter_from_connection(connection)


async def _mark_command(
    command_id: uuid.UUID,
    status: str,
    detail: str = "",
    review_id: uuid.UUID | str | None = None,
) -> None:
    try:
        await storage.update_chatops_command(
            command_id,
            status=status,
            status_detail=detail,
            review_id=review_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "github_chatops_status_update_failed", command_id=str(command_id), error=str(exc)
        )


async def _run_re_review_background(
    command_id: uuid.UUID,
    pr: PlatformPR,
    original_review: dict[str, Any],
    base_url: str,
) -> None:
    adapter = await _fresh_adapter_for_review(original_review)
    try:
        await _mark_command(command_id, "running", review_id=original_review.get("id"))
        from pr_guardian.core.orchestrator import run_re_review

        result = await run_re_review(
            pr,
            adapter,
            original_review=original_review,
            post_comment=True,
            base_url=base_url,
        )
        if result is not None:
            prev_sigs = {
                storage.finding_signature(
                    f.get("file", ""), f.get("category", ""), ar["agent_name"]
                )
                for ar in original_review.get("agent_results", [])
                for f in ar.get("findings", [])
            }
            current_sigs = {
                storage.finding_signature(f.file, f.category, ar.agent_name)
                for ar in result.agent_results
                for f in ar.findings
            }
            await storage.infer_fixes(pr.pr_id, prev_sigs, current_sigs, pr.head_commit_sha)
        await _mark_command(command_id, "completed", review_id=original_review.get("id"))
    except Exception as exc:  # noqa: BLE001
        log.error(
            "github_chatops_re_review_failed",
            command_id=str(command_id),
            repo=pr.repo,
            pr_id=pr.pr_id,
            error=str(exc),
        )
        await _mark_command(command_id, "failed", str(exc), review_id=original_review.get("id"))
    finally:
        await adapter.close()


async def _run_first_review_background(
    command_id: uuid.UUID,
    pr: PlatformPR,
    repo_link: dict[str, Any],
    base_url: str,
) -> None:
    """Run a first Guardian review for a PR that has no previous Guardian review."""
    try:
        adapter = await _adapter_for_repo_link(repo_link)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "github_chatops_first_review_adapter_failed",
            command_id=str(command_id),
            repo=pr.repo,
            error=str(exc),
        )
        await _mark_command(command_id, "failed", f"failed to build adapter: {exc}")
        return
    try:
        await _mark_command(command_id, "running")
        from pr_guardian.core.orchestrator import run_review

        await run_review(
            pr,
            adapter,
            post_comment=True,
            base_url=base_url,
        )
        await _mark_command(command_id, "completed")
    except Exception as exc:  # noqa: BLE001
        log.error(
            "github_chatops_first_review_failed",
            command_id=str(command_id),
            repo=pr.repo,
            pr_id=pr.pr_id,
            error=str(exc),
        )
        await _mark_command(command_id, "failed", str(exc))
    finally:
        await adapter.close()


async def handle_github_comment(
    *,
    repo: str,
    pr_id: str | int,
    comment_id: str | int,
    body: str,
    commenter: str,
    author_association: str = "",
    pr_author: str = "",
    source: str,
    base_url: str = "",
) -> dict[str, Any]:
    """Handle one GitHub PR conversation comment as a possible Guardian command."""
    if not is_github_command(body):
        return {"status": "ignored", "reason": "no_command"}

    command_id = await storage.claim_chatops_command(
        platform="github",
        repo=repo,
        pr_id=str(pr_id),
        command=_GUARDIAN_COMMAND,
        external_id=str(comment_id),
        source=source,
        actor=commenter,
        payload={
            "author_association": author_association,
            "body": body,
        },
    )
    if command_id is None:
        return {"status": "ignored", "reason": "duplicate"}

    # Determine whether this is a re-review or a first review.
    review = await storage.find_latest_review_for_pr("github", repo, str(pr_id))
    repo_link: dict[str, Any] | None = None
    if review is None:
        repo_link = await storage.get_active_repo_link_for_repo(
            platform="github",
            repo=repo,
            require_auto_review=False,
        )

    # Build a command-scoped adapter (used for eyes reaction and PR fetch).
    command_adapter: GitHubAdapter | None = None
    if review is not None:
        try:
            command_adapter = await _fresh_adapter_for_review(review)
        except Exception as exc:  # noqa: BLE001
            log.warning("github_chatops_adapter_failed", repo=repo, error=str(exc))
    elif repo_link is not None:
        try:
            command_adapter = await _adapter_for_repo_link(repo_link)
        except Exception as exc:  # noqa: BLE001
            log.warning("github_chatops_adapter_failed", repo=repo, error=str(exc))

    # React with eyes immediately after claiming (best-effort).
    if command_adapter is not None:
        await _add_eyes_reaction(command_adapter, repo, str(comment_id))

    # Repo is not linked — no adapter is available (we don't know which App installation
    # to authenticate with), so no ack comment can be posted. Mark ignored and stop.
    if review is None and repo_link is None:
        await _mark_command(command_id, "ignored", "repo not linked")
        log.info(
            "github_chatops_repo_not_linked",
            repo=repo,
            pr_id=str(pr_id),
            commenter=commenter,
        )
        return {"status": "ignored", "reason": "repo_not_linked"}

    if command_adapter is None:
        await _mark_command(command_id, "failed", "no adapter available")
        return {"status": "failed", "reason": "no_adapter"}

    try:
        try:
            pr_obj = await command_adapter.fetch_pr(repo, pr_id)
        except Exception as exc:  # noqa: BLE001
            await _mark_command(command_id, "failed", f"failed to fetch PR: {exc}")
            raise

        if not _is_authorized(commenter, author_association, pr_author or pr_obj.author):
            await _mark_command(command_id, "ignored", "unauthorized")
            log.info(
                "github_chatops_command_unauthorized",
                repo=repo,
                pr_id=str(pr_id),
                commenter=commenter,
                association=author_association,
            )
            return {"status": "ignored", "reason": "unauthorized"}

        if review is not None:
            await _mark_command(command_id, "queued", review_id=review.get("id"))
            try:
                await command_adapter.post_comment(pr_obj, "Guardian: re-review queued.")
            except Exception as exc:  # noqa: BLE001
                log.warning("github_chatops_ack_failed", repo=repo, pr_id=pr_id, error=str(exc))
            asyncio.create_task(_run_re_review_background(command_id, pr_obj, review, base_url))
            log.info(
                "github_chatops_re_review_queued",
                repo=repo,
                pr_id=str(pr_id),
                comment_id=str(comment_id),
                source=source,
            )
            return {"status": "queued", "review_id": review.get("id")}
        elif repo_link is not None:
            await _mark_command(command_id, "queued")
            try:
                await command_adapter.post_comment(pr_obj, "Guardian: first review queued.")
            except Exception as exc:  # noqa: BLE001
                log.warning("github_chatops_ack_failed", repo=repo, pr_id=pr_id, error=str(exc))
            asyncio.create_task(
                _run_first_review_background(command_id, pr_obj, repo_link, base_url)
            )
            log.info(
                "github_chatops_first_review_queued",
                repo=repo,
                pr_id=str(pr_id),
                comment_id=str(comment_id),
                source=source,
            )
            return {"status": "queued", "first_review": True}
        else:
            # Should not be reachable — repo_not_linked guard above handles this.
            await _mark_command(command_id, "failed", "no review and no repo link")
            return {"status": "failed", "reason": "no_review_or_link"}
    finally:
        await command_adapter.close()


async def poll_github_pr_comments(
    adapter: GitHubAdapter,
    *,
    repo: str,
    pr: dict[str, Any],
    source: str = "poll:github",
    base_url: str = "",
) -> int:
    """Poll one GitHub PR's conversation comments for Guardian commands."""
    if int(pr.get("comments") or 0) <= 0:
        return 0

    pr_id = str(pr.get("number") or "")
    if not pr_id:
        return 0
    comments = await adapter.list_issue_comments(repo, pr_id)
    pr_author = (pr.get("user") or {}).get("login") or ""
    handled = 0
    for comment in comments:
        result = await handle_github_comment(
            repo=repo,
            pr_id=pr_id,
            comment_id=str(comment.get("id") or ""),
            body=comment.get("body") or "",
            commenter=(comment.get("user") or {}).get("login") or "",
            author_association=comment.get("author_association") or "",
            pr_author=pr_author,
            source=source,
            base_url=base_url,
        )
        if result.get("status") == "queued":
            handled += 1
    return handled
