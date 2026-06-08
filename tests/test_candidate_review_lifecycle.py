from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pr_guardian.api import reviews_queue
from pr_guardian.auth.identity import Identity
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.readiness import (
    ReadinessDecision,
    _start_automatic_review,
    supersede_candidates_for_pr,
)
from pr_guardian.core.orchestrator import _is_stale_automatic_review
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.protocol import PlatformPRMetadata
from tests.test_readiness_storage import _make_session_factory


def _pr(head_sha: str = "sha1", *, repo: str = "octo/service") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo=repo,
        repo_url=f"https://github.com/{repo}/pull/42",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="Feature",
        head_commit_sha=head_sha,
        org="octo",
    )


async def _linked_candidate(*, state: str = "waiting", reason: str = "") -> dict:
    suffix = uuid.uuid4().hex[:8]
    profile = await storage.create_profile(
        f"Readiness {suffix}",
        settings={"readiness": {"quiet_period_seconds": 0}, "side_effects": {}},
    )
    connection = await storage.create_connection(
        f"Connection {suffix}",
        platform="github",
        token="fixture-token",
        health_status="healthy",
    )
    link = await storage.create_repo_link(
        platform="github",
        repo_owner="octo",
        repo_name=f"service-{suffix}",
        profile_id=uuid.UUID(profile["id"]),
        connection_id=uuid.UUID(connection["id"]),
        auto_review_enabled=True,
    )
    return await storage.create_readiness_candidate(
        repo_link_id=uuid.UUID(link["id"]),
        pr_id="42",
        head_sha="sha1",
        pr_url=f"https://github.com/octo/service-{suffix}/pull/42",
        state=state,
        reason=reason,
        readiness_snapshot={"checks": {"total": 1, "passed": 1}},
    )


async def test_ready_candidate_transitions_to_one_review_under_concurrency():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            candidate = await _linked_candidate()

            results = await asyncio.gather(
                storage.try_start_candidate_review(
                    uuid.UUID(candidate["id"]),
                    _pr(),
                    source="automatic",
                    actor="webhook",
                    reason="ready",
                    readiness_snapshot={"ready": True},
                ),
                storage.try_start_candidate_review(
                    uuid.UUID(candidate["id"]),
                    _pr(),
                    source="automatic",
                    actor="reconciler",
                    reason="ready",
                    readiness_snapshot={"ready": True},
                ),
            )

            winners = [result for result in results if result is not None]
            assert len(winners) == 1
            review_id, updated = winners[0]
            assert updated["state"] == "reviewing"
            review = await storage.get_review(review_id)
            assert review is not None
            assert review["candidate_id"] == candidate["id"]
            assert review["profile_id"] == candidate["profile_id"]
            assert review["connection_id"] == candidate["connection_id"]
            assert review["repo_link_id"] == candidate["repo_link_id"]
            assert review["profile_snapshot"]["name"].startswith("Readiness")
            assert review["connection_snapshot"]["name"].startswith("Connection")
    finally:
        await engine.dispose()


async def test_automatic_review_runs_inline_with_guardian_base_url():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            candidate = await _linked_candidate()
            adapter = AsyncMock()
            run_review = AsyncMock()

            with patch("pr_guardian.core.orchestrator.run_review", run_review):
                with patch(
                    "pr_guardian.config.profile_resolver.resolve_profile_snapshot_config",
                    AsyncMock(return_value=SimpleNamespace(config=GuardianConfig())),
                ):
                    started = await _start_automatic_review(
                        uuid.UUID(candidate["id"]),
                        _pr(repo=candidate["repo"]),
                        adapter,
                        "webhook",
                        ReadinessDecision("reviewing", "ready", {"ready": True}),
                        base_url="https://guardian.example",
                    )
                    assert started is not None
                    for _ in range(20):
                        if run_review.await_count:
                            break
                        await asyncio.sleep(0.01)

            run_review.assert_awaited_once()
            kwargs = run_review.await_args.kwargs
            assert kwargs["comment_mode"] == "inline"
            assert kwargs["manual_comment_override"] is True
            assert kwargs["base_url"] == "https://guardian.example"
    finally:
        await engine.dispose()


async def test_stale_automatic_review_skips_platform_side_effects():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            candidate = await _linked_candidate()
            started = await storage.try_start_candidate_review(
                uuid.UUID(candidate["id"]),
                _pr("sha1"),
                source="automatic",
                actor="webhook",
                reason="ready",
                readiness_snapshot={"ready": True},
            )
            assert started is not None
            review_id, _ = started
            adapter = AsyncMock()
            adapter.fetch_pr_metadata = AsyncMock(return_value=PlatformPRMetadata(head_sha="sha2"))

            stale = await _is_stale_automatic_review(
                adapter,
                _pr("sha1"),
                storage=storage,
                review_id=review_id,
            )

            assert stale is True
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "superseded"
            assert updated["reason"] == "new_commit"
    finally:
        await engine.dispose()


async def test_reviewing_candidate_superseded_on_close_or_new_commit():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            candidate = await _linked_candidate(state="reviewing", reason="ready")

            count = await supersede_candidates_for_pr(
                _pr("sha1", repo=candidate["repo"]),
                source="webhook",
                reason="pr_closed",
            )

            assert count == 1
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "superseded"
            assert updated["reason"] == "pr_closed"
    finally:
        await engine.dispose()


async def test_manual_bypass_and_manager_override_have_distinct_readiness_audit(monkeypatch):
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            bypass_candidate = await _linked_candidate(state="blocked", reason="checks_failed")
            override_candidate = await _linked_candidate(state="blocked", reason="checks_timeout")
            adapter = AsyncMock()
            adapter.set_readiness_status = AsyncMock()
            monkeypatch.setattr(
                reviews_queue, "_adapter_from_candidate", AsyncMock(return_value=adapter)
            )
            monkeypatch.setattr(reviews_queue, "_run_candidate_review", AsyncMock())

            user = Identity(kind="user", email="reviewer@example.test")
            bypass = await reviews_queue.start_candidate_review_now(
                uuid.UUID(bypass_candidate["id"]),
                reviews_queue.ManualBypassRequest(comment_mode="summary"),
                identity=user,
            )

            assert bypass["source"] == "manual_bypass"
            assert bypass["readiness_marked_success"] is False
            claimed = await storage.get_readiness_candidate_by_id(
                uuid.UUID(bypass_candidate["id"])
            )
            assert claimed is not None
            assert claimed["state"] == "reviewing"
            assert claimed["reason"] == "manual_bypass"
            review = await storage.get_review(uuid.UUID(bypass["review_id"]))
            assert review is not None
            assert review["review_source"] == "manual_bypass"

            manager = Identity(
                kind="user",
                email="manager@example.test",
                can_manage_profiles=True,
            )
            override = await reviews_queue.override_candidate_readiness(
                uuid.UUID(override_candidate["id"]),
                reviews_queue.OverrideReadinessRequest(
                    reason="CI outage acknowledged",
                    confirm=True,
                    comment_mode="summary",
                ),
                identity=manager,
            )

            assert override["source"] == "override"
            assert override["readiness_marked_success"] is True
            adapter.set_readiness_status.assert_awaited_once()
            updated = await storage.get_readiness_candidate_by_id(
                uuid.UUID(override_candidate["id"])
            )
            assert updated is not None
            assert updated["state"] == "reviewing"
            assert updated["reason"] == "manual_override"
            audits = await storage.list_profile_audit_events(
                target_type="readiness_candidate",
                target_id=uuid.UUID(override_candidate["id"]),
            )
            assert audits
            assert audits[-1]["action"] == "readiness.override"
            assert audits[-1]["actor"] == "manager@example.test"
            assert audits[-1]["before"]["state"] == "blocked"
            assert audits[-1]["after"]["reason"] == "CI outage acknowledged"
    finally:
        await engine.dispose()


async def test_manual_candidate_review_failure_is_marked_and_not_recovered(monkeypatch):
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            candidate = await _linked_candidate(state="blocked", reason="checks_failed")
            started = await storage.try_start_candidate_review(
                uuid.UUID(candidate["id"]),
                _pr(repo=candidate["repo"]),
                source="manual_bypass",
                actor="reviewer@example.test",
                reason="manual_bypass",
                readiness_snapshot={"manual": True},
                review_source="manual_bypass",
            )
            assert started is not None
            review_id, claimed = started
            monkeypatch.setattr(
                "pr_guardian.config.profile_resolver.resolve_profile_snapshot_config",
                AsyncMock(return_value=SimpleNamespace(config=GuardianConfig())),
            )
            monkeypatch.setattr(
                "pr_guardian.core.orchestrator.run_review",
                AsyncMock(side_effect=RuntimeError("provider unavailable")),
            )

            await reviews_queue._run_candidate_review(
                claimed,
                review_id,
                AsyncMock(),
                comment_mode="summary",
                manual_comment_override=True,
            )

            review = await storage.get_review(review_id)
            assert review is not None
            assert review["stage"] == "error"
            assert review["decision"] == "error"
            assert "Candidate review failed" in review["stage_detail"]
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "error"
            assert updated["reason"] == "review_failed"
            recoverable = await storage.list_recoverable_readiness_candidates()
            assert candidate["id"] not in {c["id"] for c in recoverable}
    finally:
        await engine.dispose()
