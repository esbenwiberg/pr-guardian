from __future__ import annotations

import uuid
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.factory import create_adapter
from pr_guardian.platform.protocol import PlatformAdapter, PlatformReadinessSignal

log = structlog.get_logger()

DEFAULT_QUIET_PERIOD_SECONDS = 10
DEFAULT_MAX_WAIT_MINUTES = 30
DEFAULT_ARCHMAP_MAX_WAIT_MINUTES = 10

TERMINAL_CANDIDATE_STATES = {"reviewing", "reviewed", "superseded"}
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


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    connection_id = candidate.get("connection_id")
    if not connection_id:
        return create_adapter(candidate["platform"])
    connection = await storage.get_connection(uuid.UUID(connection_id))
    token = await storage.get_connection_token(uuid.UUID(connection_id))
    return create_adapter(
        candidate["platform"],
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
    try:
        method = getattr(adapter, "set_review_status", None)
        if method is not None:
            await method(pr, "pending", "Guardian review pending")
        else:
            await adapter.set_status(pr, "pending", "Guardian review pending", context="guardian/review")
    except Exception as exc:
        log.warning("review_pending_status_write_failed", pr_id=pr.pr_id, error=str(exc))


async def create_or_update_candidate_from_pr(
    pr: PlatformPR,
    *,
    source: str = "webhook",
    adapter: PlatformAdapter | None = None,
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

    adapter = adapter or await _adapter_for_candidate(existing)
    await _post_readiness_status(adapter, pr, "pending", "Guardian readiness waiting")
    if is_new:
        await _post_review_pending(adapter, pr)
    return await evaluate_candidate(
        uuid.UUID(existing["id"]), source=source, adapter=adapter, pr=pr
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
) -> list[dict[str, Any]]:
    candidates = await storage.list_active_readiness_candidates(
        platform=platform,
        repo=repo,
        head_sha=head_sha,
        states=("waiting", "blocked"),
    )
    evaluated: list[dict[str, Any]] = []
    for candidate in candidates:
        evaluated.append(await evaluate_candidate(uuid.UUID(candidate["id"]), source=source))
    return evaluated


async def evaluate_candidate(
    candidate_id: uuid.UUID,
    *,
    source: str = "reconciler",
    adapter: PlatformAdapter | None = None,
    pr: PlatformPR | None = None,
    start_review: bool = True,
) -> dict[str, Any]:
    candidate = await storage.get_readiness_candidate_by_id(candidate_id)
    if candidate is None:
        raise LookupError(f"Readiness candidate not found: {candidate_id}")
    if candidate["state"] in TERMINAL_CANDIDATE_STATES:
        return candidate

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
    status_state = (
        "success"
        if decision.ready
        else "failure"
        if decision.state in {"blocked", "error"}
        else "pending"
    )
    status_written = await _post_readiness_status(
        adapter,
        pr,
        status_state,
        f"Guardian readiness: {decision.reason or decision.state}",
    )
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
        started = await _start_automatic_review(candidate_id, pr, adapter, source, decision)
        if started is not None:
            return started
        updated = await storage.get_readiness_candidate_by_id(candidate_id)
        if updated is None:
            raise LookupError(f"Readiness candidate not found after handoff: {candidate_id}")
        return updated

    if candidate["state"] != decision.state or candidate.get("reason") != decision.reason:
        await storage.record_candidate_transition(
            candidate_id,
            to_state=decision.state,
            source=source,
            actor=pr.platform.value,
            reason=decision.reason,
            readiness_snapshot=decision.snapshot,
        )
    else:
        await storage.record_candidate_transition(
            candidate_id,
            to_state=decision.state,
            source=source,
            actor=pr.platform.value,
            reason=decision.reason,
            readiness_snapshot=decision.snapshot,
        )
    updated = await storage.get_readiness_candidate_by_id(candidate_id)
    if updated is None:
        raise LookupError(f"Readiness candidate not found after update: {candidate_id}")
    return updated


async def _start_automatic_review(
    candidate_id: uuid.UUID,
    pr: PlatformPR,
    adapter: PlatformAdapter,
    source: str,
    decision: ReadinessDecision,
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
            )
        except Exception as exc:  # noqa: BLE001
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
        snapshot["error"] = str(exc)
        return ReadinessDecision("error", "platform_error", snapshot)

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
            snapshot["archmap"] = {"found": False, "error": str(exc)}
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
