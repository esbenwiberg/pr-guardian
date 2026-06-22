from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from pr_guardian.core.readiness import (
    create_or_update_candidate_from_pr,
    evaluate_candidate,
    evaluate_candidates_for_sha,
)
from pr_guardian.api.reviews_queue import _candidate_visible
from pr_guardian.core.readiness_reconciler import reconcile_readiness_once
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.protocol import PlatformPRMetadata, PlatformReadinessSignal
from tests.test_readiness_storage import _make_session_factory


@pytest.fixture(autouse=True)
def _stub_background_review(monkeypatch):
    """These tests assert readiness candidate *state transitions*, not the review
    pipeline. The 'ready' path fires a fire-and-forget ``run_review`` background
    task; with a FakeReadinessAdapter that has no ``fetch_diff`` it errors, and
    worse, races the shared in-memory SQLite connection. Stub it to a no-op so the
    readiness engine is tested in isolation."""
    monkeypatch.setattr("pr_guardian.core.orchestrator.run_review", AsyncMock())


class FakeReadinessAdapter:
    def __init__(
        self,
        *,
        metadata: PlatformPRMetadata | None = None,
        signals: list[PlatformReadinessSignal] | None = None,
        metadata_error: Exception | None = None,
        archmap_found: bool = True,
    ):
        self.metadata = metadata or PlatformPRMetadata(head_sha="sha1")
        self.signals = signals or []
        self.metadata_error = metadata_error
        self.archmap_found = archmap_found
        self.statuses: list[tuple[str, str, str]] = []

    async def fetch_pr_metadata(self, pr: PlatformPR) -> PlatformPRMetadata:
        if self.metadata_error:
            raise self.metadata_error
        return self.metadata

    async def fetch_readiness_signals(self, pr: PlatformPR) -> list[PlatformReadinessSignal]:
        return self.signals

    async def set_readiness_status(self, pr: PlatformPR, state: str, description: str) -> None:
        self.statuses.append(("guardian/readiness", state, description))

    async def set_review_status(self, pr: PlatformPR, state: str, description: str) -> None:
        self.statuses.append(("guardian/review", state, description))

    async def find_archmap_artifact(self, pr: PlatformPR, head_sha: str) -> bool:
        return self.archmap_found


def _pr(head_sha: str = "sha1", *, platform: Platform = Platform.GITHUB) -> PlatformPR:
    if platform == Platform.ADO:
        return PlatformPR(
            platform=platform,
            pr_id="42",
            repo="service",
            repo_url="https://dev.azure.com/acme/Proj/_git/service",
            source_branch="feature",
            target_branch="main",
            author="alice",
            title="Feature",
            head_commit_sha=head_sha,
            org="https://dev.azure.com/acme",
            project="Proj",
        )
    return PlatformPR(
        platform=platform,
        pr_id="42",
        repo="octo/service",
        repo_url="https://github.com/octo/service",
        source_branch="feature",
        target_branch="main",
        author="alice",
        title="Feature",
        head_commit_sha=head_sha,
        org="octo",
    )


async def _linked_repo(settings: dict | None = None, *, platform: Platform = Platform.GITHUB):
    profile = await storage.create_profile(
        "Readiness", settings=settings or {"readiness": {"quiet_period_seconds": 0}}
    )
    connection = await storage.create_connection(
        "Connection",
        platform=platform.value,
        token="fixture-token",
        org_url="https://dev.azure.com/acme" if platform == Platform.ADO else None,
        health_status="healthy",
    )
    if platform == Platform.ADO:
        link = await storage.create_repo_link(
            platform="ado",
            org_url="https://dev.azure.com/acme",
            project="Proj",
            repo_name="service",
            profile_id=uuid.UUID(profile["id"]),
            connection_id=uuid.UUID(connection["id"]),
            auto_review_enabled=True,
        )
    else:
        link = await storage.create_repo_link(
            platform="github",
            repo_owner="octo",
            repo_name="service",
            profile_id=uuid.UUID(profile["id"]),
            connection_id=uuid.UUID(connection["id"]),
            auto_review_enabled=True,
        )
    return profile, connection, link


async def test_opted_pr_waits_for_checks_then_becomes_reviewable():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo()
            adapter = FakeReadinessAdapter(
                signals=[PlatformReadinessSignal("ci", "in_progress", "check_run")]
            )

            candidate = await create_or_update_candidate_from_pr(
                _pr(),
                adapter=adapter,
                source="webhook",
            )

            assert candidate is not None
            assert candidate["state"] == "waiting"
            assert candidate["reason"] == "checks_pending"
            assert adapter.statuses[-1][1] == "pending"

            adapter.signals = [PlatformReadinessSignal("ci", "success", "check_run")]
            updated = await evaluate_candidate(uuid.UUID(candidate["id"]), adapter=adapter)

            assert updated["state"] == "reviewing"
            assert updated["reason"] == "ready"
            assert adapter.statuses[-1][1] == "success"
    finally:
        await engine.dispose()


async def test_terminal_candidate_at_same_sha_is_not_reposted():
    # Guards the poll fallback: re-triggering a PR that's already been reviewed at
    # this exact SHA must return the existing candidate untouched — never re-post a
    # pending readiness status (which would flip a green check back to pending each
    # poll pass).
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            _, _, link = await _linked_repo()
            existing = await storage.create_readiness_candidate(
                repo_link_id=uuid.UUID(link["id"]),
                pr_id="42",
                head_sha="sha1",
                state="reviewed",
                reason="completed",
            )
            adapter = FakeReadinessAdapter()

            result = await create_or_update_candidate_from_pr(
                _pr(), adapter=adapter, source="poll:github"
            )

            assert result is not None
            assert result["id"] == existing["id"]
            assert result["state"] == "reviewed"
            assert adapter.statuses == []
    finally:
        await engine.dispose()


async def test_unchanged_readiness_status_is_not_reposted():
    # Posting a commit status fires a GitHub `status` webhook that re-triggers
    # evaluation. Re-posting an *identical* status is a self-amplifying loop
    # that burns through GitHub's 1000-statuses-per-context-per-SHA cap, after
    # which writes 422 and the candidate strands in "error". An unchanged
    # decision must therefore write no new status; a real change still does.
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo()
            adapter = FakeReadinessAdapter(
                signals=[PlatformReadinessSignal("ci", "in_progress", "check_run")]
            )
            candidate = await create_or_update_candidate_from_pr(
                _pr(), adapter=adapter, source="webhook"
            )
            assert candidate is not None
            assert candidate["reason"] == "checks_pending"

            def readiness_writes() -> list[tuple[str, str, str]]:
                return [s for s in adapter.statuses if s[0] == "guardian/readiness"]

            before = len(readiness_writes())

            # Re-evaluate repeatedly with the SAME pending signal: no new writes.
            for _ in range(3):
                again = await evaluate_candidate(uuid.UUID(candidate["id"]), adapter=adapter)
                assert again["reason"] == "checks_pending"
            assert len(readiness_writes()) == before

            # A genuine change (checks pass) must write again.
            adapter.signals = [PlatformReadinessSignal("ci", "success", "check_run")]
            await evaluate_candidate(uuid.UUID(candidate["id"]), adapter=adapter)
            new_writes = readiness_writes()
            assert len(new_writes) == before + 1
            assert new_writes[-1][1] == "success"
    finally:
        await engine.dispose()


async def test_failed_timeout_fork_and_permission_readiness_outcomes():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo({"readiness": {"quiet_period_seconds": 0, "max_wait_minutes": 30}})

            failed = await create_or_update_candidate_from_pr(
                _pr("failed"),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="failed"),
                    signals=[PlatformReadinessSignal("ci", "failure", "check_run")],
                ),
            )
            assert failed is not None
            assert failed["state"] == "blocked"
            assert failed["reason"] == "checks_failed"

            recovered = await evaluate_candidate(
                uuid.UUID(failed["id"]),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="failed"),
                    signals=[PlatformReadinessSignal("ci", "success", "check_run")],
                ),
            )
            assert recovered["state"] == "reviewing"

            timed = await storage.create_readiness_candidate(
                repo_link_id=uuid.UUID(failed["repo_link_id"]),
                pr_id="43",
                head_sha="timed",
            )
            old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
            await storage.record_candidate_transition(
                uuid.UUID(timed["id"]),
                to_state="waiting",
                source="fixture",
                reason="checks_pending",
                readiness_snapshot={"readiness_started_at": old},
            )
            timed = await evaluate_candidate(
                uuid.UUID(timed["id"]),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="timed"),
                    signals=[PlatformReadinessSignal("ci", "pending", "status")],
                ),
                pr=_pr("timed"),
            )
            assert timed["state"] == "blocked"
            assert timed["reason"] == "checks_timeout"

            fork = await create_or_update_candidate_from_pr(
                _pr("fork"),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="fork", fork=True)
                ),
            )
            assert fork is not None
            assert fork["state"] == "blocked"
            assert fork["reason"] == "fork_requires_manual_start"

            permission = await create_or_update_candidate_from_pr(
                _pr("permission"),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="permission"),
                    metadata_error=PermissionError("forbidden"),
                ),
            )
            assert permission is not None
            assert permission["state"] == "error"
            assert permission["reason"] == "platform_error"
    finally:
        await engine.dispose()


async def test_platform_error_posts_pending_not_failing_readiness_status():
    """A platform_error is Guardian's own infra problem, not a PR failure. It must
    surface as a neutral 'pending' on guardian/readiness (the candidate is
    recoverable and the reconciler will flip it to success), never a red 'failure'
    that alarms on every PR."""
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo({"readiness": {"quiet_period_seconds": 0, "max_wait_minutes": 30}})
            adapter = FakeReadinessAdapter(
                metadata=PlatformPRMetadata(head_sha="boom"),
                metadata_error=RuntimeError("github unreachable"),
            )
            candidate = await create_or_update_candidate_from_pr(_pr("boom"), adapter=adapter)

            assert candidate is not None
            assert candidate["state"] == "error"
            assert candidate["reason"] == "platform_error"
            readiness_statuses = [s for s in adapter.statuses if s[0] == "guardian/readiness"]
            assert readiness_statuses, "expected guardian/readiness to be posted"
            assert all(state != "failure" for _, state, _ in readiness_statuses)
            assert readiness_statuses[-1][1] == "pending"
    finally:
        await engine.dispose()


class _FakeHTTPError(Exception):
    """Mimics httpx.HTTPStatusError: carries a .response with a status code."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("Resp", (), {"status_code": status_code})()


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_persistent_access_error_is_distinct_and_visible(status):
    """An auth/access/not-found failure won't self-heal by retrying — the credential
    can't see the repo. It must get the distinct 'platform_access_error' reason (not
    the transient 'platform_error' bucket) so the dashboard surfaces it for an
    operator instead of hiding it as noise."""
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo({"readiness": {"quiet_period_seconds": 0, "max_wait_minutes": 30}})
            adapter = FakeReadinessAdapter(
                metadata=PlatformPRMetadata(head_sha="boom"),
                metadata_error=_FakeHTTPError(status),
            )
            candidate = await create_or_update_candidate_from_pr(_pr("boom"), adapter=adapter)

            assert candidate is not None
            assert candidate["state"] == "error"
            assert candidate["reason"] == "platform_access_error"
            assert candidate["readiness_snapshot"].get("error_status") == status
            assert _candidate_visible(candidate) is True
    finally:
        await engine.dispose()


async def test_transient_platform_error_stays_hidden():
    """A non-HTTP failure (no status) is a transient Guardian-side blip — it keeps the
    'platform_error' reason and stays hidden from the operator queue."""
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo({"readiness": {"quiet_period_seconds": 0, "max_wait_minutes": 30}})
            adapter = FakeReadinessAdapter(
                metadata=PlatformPRMetadata(head_sha="boom"),
                metadata_error=RuntimeError("github unreachable"),
            )
            candidate = await create_or_update_candidate_from_pr(_pr("boom"), adapter=adapter)

            assert candidate is not None
            assert candidate["reason"] == "platform_error"
            assert _candidate_visible(candidate) is False
    finally:
        await engine.dispose()


async def test_check_event_recovers_errored_candidate():
    """An errored candidate must self-heal on the next CI-completion (by-SHA) event,
    not stay wedged until a new commit. evaluate_candidates_for_sha must therefore
    re-evaluate 'error' candidates, matching the reconciler."""
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo({"readiness": {"quiet_period_seconds": 0, "max_wait_minutes": 30}})
            errored = await create_or_update_candidate_from_pr(
                _pr("sha1"),
                adapter=FakeReadinessAdapter(
                    metadata=PlatformPRMetadata(head_sha="sha1"),
                    metadata_error=RuntimeError("github unreachable"),
                ),
            )
            assert errored is not None
            assert errored["state"] == "error"

            healthy = FakeReadinessAdapter(
                metadata=PlatformPRMetadata(head_sha="sha1"),
                signals=[PlatformReadinessSignal("ci", "success", "check_run")],
            )
            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=healthy):
                evaluated = await evaluate_candidates_for_sha(
                    platform="github",
                    repo="octo/service",
                    head_sha="sha1",
                    source="github:check_run",
                )

            assert len(evaluated) == 1
            assert evaluated[0]["state"] == "reviewing"
    finally:
        await engine.dispose()


async def test_reconciler_starts_candidate_after_missed_check_event():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            _, _, link = await _linked_repo()
            candidate = await storage.create_readiness_candidate(
                repo_link_id=uuid.UUID(link["id"]),
                pr_id="42",
                head_sha="sha1",
            )
            adapter = FakeReadinessAdapter(
                signals=[PlatformReadinessSignal("ci", "success", "check_run")]
            )

            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=adapter):
                assert await reconcile_readiness_once() == 1

            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "reviewing"
            transitions = await storage.list_candidate_transitions(uuid.UUID(candidate["id"]))
            assert transitions[-1]["source"] == "reconciler"
    finally:
        await engine.dispose()


async def test_reconciler_recovers_stale_reviewing_candidate():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            _, _, link = await _linked_repo()
            candidate = await storage.create_readiness_candidate(
                repo_link_id=uuid.UUID(link["id"]),
                pr_id="42",
                head_sha="sha1",
            )
            started = await storage.try_start_candidate_review(
                uuid.UUID(candidate["id"]),
                _pr(),
                source="automatic",
                actor="webhook",
                reason="ready",
                readiness_snapshot={"ready": True},
            )
            assert started is not None
            old_review_id, _ = started
            stale_time = datetime.now(timezone.utc) - timedelta(minutes=16)
            with patch("pr_guardian.persistence.storage._now", return_value=stale_time):
                await storage.record_candidate_transition(
                    uuid.UUID(candidate["id"]),
                    to_state="reviewing",
                    source="fixture",
                    reason="ready",
                    readiness_snapshot={"ready": True},
                )

            adapter = FakeReadinessAdapter(
                signals=[PlatformReadinessSignal("ci", "success", "check_run")]
            )
            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=adapter):
                assert await reconcile_readiness_once() == 1

            abandoned = await storage.get_review(old_review_id)
            assert abandoned is not None
            assert abandoned["stage"] == "error"
            assert abandoned["decision"] == "error"
            assert "heartbeat expired" in abandoned["stage_detail"]

            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "reviewing"
            assert updated["reason"] == "ready"
            transitions = await storage.list_candidate_transitions(uuid.UUID(candidate["id"]))
            assert any(t["reason"] == "review_worker_stale" for t in transitions)
            reviews = await storage.list_reviews(limit=10)
            linked_reviews = [r for r in reviews if r.get("candidate_id") == candidate["id"]]
            assert len(linked_reviews) == 2
    finally:
        await engine.dispose()


async def test_automatic_review_startup_failure_is_marked_and_reported(monkeypatch):
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo()
            adapter = FakeReadinessAdapter(
                signals=[PlatformReadinessSignal("ci", "success", "check_run")]
            )
            monkeypatch.setattr(
                "pr_guardian.config.profile_resolver.resolve_profile_snapshot_config",
                AsyncMock(side_effect=RuntimeError("profile snapshot invalid")),
            )

            # Capture the asyncio Task spawned by _start_automatic_review so we
            # can await it explicitly. This prevents any concurrent DB session
            # from racing the background task's session on the shared in-memory
            # connection (which would cause pool reset to rollback pending writes).
            bg_tasks: list[asyncio.Task] = []
            _orig_create_task = asyncio.create_task

            def _capture_task(coro, **kw):
                t = _orig_create_task(coro, **kw)
                bg_tasks.append(t)
                return t

            with patch("pr_guardian.core.readiness.asyncio.create_task", _capture_task):
                candidate = await create_or_update_candidate_from_pr(_pr(), adapter=adapter)
            assert candidate is not None

            # Wait for _run() to finish before opening any DB session from this test.
            await asyncio.gather(*bg_tasks, return_exceptions=True)

            reviews = await storage.list_reviews(limit=10)
            linked_reviews = [r for r in reviews if r.get("candidate_id") == candidate["id"]]
            assert len(linked_reviews) == 1
            review = linked_reviews[0]
            assert review["stage"] == "error"
            assert review["decision"] == "error"
            assert "Automatic review startup failed" in review["stage_detail"]
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "error"
            assert updated["reason"] == "review_failed"
            recoverable = await storage.list_recoverable_readiness_candidates()
            assert candidate["id"] not in {c["id"] for c in recoverable}
            assert (
                "guardian/review",
                "failure",
                "Guardian review failed before starting",
            ) in adapter.statuses
    finally:
        await engine.dispose()


async def _reviewed_candidate(link: dict):
    """Drive a candidate all the way to the terminal ``reviewed`` state."""
    candidate = await storage.create_readiness_candidate(
        repo_link_id=uuid.UUID(link["id"]),
        pr_id="42",
        head_sha="sha1",
    )
    started = await storage.try_start_candidate_review(
        uuid.UUID(candidate["id"]),
        _pr(),
        source="automatic",
        actor="webhook",
        reason="ready",
        readiness_snapshot={"ready": True},
    )
    assert started is not None
    review_id, _ = started
    assert await storage.mark_candidate_reviewed_for_review(review_id)
    return candidate


async def test_reconciler_reasserts_stranded_readiness_for_reviewed_candidate():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            _, _, link = await _linked_repo()
            candidate = await _reviewed_candidate(link)

            # First pass: the stranded check is re-asserted to success and the
            # candidate is flagged synced.
            adapter = FakeReadinessAdapter()
            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=adapter):
                assert await reconcile_readiness_once() == 1
            assert adapter.statuses == [
                ("guardian/readiness", "success", "Guardian readiness: review_completed")
            ]
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["state"] == "reviewed"
            assert updated["readiness_synced"] is True

            # Second pass: the synced flag excludes it — no candidate, no re-post.
            adapter2 = FakeReadinessAdapter()
            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=adapter2):
                assert await reconcile_readiness_once() == 0
            assert adapter2.statuses == []
    finally:
        await engine.dispose()


async def test_reviewed_readiness_reassert_failure_leaves_candidate_unsynced():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            _, _, link = await _linked_repo()
            candidate = await _reviewed_candidate(link)

            class _RaisingAdapter(FakeReadinessAdapter):
                async def set_readiness_status(self, pr, state, description):
                    raise RuntimeError("status write failed")

            adapter = _RaisingAdapter()
            with patch("pr_guardian.core.readiness._adapter_for_candidate", return_value=adapter):
                # The reconcile tick survives the failed write (best-effort).
                assert await reconcile_readiness_once() == 1
            updated = await storage.get_readiness_candidate_by_id(uuid.UUID(candidate["id"]))
            assert updated is not None
            assert updated["readiness_synced"] is False
            # Still eligible, so the next tick retries.
            recoverable = await storage.list_recoverable_readiness_candidates()
            assert candidate["id"] in {c["id"] for c in recoverable}
    finally:
        await engine.dispose()


class _AdoResponse:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _AdoClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, params: dict | None = None):
        params = params or {}
        self.calls.append((url, params))
        if url.endswith("/_apis/build/builds"):
            return _AdoResponse({"value": [{"id": 7}]})
        return _AdoResponse({"name": params.get("artifactName")}, status_code=404)


async def test_archmap_wait_times_out_soft_and_ado_uses_sha_artifact_name():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await _linked_repo(
                {
                    "readiness": {
                        "quiet_period_seconds": 0,
                        "archmap_expected": True,
                        "archmap_max_wait_minutes": 0,
                    }
                }
            )
            candidate = await create_or_update_candidate_from_pr(
                _pr(),
                adapter=FakeReadinessAdapter(
                    signals=[PlatformReadinessSignal("ci", "success", "check_run")],
                    archmap_found=False,
                ),
            )

            assert candidate is not None
            assert candidate["state"] == "reviewing"
            assert candidate["readiness_snapshot"]["archmap"]["warning"] == "archmap_timeout"

            client = _AdoClient()
            ado = ADOAdapter(pat="fixture", org_url="https://dev.azure.com/acme")
            with patch.object(ado, "_get_client", return_value=client):
                found = await ado.find_archmap_artifact(
                    _pr("abc123", platform=Platform.ADO), "abc123"
                )
            assert found is False
            assert any(
                params.get("artifactName") == "archmap-abc123" for _, params in client.calls
            )
    finally:
        await engine.dispose()
