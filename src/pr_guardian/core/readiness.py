from __future__ import annotations

import uuid
import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from pr_guardian.decision.actions import build_review_detail_url
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.factory import create_adapter
from pr_guardian.platform.guidance import upsert_guidance_comment
from pr_guardian.platform.protocol import PlatformAdapter, PlatformReadinessSignal

log = structlog.get_logger()

DEFAULT_QUIET_PERIOD_SECONDS = 10
DEFAULT_MAX_WAIT_MINUTES = 30
DEFAULT_ARCHMAP_MAX_WAIT_MINUTES = 10
DEFAULT_REVIEWING_STALE_MINUTES = 15

TERMINAL_CANDIDATE_STATES = {"reviewed", "superseded"}
ACTIVE_CANDIDATE_STATES = {"reviewing"}
RECOVERABLE_BLOCK_REASONS = {"checks_failed", "checks_timeout"}
GUARDIAN_STATUS_NAMES = {"guardian/readiness", "guardian/review", "pr-guardian"}
SUCCESS_STATES = {"success", "succeeded", "neutral", "skipped"}
FAILURE_STATES = {
    "failure",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "timed_out",
    "action_required",
    "rejected",
}


@dataclass(frozen=True)
class ReadinessDecision:
    state: str
    reason: str
    snapshot: dict[str, Any]

    @property
    def ready(self) -> bool:
        return self.state == "reviewing"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exc_http_status(exc: Exception) -> int | None:
    """Duck-type the HTTP status off an exception so core/ stays free of httpx.

    httpx.HTTPStatusError carries `.response.status_code`. 401/403/404 are
    persistent auth/access/not-found errors an operator must fix; everything
    else (5xx, 429, timeouts, network) is a transient blip worth retrying.
    """
    return getattr(getattr(exc, "response", None), "status_code", None)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _readiness_settings(profile: dict[str, Any] | None) -> dict[str, Any]:
    settings = (profile or {}).get("settings") or {}
    readiness = settings.get("readiness") or {}
    return {
        "quiet_period_seconds": int(
            readiness.get("quiet_period_seconds", DEFAULT_QUIET_PERIOD_SECONDS)
        ),
        "max_wait_minutes": int(readiness.get("max_wait_minutes", DEFAULT_MAX_WAIT_MINUTES)),
        "archmap_max_wait_minutes": int(
            readiness.get("archmap_max_wait_minutes", DEFAULT_ARCHMAP_MAX_WAIT_MINUTES)
        ),
        "ignored_statuses": set(readiness.get("ignored_statuses") or []),
        "ignored_checks": set(readiness.get("ignored_checks") or []),
        "archmap_expected": bool(readiness.get("archmap_expected", False)),
    }


def _candidate_pr(candidate: dict[str, Any]) -> PlatformPR:
    platform = Platform(candidate["platform"])
    return PlatformPR(
        platform=platform,
        pr_id=str(candidate["pr_id"]),
        repo=candidate["repo"],
        repo_url=candidate.get("pr_url") or "",
        source_branch="",
        target_branch="",
        author="",
        title="",
        head_commit_sha=candidate["head_sha"],
        org=candidate.get("org_url") or candidate.get("repo_owner") or "",
        project=candidate.get("project") or "",
    )


def _signal_state(signal: PlatformReadinessSignal) -> str:
    state = (signal.state or "").lower().strip()
    if state in SUCCESS_STATES:
        return "success"
    if state in FAILURE_STATES:
        return "failure"
    if state in {"pending", "queued", "in_progress", "notset", "not_set", "waiting"}:
        return "pending"
    return "pending"


def _filtered_signals(
    signals: list[PlatformReadinessSignal], settings: dict[str, Any]
) -> list[PlatformReadinessSignal]:
    ignored_checks = {str(v).lower() for v in settings["ignored_checks"]}
    ignored_statuses = {str(v).lower() for v in settings["ignored_statuses"]}
    filtered: list[PlatformReadinessSignal] = []
    for signal in signals:
        name = signal.name.strip()
        lowered = name.lower()
        if lowered in GUARDIAN_STATUS_NAMES:
            continue
        if signal.source == "check_run" and lowered in ignored_checks:
            continue
        if signal.source != "check_run" and lowered in ignored_statuses:
            continue
        filtered.append(signal)
    return filtered


def _checks_snapshot(signals: list[PlatformReadinessSignal]) -> dict[str, Any]:
    passed = [s for s in signals if _signal_state(s) == "success"]
    pending = [s for s in signals if _signal_state(s) == "pending"]
    failed = [s for s in signals if _signal_state(s) == "failure"]
    return {
        "total": len(signals),
        "passed": len(passed),
        "pending": len(pending),
        "failed": len(failed),
        "signals": [asdict(s) for s in signals],
    }


async def _adapter_for_candidate(candidate: dict[str, Any]) -> PlatformAdapter:
    platform = candidate["platform"]
    connection_id = candidate.get("connection_id")

    if platform == "github":
        if not connection_id:
            raise ValueError(
                f"GitHub readiness candidate {candidate.get('id')} has no connection_id; "
                "a GitHub App Connection is required"
            )
        connection = await storage.get_connection(uuid.UUID(connection_id))
        if connection is None:
            raise ValueError(f"Connection {connection_id} not found")
        from pr_guardian.platform.github_auth import build_github_adapter_from_connection

        return await build_github_adapter_from_connection(connection)

    # Non-GitHub platforms (ADO etc.) use PAT/token from connection or env.
    if not connection_id:
        return create_adapter(platform)
    connection = await storage.get_connection(uuid.UUID(connection_id))
    token = await storage.get_connection_token(uuid.UUID(connection_id))
    return create_adapter(
        platform,
        token_override=token,
        org_url_override=(connection or {}).get("org_url") or None,
    )


async def _post_readiness_status(
    adapter: PlatformAdapter, pr: PlatformPR, state: str, description: str
) -> bool:
    try:
        await adapter.set_readiness_status(pr, state, description)
        return True
    except Exception as exc:
        log.warning("readiness_status_write_failed", pr_id=pr.pr_id, error=str(exc))
        return False


async def _post_review_pending(adapter: PlatformAdapter, pr: PlatformPR) -> None:
    """Post guardian/review = pending immediately so merge is blocked from PR open."""
    await _post_review_status(adapter, pr, "pending", "Guardian review pending")


async def _post_review_status(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    state: str,
    description: str,
    target_url: str = "",
) -> bool:
    try:
        method = getattr(adapter, "set_review_status", None)
        if method is not None:
            try:
                await method(pr, state, description, target_url=target_url)
            except TypeError:
                await method(pr, state, description)
        else:
            try:
                await adapter.set_status(
                    pr, state, description, context="guardian/review", target_url=target_url
                )
            except TypeError:
                await adapter.set_status(pr, state, description, context="guardian/review")
        return True
    except Exception as exc:
        log.warning(
            "review_status_write_failed",
            pr_id=pr.pr_id,
            state=state,
            description=description,
            error=str(exc),
        )
        return False


def _candidate_reviewing_stale(candidate: dict[str, Any], now: datetime) -> bool:
    updated_at = _parse_dt(candidate.get("updated_at"))
    if updated_at is None:
        return True
    return now >= updated_at + timedelta(minutes=DEFAULT_REVIEWING_STALE_MINUTES)


async def create_or_update_candidate_from_pr(
    pr: PlatformPR,
    *,
    source: str = "webhook",
    adapter: PlatformAdapter | None = None,
    base_url: str = "",
    remint: bool = True,
) -> dict[str, Any] | None:
    """Create/update a candidate for an exact opted-in repo and evaluate it."""
    link = await storage.get_active_repo_link_for_repo(
        platform=pr.platform.value,
        repo=pr.repo,
        org_url=pr.org,
        project=pr.project,
        require_auto_review=True,
    )
    if link is None:
        return None

    existing = await storage.get_readiness_candidate(
        platform=pr.platform.value,
        repo=pr.repo,
        org_url=pr.org,
        project=pr.project,
        pr_id=pr.pr_id,
        head_sha=pr.head_commit_sha,
    )
    # A terminal candidate for this exact SHA is already done — return it without
    # re-posting a pending status. This is harmless on the webhook path (a rare
    # re-delivery) but essential for the poll fallback, which would otherwise
    # flip every reviewed PR's readiness check back to "pending" on each pass.
    if existing is not None and existing["state"] in TERMINAL_CANDIDATE_STATES:
        return existing
    is_new = existing is None
    if is_new:
        older = await storage.list_active_readiness_candidates(
            platform=pr.platform.value,
            repo=pr.repo,
            pr_id=pr.pr_id,
            states=("waiting", "blocked", "error", "reviewing"),
        )
        for candidate in older:
            if candidate["head_sha"] != pr.head_commit_sha:
                await storage.record_candidate_transition(
                    uuid.UUID(candidate["id"]),
                    to_state="superseded",
                    source=source,
                    actor=pr.platform.value,
                    reason="new_commit",
                    readiness_snapshot=candidate.get("readiness_snapshot") or {},
                )
        existing = await storage.create_readiness_candidate(
            repo_link_id=uuid.UUID(link["id"]),
            pr_id=pr.pr_id,
            head_sha=pr.head_commit_sha,
            pr_url=pr.pr_url,
            readiness_snapshot={"created_source": source},
        )

    assert existing is not None
    adapter = adapter or await _adapter_for_candidate(existing)
    if is_new:
        # Show a neutral pending status immediately on first sight, before the
        # (possibly slow) evaluation runs. For an existing candidate we skip
        # straight to evaluate_candidate — it writes the real status and dedupes
        # it, so re-posting here would be redundant spam that counts against
        # GitHub's 1000-statuses-per-context-per-SHA cap on every poll pass.
        await _post_readiness_status(adapter, pr, "pending", "Guardian readiness waiting")
        await _post_review_pending(adapter, pr)
        # Post initial sticky guidance comment (no review deeplink yet)
        await upsert_guidance_comment(adapter, pr, "pending", storage=storage)
    return await evaluate_candidate(
        uuid.UUID(existing["id"]),
        source=source,
        adapter=adapter,
        pr=pr,
        base_url=base_url,
        remint=remint,
    )


async def supersede_candidates_for_pr(
    pr: PlatformPR, *, source: str = "webhook", reason: str = "pr_closed"
) -> int:
    candidates = await storage.list_active_readiness_candidates(
        platform=pr.platform.value,
        repo=pr.repo,
        pr_id=pr.pr_id,
        states=("waiting", "blocked", "error", "reviewing"),
    )
    for candidate in candidates:
        await storage.record_candidate_transition(
            uuid.UUID(candidate["id"]),
            to_state="superseded",
            source=source,
            actor=pr.platform.value,
            reason=reason,
            readiness_snapshot=candidate.get("readiness_snapshot") or {},
        )
    return len(candidates)


async def evaluate_candidates_for_sha(
    *,
    platform: str,
    repo: str,
    head_sha: str,
    source: str,
    base_url: str = "",
) -> list[dict[str, Any]]:
    candidates = await storage.list_active_readiness_candidates(
        platform=platform,
        repo=repo,
        head_sha=head_sha,
        states=("waiting", "blocked", "error"),
    )
    evaluated: list[dict[str, Any]] = []
    for candidate in candidates:
        evaluated.append(
            await evaluate_candidate(uuid.UUID(candidate["id"]), source=source, base_url=base_url)
        )
    return evaluated


async def reassert_reviewed_readiness(candidate: dict[str, Any]) -> bool:
    """Re-post guardian/readiness=success for a completed review whose check is stranded.

    A finished review re-asserts readiness=success, but that write is best-effort:
    when it fails the candidate is already terminal (`reviewed`) and never
    re-evaluated, so the readiness check stays pending forever. The reconciler
    calls this for unsynced reviewed candidates. It is idempotent (success→success
    is a no-op on the platform) and one-shot: a confirmed write flips
    `readiness_synced`, so the candidate drops out of the reconciler scan. A failed
    write leaves the flag unset to retry on the next tick.
    """
    candidate_id = uuid.UUID(candidate["id"])
    try:
        adapter = await _adapter_for_candidate(candidate)
        pr = _candidate_pr(candidate)
    except Exception as exc:
        log.warning(
            "readiness_reassert_adapter_failed",
            candidate_id=candidate["id"],
            error=repr(exc),
            error_type=type(exc).__name__,
        )
        return False
    written = await _post_readiness_status(
        adapter, pr, "success", "Guardian readiness: review_completed"
    )
    if written:
        await storage.mark_readiness_synced(candidate_id)
    return written


async def evaluate_candidate(
    candidate_id: uuid.UUID,
    *,
    source: str = "reconciler",
    adapter: PlatformAdapter | None = None,
    pr: PlatformPR | None = None,
    start_review: bool = True,
    base_url: str = "",
    remint: bool = True,
) -> dict[str, Any]:
    candidate = await storage.get_readiness_candidate_by_id(candidate_id)
    if candidate is None:
        raise LookupError(f"Readiness candidate not found: {candidate_id}")
    if candidate["state"] in TERMINAL_CANDIDATE_STATES:
        return candidate
    if candidate["state"] in ACTIVE_CANDIDATE_STATES:
        if not _candidate_reviewing_stale(candidate, _now()):
            return candidate
        recovered = await storage.recover_stale_reviewing_candidate(
            candidate_id,
            source=source,
            actor="reconciler" if source == "reconciler" else source,
            stale_after_minutes=DEFAULT_REVIEWING_STALE_MINUTES,
        )
        if recovered is None:
            raise LookupError(
                f"Readiness candidate not found after stale recovery: {candidate_id}"
            )
        candidate = recovered

    link = await storage.get_repo_link(uuid.UUID(candidate["repo_link_id"]))
    profile = (
        await storage.get_profile(uuid.UUID(link["profile_id"]))
        if link and link.get("profile_id")
        else None
    )
    connection = (
        await storage.get_connection(uuid.UUID(link["connection_id"]))
        if link and link.get("connection_id")
        else None
    )
    pr = pr or _candidate_pr(candidate)
    adapter = adapter or await _adapter_for_candidate(candidate)
    assert pr is not None
    assert adapter is not None

    decision = await evaluate_readiness(
        candidate,
        link=link,
        profile=profile,
        connection=connection,
        adapter=adapter,
        pr=pr,
        now=_now(),
    )
    # Only a genuine "blocked" decision (e.g. checks_failed) posts a failing
    # readiness status. An "error" decision is Guardian's own problem to reach
    # the platform (platform_error / status_write_failed) — surfacing it as a
    # red X on every PR is noise, and the candidate is recoverable, so the
    # reconciler retries and flips it to success once the platform recovers.
    # Posting "pending" keeps the PR neutral while we self-heal.
    status_state = (
        "success" if decision.ready else "failure" if decision.state == "blocked" else "pending"
    )
    status_description = f"Guardian readiness: {decision.reason or decision.state}"
    # Posting a commit status itself fires a GitHub `status` webhook, which
    # re-triggers readiness evaluation. Re-posting an *unchanged* status is
    # therefore a self-amplifying loop that burns through GitHub's hard cap of
    # 1000 statuses per context per SHA — past which every write 422s and the
    # candidate gets stranded in "error". Only write when the (sha, state,
    # description) actually changed from the last status we successfully wrote.
    prev_write = (candidate.get("readiness_snapshot") or {}).get("status_write") or {}
    status_unchanged = (
        prev_write.get("ok") is True
        and prev_write.get("sha") == pr.head_commit_sha
        and prev_write.get("state") == status_state
        and prev_write.get("description") == status_description
    )
    if status_unchanged:
        status_written = True
        decision.snapshot["status_write"] = prev_write
    else:
        status_written = await _post_readiness_status(
            adapter, pr, status_state, status_description
        )
        if status_written:
            decision.snapshot["status_write"] = {
                "sha": pr.head_commit_sha,
                "state": status_state,
                "description": status_description,
                "ok": True,
            }
    if not status_written and decision.state != "superseded":
        augmented = {**decision.snapshot, "status_write": {"state": status_state, "ok": False}}
        if decision.ready:
            # Commit status write failed (e.g. token missing repo:status scope) but the
            # PR is ready to review — proceed with the review rather than silently killing
            # the pipeline. The status check is informational; the review is the product.
            decision = ReadinessDecision("reviewing", decision.reason, augmented)
        else:
            decision = ReadinessDecision("error", "status_write_failed", augmented)
    if decision.ready and start_review:
        carried = await _try_carry_forward_auto_approve(
            candidate_id, pr, adapter, source, decision, base_url=base_url
        )
        if carried is not None:
            return carried
        started = await _start_automatic_review(
            candidate_id, pr, adapter, source, decision, base_url=base_url
        )
        if started is not None:
            return started
        updated = await storage.get_readiness_candidate_by_id(candidate_id)
        if updated is None:
            raise LookupError(f"Readiness candidate not found after handoff: {candidate_id}")
        return updated

    await storage.record_candidate_transition(
        candidate_id,
        to_state=decision.state,
        source=source,
        actor=pr.platform.value,
        reason=decision.reason,
        readiness_snapshot=decision.snapshot,
    )
    if remint and decision.state == "superseded" and decision.reason == "new_commit":
        await _remint_for_live_head(pr, decision, source=source, base_url=base_url)
    updated = await storage.get_readiness_candidate_by_id(candidate_id)
    if updated is None:
        raise LookupError(f"Readiness candidate not found after update: {candidate_id}")
    return updated


async def _remint_for_live_head(
    stale_pr: PlatformPR,
    decision: ReadinessDecision,
    *,
    source: str,
    base_url: str,
) -> None:
    """Mint a candidate for the PR's *current* head after a new_commit supersede.

    Candidates are keyed per head SHA. The reconciler and the check/workflow
    webhooks can only re-drive candidates that already exist — they never create
    one for a head that has moved on. A candidate for the new head is minted only
    by the pull_request webhook, manual Sync, or the pr-sync poll (every 5 min in
    work hours, 10 min off-hours). When that webhook is missed, the live head
    strands with no candidate driving it until the slow poll catches up: the old
    candidate self-supersedes here as new_commit and nothing replaces it. So
    re-mint for the live head right now, collapsing recovery to the next 30s
    reconcile tick. remint=False bounds this to a single hop (no recursion if the
    re-minted head is itself already stale).
    """
    live_head = str((decision.snapshot.get("metadata") or {}).get("head_sha") or "")
    if not live_head or live_head == stale_pr.head_commit_sha:
        return
    live_pr = replace(stale_pr, head_commit_sha=live_head)
    try:
        await create_or_update_candidate_from_pr(
            live_pr, source=source, base_url=base_url, remint=False
        )
    except Exception as exc:
        log.warning(
            "readiness_remint_failed",
            repo=stale_pr.repo,
            pr_id=stale_pr.pr_id,
            head_sha=live_head,
            error=repr(exc),
            error_type=type(exc).__name__,
        )


async def _try_carry_forward_auto_approve(
    candidate_id: uuid.UUID,
    pr: PlatformPR,
    adapter: PlatformAdapter,
    source: str,
    decision: ReadinessDecision,
    *,
    base_url: str = "",
) -> dict[str, Any] | None:
    """Carry a prior auto-approve forward when the live head's net diff is unchanged.

    A pure "Update branch" base-merge produces a new head SHA whose three-dot diff
    vs base is byte-identical to a SHA Guardian already auto-approved — no new
    reviewable content. Re-gating from scratch defaults to needs-human-review and,
    under ``strict`` branch protection, creates an update-branch → re-review
    treadmill (issue #97). When the live diff's identity hash matches the most
    recent completed review *and that review auto-approved*, post
    ``guardian/review=success`` to the live head and mark the candidate reviewed,
    skipping the agents. Returns the updated candidate on carry-forward, else
    ``None`` (caller then runs the normal review).

    Scope is deliberately auto-approve only: a prior human/security clearance or a
    block still re-reviews. The diff is fetched only when a matching auto-approved
    review exists, so first-time reviews pay no extra platform call.
    """
    try:
        prior = await storage.find_latest_review_for_pr(pr.platform.value, pr.repo, pr.pr_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("carry_forward_lookup_failed", pr_id=pr.pr_id, error=repr(exc))
        return None
    if prior is None or prior.get("decision") != Decision.AUTO_APPROVE.value:
        return None
    prior_hash = prior.get("diff_identity_hash") or ""
    if not prior_hash:
        return None

    try:
        diff = await adapter.fetch_diff(pr)
    except Exception as exc:  # noqa: BLE001
        # Can't confirm identity — fall back to a full review rather than
        # carrying a verdict forward on unverified content.
        log.warning("carry_forward_diff_fetch_failed", pr_id=pr.pr_id, error=repr(exc))
        return None
    if diff.identity_hash != prior_hash:
        return None

    target_url = build_review_detail_url(prior["id"], base_url) or ""
    posted = await _post_review_status(
        adapter,
        pr,
        "success",
        "Guardian cleared (unchanged diff carried forward)",
        target_url,
    )
    if not posted:
        # The green check is the whole point — if it didn't land, don't mark the
        # candidate reviewed. Let the reconciler retry (or a full review run).
        return None

    # Advance the sticky guidance comment to cleared so it doesn't strand at the
    # "pending" note this candidate just posted while guardian/review is green.
    await upsert_guidance_comment(adapter, pr, "success", review_url=target_url, storage=storage)

    snapshot = {
        **decision.snapshot,
        "carried_forward": {
            "prior_review_id": prior["id"],
            "prior_head_sha": prior.get("head_commit_sha"),
            "diff_identity_hash": prior_hash,
            "reason": "base_merge_unchanged_diff",
        },
    }
    await storage.record_candidate_transition(
        candidate_id,
        to_state="reviewed",
        source=source,
        actor="guardian",
        reason="carried_forward_base_merge",
        readiness_snapshot=snapshot,
    )
    log.info(
        "review_carried_forward",
        pr_id=pr.pr_id,
        repo=pr.repo,
        head_sha=pr.head_commit_sha,
        prior_review_id=prior["id"],
        prior_head_sha=prior.get("head_commit_sha"),
    )
    return await storage.get_readiness_candidate_by_id(candidate_id)


async def _start_automatic_review(
    candidate_id: uuid.UUID,
    pr: PlatformPR,
    adapter: PlatformAdapter,
    source: str,
    decision: ReadinessDecision,
    *,
    base_url: str = "",
) -> dict[str, Any] | None:
    started = await storage.try_start_candidate_review(
        candidate_id,
        pr,
        source=source,
        actor=source,
        reason=decision.reason,
        readiness_snapshot=decision.snapshot,
        comment_mode="inline",
        review_source="automatic",
    )
    if started is None:
        return None
    review_id, candidate = started

    async def _run() -> None:
        try:
            from pr_guardian.config.profile_resolver import resolve_profile_snapshot_config
            from pr_guardian.core.orchestrator import run_review

            resolved = await resolve_profile_snapshot_config(
                candidate.get("profile_snapshot"),
                candidate.get("connection_snapshot"),
            )
            await run_review(
                pr,
                adapter,
                service_config=resolved.config,
                existing_review_db_id=review_id,
                comment_mode="inline",
                manual_comment_override=True,
                base_url=base_url,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                await storage.mark_review_failed(
                    review_id,
                    f"Automatic review startup failed: {exc}",
                )
            except Exception as mark_exc:  # noqa: BLE001
                log.warning(
                    "automatic_candidate_review_mark_failed",
                    candidate_id=str(candidate_id),
                    review_id=str(review_id),
                    error=str(mark_exc),
                )
            await _post_review_status(
                adapter,
                pr,
                "failure",
                "Guardian review failed before starting",
            )
            log.error(
                "automatic_candidate_review_failed",
                candidate_id=str(candidate_id),
                review_id=str(review_id),
                error=str(exc),
            )

    asyncio.create_task(_run())
    return candidate


async def evaluate_readiness(
    candidate: dict[str, Any],
    *,
    link: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    connection: dict[str, Any] | None,
    adapter: PlatformAdapter,
    pr: PlatformPR,
    now: datetime,
) -> ReadinessDecision:
    previous_snapshot = candidate.get("readiness_snapshot") or {}
    snapshot: dict[str, Any] = {
        "evaluated_at": now.isoformat(),
        "candidate_id": candidate["id"],
        "head_sha": candidate["head_sha"],
    }

    if (
        link is None
        or link.get("archived_at")
        or link.get("paused")
        or not link.get("auto_review_enabled")
    ):
        snapshot["repo_link"] = {"active": False}
        return ReadinessDecision("blocked", "repo_link_disabled", snapshot)
    if profile is None or profile.get("archived_at"):
        snapshot["profile"] = {"active": False}
        return ReadinessDecision("error", "profile_unavailable", snapshot)
    if (
        connection is None
        or connection.get("archived_at")
        or connection.get("health_status") != "healthy"
    ):
        snapshot["connection"] = {
            "active": False,
            "health_status": (connection or {}).get("health_status"),
        }
        return ReadinessDecision("error", "connection_unavailable", snapshot)

    settings = _readiness_settings(profile)
    snapshot["settings"] = {
        "quiet_period_seconds": settings["quiet_period_seconds"],
        "max_wait_minutes": settings["max_wait_minutes"],
        "archmap_max_wait_minutes": settings["archmap_max_wait_minutes"],
        "archmap_expected": settings["archmap_expected"],
    }

    try:
        metadata = await adapter.fetch_pr_metadata(pr)
        pr = PlatformPR(
            platform=pr.platform,
            pr_id=pr.pr_id,
            repo=pr.repo,
            repo_url=pr.repo_url,
            source_branch=pr.source_branch,
            target_branch=pr.target_branch,
            author=pr.author,
            title=pr.title,
            head_commit_sha=metadata.head_sha or pr.head_commit_sha,
            body=pr.body,
            org=pr.org,
            project=pr.project,
            install_id=pr.install_id,
        )
        snapshot["metadata"] = asdict(metadata)
        if metadata.closed:
            return ReadinessDecision(
                "superseded", "pr_merged" if metadata.merged else "pr_closed", snapshot
            )
        if metadata.head_sha and metadata.head_sha != candidate["head_sha"]:
            return ReadinessDecision("superseded", "new_commit", snapshot)
        if metadata.draft:
            snapshot["draft"] = {"max_wait_accrues": False}
            return ReadinessDecision("waiting", "draft", snapshot)
        if metadata.fork:
            return ReadinessDecision("blocked", "fork_requires_manual_start", snapshot)

        readiness_started = _parse_dt(previous_snapshot.get("readiness_started_at")) or now
        snapshot["readiness_started_at"] = readiness_started.isoformat()
        if now < readiness_started + timedelta(seconds=settings["quiet_period_seconds"]):
            snapshot["quiet_period"] = {
                "satisfied": False,
                "seconds": settings["quiet_period_seconds"],
            }
            return ReadinessDecision("waiting", "quiet_period", snapshot)

        signals = _filtered_signals(await adapter.fetch_readiness_signals(pr), settings)
    except Exception as exc:
        status = _exc_http_status(exc)
        # 401/403/404 are auth/access/not-found: the credential can't see this repo
        # or PR. That won't self-heal by retrying — it needs an operator to fix the
        # connection's access, so it gets a distinct, *visible* reason. Everything
        # else (5xx, 429, timeouts, network) is a transient Guardian-side blip that
        # the reconciler quietly retries, and stays hidden as before.
        persistent = status in (401, 403, 404)
        reason = "platform_access_error" if persistent else "platform_error"
        # str(exc) is empty for many exception types (bare raises, some httpx
        # transport errors). Record the repr and type so the real cause is
        # recoverable from the snapshot and logs instead of an empty string.
        snapshot["error"] = repr(exc)
        snapshot["error_type"] = type(exc).__name__
        if status is not None:
            snapshot["error_status"] = status
        log.warning(
            "readiness_platform_error",
            candidate_id=candidate["id"],
            repo=pr.repo,
            pr_id=pr.pr_id,
            connection_id=candidate.get("connection_id"),
            status=status,
            reason=reason,
            error=repr(exc),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return ReadinessDecision("error", reason, snapshot)

    checks = _checks_snapshot(signals)
    snapshot["checks"] = checks
    if checks["failed"]:
        return ReadinessDecision("blocked", "checks_failed", snapshot)
    max_wait_elapsed = now >= readiness_started + timedelta(minutes=settings["max_wait_minutes"])
    if checks["pending"]:
        snapshot["checks"]["max_wait_elapsed"] = max_wait_elapsed
        if max_wait_elapsed:
            return ReadinessDecision("blocked", "checks_timeout", snapshot)
        return ReadinessDecision("waiting", "checks_pending", snapshot)

    if settings["archmap_expected"]:
        first_archmap_wait = _parse_dt(previous_snapshot.get("archmap_wait_started_at")) or now
        snapshot["archmap_wait_started_at"] = first_archmap_wait.isoformat()
        try:
            found = await adapter.find_archmap_artifact(pr, candidate["head_sha"])
        except Exception as exc:
            status = _exc_http_status(exc)
            # A persistent 401/403/404 means the credential can't read the Actions
            # artifacts API (most often the GitHub App is missing `Actions: Read`).
            # That never self-heals by waiting, so surface it as a visible access
            # error instead of silently sitting in archmap_wait until the soft
            # timeout — the same treatment the PR-metadata fetch above gets.
            if status in (401, 403, 404):
                snapshot["archmap"] = {"found": False, "error": repr(exc), "error_status": status}
                snapshot["error"] = repr(exc)
                snapshot["error_type"] = type(exc).__name__
                snapshot["error_status"] = status
                log.warning(
                    "readiness_archmap_access_error",
                    candidate_id=candidate["id"],
                    repo=pr.repo,
                    pr_id=pr.pr_id,
                    connection_id=candidate.get("connection_id"),
                    status=status,
                    error=repr(exc),
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )
                return ReadinessDecision("error", "platform_access_error", snapshot)
            # Transient blip (5xx/429/timeout/network): Archmap is best-effort, so
            # keep waiting and let the reconciler retry — the soft timeout still
            # applies, so a flaky artifacts API can't strand the candidate forever.
            snapshot["archmap"] = {"found": False, "error": repr(exc)}
            found = False
        if not found:
            deadline = first_archmap_wait + timedelta(minutes=settings["archmap_max_wait_minutes"])
            if now < deadline:
                snapshot["archmap"] = {"found": False, "waiting": True}
                return ReadinessDecision("waiting", "archmap_wait", snapshot)
            snapshot["archmap"] = {
                "found": False,
                "waiting": False,
                "warning": "archmap_timeout",
            }
        else:
            snapshot["archmap"] = {"found": True}

    return ReadinessDecision("reviewing", "ready", snapshot)
