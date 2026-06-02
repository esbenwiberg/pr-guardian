from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pr_guardian.core.readiness import create_or_update_candidate_from_pr, evaluate_candidate
from pr_guardian.core.readiness_reconciler import reconcile_readiness_once
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence import storage
from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.protocol import PlatformPRMetadata, PlatformReadinessSignal
from tests.test_readiness_storage import _make_session_factory


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
