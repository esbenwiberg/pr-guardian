"""Storage coverage for repo links, readiness candidates, and provenance snapshots."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence.storage import (
    create_connection,
    create_profile,
    create_readiness_candidate,
    create_repo_link,
    create_review_record,
    create_scan_record,
    ensure_default_profile,
    get_readiness_candidate,
    get_review,
    get_scan,
    list_candidate_transitions,
    record_candidate_transition,
    set_review_provenance,
    set_scan_provenance,
)


_meta = sa.MetaData()
sa.Table(
    "profiles",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.String(128), unique=True),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "connections",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.String(128), unique=True),
    sa.Column("description", sa.String(256), nullable=False, server_default=""),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("org_url", sa.Text),
    sa.Column("encrypted_token", sa.Text),
    sa.Column("token_secret_ref", sa.Text),
    sa.Column("token_prefix", sa.String(20), nullable=False, server_default=""),
    sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown"),
    sa.Column("health_message", sa.Text, nullable=False, server_default=""),
    sa.Column("health_checked_at", sa.DateTime(timezone=True)),
    sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "repo_links",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("org_url", sa.Text, nullable=False, server_default=""),
    sa.Column("project", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_owner", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_name", sa.String(256), nullable=False),
    sa.Column("repo_url", sa.Text, nullable=False, server_default=""),
    sa.Column("canonical_repo_key", sa.String(512), nullable=False, unique=True),
    sa.Column("profile_id", sa.Text, nullable=False),
    sa.Column("connection_id", sa.Text, nullable=False),
    sa.Column("auto_review_enabled", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("paused", sa.Boolean, nullable=False, server_default="false"),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("created_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("updated_by", sa.String(256), nullable=False, server_default="system"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "profile_audit_events",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("actor", sa.String(256), nullable=False, server_default="system"),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("target_type", sa.String(64), nullable=False),
    sa.Column("target_id", sa.Text),
    sa.Column("before", sa.JSON),
    sa.Column("after", sa.JSON),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "readiness_candidates",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("repo_link_id", sa.Text, nullable=False),
    sa.Column("profile_id", sa.Text),
    sa.Column("connection_id", sa.Text),
    sa.Column("platform", sa.String(16), nullable=False),
    sa.Column("org_url", sa.Text, nullable=False, server_default=""),
    sa.Column("project", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_owner", sa.String(256), nullable=False, server_default=""),
    sa.Column("repo_name", sa.String(256), nullable=False),
    sa.Column("repo", sa.String(512), nullable=False),
    sa.Column("canonical_repo_key", sa.String(512), nullable=False),
    sa.Column("pr_id", sa.String(64), nullable=False),
    sa.Column("pr_url", sa.Text, nullable=False, server_default=""),
    sa.Column("head_sha", sa.String(64), nullable=False),
    sa.Column("state", sa.String(16), nullable=False, server_default="waiting"),
    sa.Column("reason", sa.String(128), nullable=False, server_default=""),
    sa.Column("readiness_snapshot", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("profile_snapshot", sa.JSON),
    sa.Column("connection_snapshot", sa.JSON),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "readiness_candidate_transitions",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("candidate_id", sa.Text, nullable=False),
    sa.Column("from_state", sa.String(16)),
    sa.Column("to_state", sa.String(16), nullable=False),
    sa.Column("source", sa.String(64), nullable=False, server_default=""),
    sa.Column("actor", sa.String(256), nullable=False, server_default=""),
    sa.Column("reason", sa.String(128), nullable=False, server_default=""),
    sa.Column("readiness_snapshot", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)
sa.Table(
    "reviews",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("pr_id", sa.String(64)),
    sa.Column("repo", sa.String(256)),
    sa.Column("platform", sa.String(16)),
    sa.Column("author", sa.String(128), server_default=""),
    sa.Column("title", sa.Text, server_default=""),
    sa.Column("source_branch", sa.String(256), server_default=""),
    sa.Column("target_branch", sa.String(256), server_default=""),
    sa.Column("head_commit_sha", sa.String(64), server_default=""),
    sa.Column("pr_url", sa.Text, server_default=""),
    sa.Column("risk_tier", sa.String(16), server_default=""),
    sa.Column("repo_risk_class", sa.String(16), server_default="standard"),
    sa.Column("trust_tier", sa.String(32), server_default=""),
    sa.Column("trust_tier_details", sa.JSON),
    sa.Column("combined_score", sa.Float, server_default="0"),
    sa.Column("decision", sa.String(32), server_default="pending"),
    sa.Column("mechanical_passed", sa.Boolean, server_default="true"),
    sa.Column("override_reasons", sa.JSON, server_default="[]"),
    sa.Column("summary", sa.Text, server_default=""),
    sa.Column("stage", sa.String(32), server_default="queued"),
    sa.Column("stage_detail", sa.Text, server_default=""),
    sa.Column("pipeline_log", sa.JSON, server_default="[]"),
    sa.Column("total_input_tokens", sa.Integer, server_default="0"),
    sa.Column("total_output_tokens", sa.Integer, server_default="0"),
    sa.Column("cost_usd", sa.Float, server_default="0"),
    sa.Column("comment_mode", sa.String(32), server_default="none"),
    sa.Column("profile_id", sa.Text),
    sa.Column("profile_snapshot", sa.JSON),
    sa.Column("connection_id", sa.Text),
    sa.Column("connection_snapshot", sa.JSON),
    sa.Column("repo_link_id", sa.Text),
    sa.Column("candidate_id", sa.Text),
    sa.Column("review_source", sa.String(32), server_default="manual"),
    sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("duration_ms", sa.Integer),
)
sa.Table(
    "scans",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("scan_type", sa.String(32)),
    sa.Column("repo", sa.String(256)),
    sa.Column("platform", sa.String(16)),
    sa.Column("time_window_days", sa.Integer, server_default="7"),
    sa.Column("staleness_months", sa.Integer, server_default="6"),
    sa.Column("total_findings", sa.Integer, server_default="0"),
    sa.Column("summary", sa.Text, server_default=""),
    sa.Column("stage", sa.String(32), server_default="queued"),
    sa.Column("stage_detail", sa.Text, server_default=""),
    sa.Column("pipeline_log", sa.JSON, server_default="[]"),
    sa.Column("total_input_tokens", sa.Integer, server_default="0"),
    sa.Column("total_output_tokens", sa.Integer, server_default="0"),
    sa.Column("cost_usd", sa.Float, server_default="0"),
    sa.Column("profile_id", sa.Text),
    sa.Column("profile_snapshot", sa.JSON),
    sa.Column("connection_id", sa.Text),
    sa.Column("connection_snapshot", sa.JSON),
    sa.Column("repo_link_id", sa.Text),
    sa.Column("scan_source", sa.String(32), server_default="scan"),
    sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("duration_ms", sa.Integer),
)
sa.Table(
    "mechanical_results",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("review_id", sa.Text),
    sa.Column("tool", sa.String(64)),
    sa.Column("passed", sa.Boolean),
    sa.Column("severity", sa.String(16)),
    sa.Column("findings", sa.JSON),
    sa.Column("error", sa.Text),
)
sa.Table(
    "agent_results",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("review_id", sa.Text),
    sa.Column("agent_name", sa.String(64)),
    sa.Column("verdict", sa.String(16)),
    sa.Column("languages_reviewed", sa.JSON),
    sa.Column("error", sa.Text),
    sa.Column("verdict_explanation", sa.Text),
)
sa.Table(
    "findings",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("agent_result_id", sa.Text),
    sa.Column("severity", sa.String(16)),
    sa.Column("certainty", sa.String(16)),
    sa.Column("category", sa.String(128)),
    sa.Column("language", sa.String(32)),
    sa.Column("file", sa.Text),
    sa.Column("line", sa.Integer),
    sa.Column("description", sa.Text),
    sa.Column("suggestion", sa.Text),
    sa.Column("cwe", sa.String(32)),
)
sa.Table(
    "scan_agent_results",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("scan_id", sa.Text),
    sa.Column("agent_name", sa.String(64)),
    sa.Column("verdict", sa.String(16)),
    sa.Column("summary", sa.Text),
    sa.Column("error", sa.Text),
)
sa.Table(
    "scan_findings",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("agent_result_id", sa.Text),
    sa.Column("severity", sa.String(16)),
    sa.Column("certainty", sa.String(16)),
    sa.Column("category", sa.String(64)),
    sa.Column("file", sa.Text),
    sa.Column("line", sa.Integer),
    sa.Column("description", sa.Text),
    sa.Column("suggestion", sa.Text),
    sa.Column("priority", sa.Float),
    sa.Column("last_modified", sa.String(64)),
    sa.Column("effort_estimate", sa.String(16)),
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_profile_link_candidate_and_provenance_persistence():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            default_profile = await ensure_default_profile()
            assert default_profile["is_default"] is True

            profile = await create_profile(
                "Standard Service",
                settings={"thresholds": {"auto_approve_max_score": 15}},
            )
            connection = await create_connection(
                "GitHub Main",
                platform="github",
                token="ghp_readiness_secret",
                health_status="healthy",
                sync_enabled=True,
            )
            assert "encrypted_token" not in connection

            link = await create_repo_link(
                platform="github",
                repo_owner="octo",
                repo_name="service",
                repo_url="https://github.com/octo/service",
                profile_id=uuid.UUID(profile["id"]),
                connection_id=uuid.UUID(connection["id"]),
                auto_review_enabled=True,
            )

            candidate = await create_readiness_candidate(
                repo_link_id=uuid.UUID(link["id"]),
                pr_id="42",
                pr_url="https://github.com/octo/service/pull/42",
                head_sha="abc123",
                readiness_snapshot={"checks": {"passed": 1, "pending": 2}},
            )
            transition = await record_candidate_transition(
                uuid.UUID(candidate["id"]),
                to_state="blocked",
                source="webhook",
                actor="github",
                reason="checks_failed",
                readiness_snapshot={"checks": {"failed": 1}},
            )

            loaded = await get_readiness_candidate(
                platform="github",
                repo="octo/service",
                pr_id="42",
                head_sha="abc123",
            )
            assert loaded is not None
            assert loaded["repo_link_id"] == link["id"]
            assert loaded["profile_id"] == profile["id"]
            assert loaded["connection_id"] == connection["id"]
            assert loaded["state"] == "blocked"

            transitions = await list_candidate_transitions(uuid.UUID(candidate["id"]))
            assert [t["id"] for t in transitions] == [transition["id"]]
            assert transitions[0]["source"] == "webhook"
            assert transitions[0]["reason"] == "checks_failed"
            assert transitions[0]["readiness_snapshot"]["checks"]["failed"] == 1

            pr = PlatformPR(
                platform=Platform.GITHUB,
                pr_id="42",
                repo="octo/service",
                repo_url="https://github.com/octo/service",
                source_branch="feature",
                target_branch="main",
                author="alice",
                title="Feature",
                head_commit_sha="abc123",
            )
            review_id = await create_review_record(pr, comment_mode="summary")
            scan_id = await create_scan_record("recent_changes", "octo/service", "github")
            profile_snapshot = loaded["profile_snapshot"]
            connection_snapshot = loaded["connection_snapshot"]
            await set_review_provenance(
                review_id,
                profile_id=uuid.UUID(profile["id"]),
                profile_snapshot=profile_snapshot,
                connection_id=uuid.UUID(connection["id"]),
                connection_snapshot=connection_snapshot,
                repo_link_id=uuid.UUID(link["id"]),
                candidate_id=uuid.UUID(candidate["id"]),
                review_source="automatic",
            )
            await set_scan_provenance(
                scan_id,
                profile_id=uuid.UUID(profile["id"]),
                profile_snapshot=profile_snapshot,
                connection_id=uuid.UUID(connection["id"]),
                connection_snapshot=connection_snapshot,
                repo_link_id=uuid.UUID(link["id"]),
                scan_source="scan",
            )

            stored_review = await get_review(review_id)
            stored_scan = await get_scan(scan_id)
            assert stored_review is not None
            assert stored_review["profile_snapshot"]["name"] == "Standard Service"
            assert stored_review["connection_snapshot"]["name"] == "GitHub Main"
            assert stored_review["candidate_id"] == candidate["id"]
            assert stored_review["review_source"] == "automatic"
            assert "pat_name" not in stored_review
            assert stored_scan is not None
            assert stored_scan["profile_snapshot"]["name"] == "Standard Service"
            assert stored_scan["connection_snapshot"]["token_prefix"] == "ghp_read..."
    finally:
        await engine.dispose()
