from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_guardian.config.profile_resolver import (
    ProfileResolutionError,
    ResolvedProfileConfig,
    resolve_profile_config,
)
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.pr import Diff
from pr_guardian.persistence.storage import create_connection, create_profile, create_repo_link
from tests.test_readiness_storage import _make_session_factory


@pytest.mark.asyncio
async def test_manual_review_uses_linked_profile_or_requires_connection_picker():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            profile = await create_profile(
                "Manual Linked",
                settings={"repo_risk_class": "critical"},
            )
            connection = await create_connection(
                "GitHub Main",
                platform="github",
                token="fixture-value-main",
                health_status="healthy",
            )
            await create_connection(
                "GitHub Backup",
                platform="github",
                token="fixture-value-backup",
                health_status="healthy",
            )
            await create_repo_link(
                platform="github",
                repo_owner="octo",
                repo_name="service",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
            )

            linked = await resolve_profile_config(
                platform="github",
                repo="octo/service",
                require_connection=True,
                allow_db_failure=False,
            )
            assert linked.linked is True
            assert linked.config.repo_risk_class == "critical"
            assert linked.connection_id == uuid.UUID(connection["id"])

            with pytest.raises(ProfileResolutionError, match="Connection selection"):
                await resolve_profile_config(
                    platform="github",
                    repo="octo/unlinked",
                    require_connection=True,
                    allow_db_failure=False,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repo_review_uses_profile_but_suppresses_platform_side_effects():
    from pr_guardian.api.review import _run_repo_review_background

    adapter = MagicMock()
    adapter.close = AsyncMock()
    review_id = uuid.uuid4()
    resolved = ResolvedProfileConfig(
        config=GuardianConfig(repo_risk_class="critical"),
        profile_id=uuid.uuid4(),
        profile_snapshot={"id": str(uuid.uuid4()), "settings": {"repo_risk_class": "critical"}},
        connection_id=uuid.uuid4(),
        connection_snapshot={"id": str(uuid.uuid4()), "name": "GitHub Main"},
        repo_link_id=uuid.uuid4(),
        source="linked",
    )
    storage = MagicMock()
    storage.create_review_record = AsyncMock(return_value=review_id)
    storage.set_review_provenance = AsyncMock()
    storage.update_review_stage = AsyncMock()
    storage.mark_review_failed = AsyncMock()
    meta = {
        "files_included": 0,
        "files_truncated": 0,
        "files_skipped_binary": 0,
        "selection_capped": False,
        "total_bytes": 0,
    }

    with (
        patch("pr_guardian.core.orchestrator.get_storage", return_value=storage),
        patch(
            "pr_guardian.api.review.build_repo_diff",
            new_callable=AsyncMock,
            return_value=(Diff(files=[]), meta),
        ),
        patch("pr_guardian.api.review.run_review", new_callable=AsyncMock) as run_review,
    ):
        await _run_repo_review_background(
            "octo/service",
            "github",
            adapter,
            "HEAD",
            50,
            resolved_profile=resolved,
        )

    storage.set_review_provenance.assert_awaited_once()
    run_review.assert_awaited_once()
    kwargs = run_review.call_args.kwargs
    assert kwargs["service_config"] is resolved.config
    assert kwargs["post_comment"] is False
    assert kwargs["skip_platform_side_effects"] is True
