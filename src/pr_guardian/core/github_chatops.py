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

# Dismiss command, used when an author replies to a Guardian inline comment:
#   @guardian dismiss false_positive: reason
#   @pr-guardian dismiss by_design
# The status and reason are both optional; status defaults to "acknowledged".
_DISMISS_STATUSES = {"false_positive", "by_design", "acknowledged", "will_fix"}
_DEFAULT_DISMISS_STATUS = "acknowledged"
_DISMISS_RE = re.compile(
    r"(?im)@(?:guardian|pr-guardian)\s+dismiss\b"
    r"(?:\s+(false_positive|by_design|acknowledged|will_fix))?"
    r"(?:\s*[:\-]\s*(.*))?"
)

# Safety gate: the PR author may only self-dismiss low/medium, non-security
# findings via a comment. High/critical and security findings still require a
# code fix, an API-key dismissal, or a human override.
_COMMENT_UNDISMISSABLE_AGENTS = {"security_privacy"}
_COMMENT_DISMISSABLE_SEVERITIES = {"low", "medium"}


def is_github_dismiss_command(body: str) -> bool:
    """Return True when a comment body contains a Guardian dismiss command."""
    return bool(_DISMISS_RE.search(body or ""))


def parse_dismiss_command(body: str) -> tuple[str, str] | None:
    """Parse `@guardian dismiss <status>: <reason>` into (status, reason)."""
    m = _DISMISS_RE.search(body or "")
    if not m:
        return None
    status = (m.group(1) or _DEFAULT_DISMISS_STATUS).lower()
    reason = (m.group(2) or "").strip()
    return status, reason


def _is_comment_dismissable(finding: dict) -> bool:
    """Whether a single stored finding payload may be dismissed via a comment."""
    sev = (finding.get("severity") or "").lower()
    if sev not in _COMMENT_DISMISSABLE_SEVERITIES:
        return False
    if (finding.get("agent_name") or "") in _COMMENT_UNDISMISSABLE_AGENTS:
        return False
    return True


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


async def handle_github_review_comment_reply(
    *,
    repo: str,
    pr_id: str | int,
    comment_id: str | int,
    in_reply_to_id: str,
    body: str,
    commenter: str,
    author_association: str = "",
    pr_author: str = "",
    source: str,
    base_url: str = "",
) -> dict[str, Any]:
    """Handle a reply to a Guardian inline review comment as a dismiss command.

    Maps the reply's parent comment back to the finding(s) it carried and records
    a dismissal for the low/medium, non-security findings among them. Does NOT
    trigger a re-review — the verdict refreshes on the next `@guardian re-review`.
    """
    parsed = parse_dismiss_command(body)
    if parsed is None:
        return {"status": "ignored", "reason": "no_dismiss_command"}
    status, reason = parsed

    # Resolve the parent comment to the finding(s) it carried. If it isn't a
    # Guardian inline comment, there's nothing to dismiss.
    parent = await storage.find_inline_comment_by_platform_id(
        "github", repo, str(pr_id), str(in_reply_to_id)
    )
    if parent is None:
        return {"status": "ignored", "reason": "not_a_guardian_finding_comment"}

    command_id = await storage.claim_chatops_command(
        platform="github",
        repo=repo,
        pr_id=str(pr_id),
        command="dismiss",
        external_id=str(comment_id),
        source=source,
        actor=commenter,
        payload={
            "author_association": author_association,
            "body": body,
            "in_reply_to_id": str(in_reply_to_id),
        },
    )
    if command_id is None:
        return {"status": "ignored", "reason": "duplicate"}

    if not _is_authorized(commenter, author_association, pr_author):
        await _mark_command(command_id, "ignored", "unauthorized")
        log.info(
            "github_dismiss_unauthorized",
            repo=repo,
            pr_id=str(pr_id),
            commenter=commenter,
            association=author_association,
        )
        return {"status": "ignored", "reason": "unauthorized"}

    # Build an adapter from the originating review's connection for the ack reply.
    adapter: GitHubAdapter | None = None
    try:
        review = await storage.get_review(uuid.UUID(parent["review_id"]))
        if review is not None:
            adapter = await _fresh_adapter_for_review(review)
    except Exception as exc:  # noqa: BLE001
        log.warning("github_dismiss_adapter_failed", repo=repo, error=str(exc))

    async def _reply(msg: str) -> None:
        if adapter is None:
            return
        try:
            await adapter.reply_to_review_comment(repo, pr_id, in_reply_to_id, msg)
        except Exception as exc:  # noqa: BLE001
            log.warning("github_dismiss_reply_failed", repo=repo, error=str(exc))

    try:
        if status not in _DISMISS_STATUSES:
            await _mark_command(command_id, "ignored", f"invalid status: {status}")
            await _reply(
                f"Guardian: `{status}` isn't a valid dismiss status. "
                f"Use one of: {', '.join(sorted(_DISMISS_STATUSES))}."
            )
            return {"status": "ignored", "reason": "invalid_status"}

        findings = parent.get("findings", [])
        eligible = [f for f in findings if _is_comment_dismissable(f)]
        blocked = [f for f in findings if not _is_comment_dismissable(f)]

        if not eligible:
            await _mark_command(command_id, "ignored", "no dismissable findings")
            await _reply(
                "Guardian: this finding can't be dismissed via comment. High/critical "
                "and security findings need a code fix, an API-key dismissal, or a "
                "human override."
            )
            return {"status": "ignored", "reason": "not_dismissable"}

        dismissed = 0
        for f in eligible:
            try:
                await storage.upsert_dismissal(
                    pr_id=parent["pr_id"],
                    repo=parent["repo"],
                    platform=parent["platform"],
                    finding=f,
                    agent_name=f.get("agent_name", ""),
                    status=status,
                    comment=f"[comment dismiss by {commenter}] {reason}".strip(),
                )
                dismissed += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("github_dismiss_upsert_failed", repo=repo, error=str(exc))

        note = ""
        if blocked:
            note = (
                f" {len(blocked)} finding(s) in this comment can't be self-dismissed "
                "(high/critical or security)."
            )
        await _reply(
            f"Guardian: recorded {dismissed} dismissal(s) as `{status}`. They'll be "
            f"excluded on the next `@guardian re-review`.{note}"
        )
        await _mark_command(
            command_id,
            "completed",
            f"dismissed {dismissed}",
            review_id=parent["review_id"],
        )
        log.info(
            "github_dismiss_recorded",
            repo=repo,
            pr_id=str(pr_id),
            dismissed=dismissed,
            status=status,
        )
        return {"status": "dismissed", "count": dismissed, "dismiss_status": status}
    finally:
        if adapter is not None:
            await adapter.close()


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
