from __future__ import annotations

import uuid
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.models.context import RepoRiskClass, RiskTier
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision, ReviewResult
from pr_guardian.models.pr import Platform, PlatformPR
from pr_guardian.persistence.storage import create_review_record, get_review, save_review_result


def _table_meta() -> sa.MetaData:
    meta = sa.MetaData()
    sa.Table(
        "reviews",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("pr_id", sa.String(64)),
        sa.Column("repo", sa.String(256)),
        sa.Column("platform", sa.String(16)),
        sa.Column("author", sa.String(128)),
        sa.Column("title", sa.Text),
        sa.Column("source_branch", sa.String(256)),
        sa.Column("target_branch", sa.String(256)),
        sa.Column("head_commit_sha", sa.String(64)),
        sa.Column("pr_url", sa.Text),
        sa.Column("risk_tier", sa.String(16)),
        sa.Column("repo_risk_class", sa.String(16)),
        sa.Column("trust_tier", sa.String(32)),
        sa.Column("trust_tier_details", sa.JSON),
        sa.Column("combined_score", sa.Float),
        sa.Column("decision", sa.String(32)),
        sa.Column("mechanical_passed", sa.Boolean),
        sa.Column("override_reasons", sa.JSON),
        sa.Column("summary", sa.Text),
        sa.Column("stage", sa.String(32)),
        sa.Column("stage_detail", sa.Text),
        sa.Column("pipeline_log", sa.JSON),
        sa.Column("total_input_tokens", sa.Integer),
        sa.Column("total_output_tokens", sa.Integer),
        sa.Column("cost_usd", sa.Float),
        sa.Column("comment_mode", sa.String(32), default="none"),
        sa.Column("pat_name", sa.String(128)),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("duration_ms", sa.Integer),
    )
    sa.Table(
        "mechanical_results",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("review_id", sa.String),
        sa.Column("tool", sa.String(64)),
        sa.Column("passed", sa.Boolean),
        sa.Column("severity", sa.String(16)),
        sa.Column("findings", sa.JSON),
        sa.Column("error", sa.Text),
    )
    sa.Table(
        "agent_results",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("review_id", sa.String),
        sa.Column("agent_name", sa.String(64)),
        sa.Column("verdict", sa.String(16)),
        sa.Column("status", sa.String(16)),
        sa.Column("status_reason", sa.Text),
        sa.Column("languages_reviewed", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("verdict_explanation", sa.Text),
    )
    sa.Table(
        "findings",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("agent_result_id", sa.String),
        sa.Column("severity", sa.String(16)),
        sa.Column("certainty", sa.String(16)),
        sa.Column("category", sa.String(128)),
        sa.Column("language", sa.String(32)),
        sa.Column("file", sa.Text),
        sa.Column("line", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("quote", sa.Text),
        sa.Column("suggestion", sa.Text),
        sa.Column("cwe", sa.String(32)),
    )
    return meta


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_table_meta().create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_quote_status_roundtrip_through_storage_and_dashboard_shape():
    engine, factory = await _make_session_factory()
    try:
        pr = PlatformPR(
            platform=Platform.GITHUB,
            pr_id="42",
            repo="org/repo",
            repo_url="https://github.com/org/repo",
            source_branch="feature",
            target_branch="main",
            author="dev",
            title="Wire auth guard",
            head_commit_sha="abc123",
        )
        quote = "return user.is_admin or allow_all"
        result = ReviewResult(
            pr_id=pr.pr_id,
            repo=pr.repo,
            risk_tier=RiskTier.MEDIUM,
            repo_risk_class=RepoRiskClass.STANDARD,
            decision=Decision.HUMAN_REVIEW,
            agent_results=[
                AgentResult(
                    agent_name="security_privacy",
                    verdict=Verdict.WARN,
                    findings=[
                        Finding(
                            severity=Severity.HIGH,
                            certainty=Certainty.DETECTED,
                            category="authorization",
                            language="python",
                            file="src/auth.py",
                            line=17,
                            description="The new guard can be bypassed.",
                            quote=quote,
                        )
                    ],
                ),
                AgentResult(
                    agent_name="architecture",
                    verdict=Verdict.PASS,
                    status="skipped",
                    status_reason="no architecture context found",
                ),
            ],
        )

        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            review_id = await create_review_record(pr)
            async with factory() as session:
                reviews = _table_meta().tables["reviews"]
                await session.execute(
                    sa.update(reviews)
                    .where(reviews.c.id == review_id.hex)
                    .values(started_at=None)
                )
                await session.commit()
            await save_review_result(review_id, result)
            saved = await get_review(review_id)

        assert saved is not None
        agents = {a["agent_name"]: a for a in saved["agent_results"]}
        assert agents["security_privacy"]["status"] == "ran"
        assert agents["security_privacy"]["status_reason"] is None
        assert agents["security_privacy"]["findings"][0]["quote"] == quote
        assert agents["architecture"]["status"] == "skipped"
        assert agents["architecture"]["status_reason"] == "no architecture context found"
    finally:
        await engine.dispose()
