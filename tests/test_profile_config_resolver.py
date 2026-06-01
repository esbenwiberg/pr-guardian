from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from pr_guardian.config.loader import load_repo_config
from pr_guardian.config.profile_resolver import (
    sanitize_profile_settings,
    resolve_profile_config,
)
from pr_guardian.persistence.storage import (
    DEFAULT_PROFILE_ID,
    create_connection,
    create_profile,
    create_repo_link,
    ensure_default_profile,
)

from tests.test_readiness_storage import _make_session_factory


@pytest.mark.asyncio
async def test_linked_and_unlinked_runs_resolve_expected_profiles():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            await ensure_default_profile()
            profile = await create_profile(
                "Critical Service",
                settings={
                    "repo_risk_class": "critical",
                    "thresholds": {
                        "auto_approve_max_score": 2,
                        "human_review_min_score": 3,
                        "hard_block_score": 9,
                    },
                    "side_effects": {"scan_issues": True},
                    "llm": {"default_provider": "should-not-enter-profile"},
                },
            )
            connection = await create_connection(
                "GitHub Main",
                platform="github",
                token="fixture-value-profile-resolver",
                health_status="healthy",
            )
            link = await create_repo_link(
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
            assert linked.repo_link_id == uuid.UUID(link["id"])
            assert linked.config.repo_risk_class == "critical"
            assert linked.config.thresholds.auto_approve_max_score == 2
            assert linked.config.thresholds.hard_block_score == 9
            assert linked.profile_snapshot is not None
            assert "llm" not in linked.profile_snapshot["settings"]
            assert "human_review_min_score" not in linked.profile_snapshot["settings"]["thresholds"]

            unlinked = await resolve_profile_config(
                platform="github",
                repo="octo/unlinked",
                require_connection=True,
                allow_db_failure=False,
            )
            assert unlinked.source == "default"
            assert unlinked.profile_id == DEFAULT_PROFILE_ID
            assert unlinked.connection_id == uuid.UUID(connection["id"])
            assert unlinked.config.side_effects.scan_issues is False
    finally:
        await engine.dispose()


def test_review_yml_is_not_read_by_product_policy_paths(tmp_path):
    (tmp_path / "review.yml").write_text("repo_risk_class: critical\n")
    config = load_repo_config(tmp_path)
    assert config.repo_risk_class == "standard"


def test_profile_schema_excludes_llm_runtime_and_dormant_fields():
    settings = sanitize_profile_settings(
        {
            "repo_risk_class": "elevated",
            "llm": {"default_provider": "openai"},
            "agents": {"timeout_seconds": 5},
            "intent_verification": {"enabled": True},
            "privacy": {"compliance_frameworks": ["gdpr"]},
            "feedback": {"enabled": True},
            "test_quality": {"min_assertion_quality_score": 0.9},
            "triage": {"agent_context_thresholds": {"compact": 1}},
            "thresholds": {
                "auto_approve_max_score": 1,
                "human_review_min_score": 2,
                "hard_block_score": 3,
            },
            "side_effects": {"scan_issues": True},
        }
    )
    assert settings["repo_risk_class"] == "elevated"
    assert settings["thresholds"] == {
        "auto_approve_max_score": 1,
        "hard_block_score": 3,
    }
    assert settings["side_effects"]["scan_issues"] is True
    assert "llm" not in settings
    assert "agents" not in settings
    assert "intent_verification" not in settings
    assert "privacy" not in settings
    assert "feedback" not in settings
    assert "test_quality" not in settings
    assert "triage" not in settings
