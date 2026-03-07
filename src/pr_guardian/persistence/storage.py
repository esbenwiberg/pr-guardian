"""Service layer: save review results and query for the dashboard."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pr_guardian.models.findings import AgentResult as AgentResultDomain
from pr_guardian.models.output import ReviewResult
from pr_guardian.models.pr import PlatformPR
from pr_guardian.persistence.database import async_session
from pr_guardian.persistence.models import (
    AgentResultRow,
    FindingRow,
    MechanicalResultRow,
    PromptOverrideRow,
    ReviewRow,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

async def create_review_record(pr: PlatformPR) -> uuid.UUID:
    """Insert a pending review row when a review starts. Returns the row id."""
    row = ReviewRow(
        pr_id=pr.pr_id,
        repo=pr.repo,
        platform=pr.platform.value,
        author=pr.author,
        title=pr.title,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        head_commit_sha=pr.head_commit_sha,
        pr_url=pr.pr_url,
        stage="discovery",
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        log.debug("review_record_created", review_id=str(row.id), pr_id=pr.pr_id)
        return row.id


async def update_review_stage(review_id: uuid.UUID, stage: str, detail: str = "") -> None:
    """Update the pipeline stage for live-progress tracking."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if row:
            row.stage = stage
            row.stage_detail = detail
            await session.commit()


async def mark_review_failed(
    review_id: uuid.UUID,
    error: str,
    pipeline_log: list[dict] | None = None,
) -> None:
    """Mark a review as failed so it no longer appears as active."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if row:
            now = datetime.now(timezone.utc)
            row.stage = "error"
            row.stage_detail = error[:500]
            row.decision = "error"
            row.finished_at = now
            if pipeline_log is not None:
                row.pipeline_log = pipeline_log
            if row.started_at:
                row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
            await session.commit()


async def save_review_result(review_id: uuid.UUID, result: ReviewResult) -> None:
    """Persist the full review result once the pipeline finishes."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            log.warning("review_row_not_found", review_id=str(review_id))
            return

        now = datetime.now(timezone.utc)
        row.risk_tier = result.risk_tier.value
        row.repo_risk_class = result.repo_risk_class.value
        row.combined_score = result.combined_score
        row.decision = result.decision.value
        row.mechanical_passed = result.mechanical_passed
        row.override_reasons = result.override_reasons
        row.summary = result.summary
        row.pipeline_log = result.pipeline_log
        row.total_input_tokens = result.total_input_tokens
        row.total_output_tokens = result.total_output_tokens
        row.cost_usd = result.cost_usd
        row.stage = "complete"
        row.finished_at = now
        if row.started_at:
            row.duration_ms = int((now - row.started_at).total_seconds() * 1000)

        # Mechanical results
        for mech in result.mechanical_results:
            session.add(MechanicalResultRow(
                review_id=review_id,
                tool=mech.tool,
                passed=mech.passed,
                severity=mech.severity,
                findings=mech.findings,
                error=mech.error,
            ))

        # Agent results + findings
        for ar in result.agent_results:
            ar_row = AgentResultRow(
                review_id=review_id,
                agent_name=ar.agent_name,
                verdict=ar.verdict.value,
                languages_reviewed=ar.languages_reviewed,
                error=ar.error,
            )
            session.add(ar_row)
            await session.flush()  # get the id

            for f in ar.findings:
                session.add(FindingRow(
                    agent_result_id=ar_row.id,
                    severity=f.severity.value,
                    certainty=f.certainty.value,
                    category=f.category,
                    language=f.language,
                    file=f.file,
                    line=f.line,
                    description=f.description,
                    suggestion=f.suggestion,
                    cwe=f.cwe,
                ))

        await session.commit()
        log.info("review_result_saved", review_id=str(review_id), decision=result.decision.value)


# ---------------------------------------------------------------------------
# Read operations (dashboard queries)
# ---------------------------------------------------------------------------

async def get_review(review_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a single review with all nested data."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            return None
        return _review_to_dict(row)


async def list_reviews(
    limit: int = 50,
    offset: int = 0,
    repo: str | None = None,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """List reviews with optional filters, newest first."""
    async with async_session() as session:
        q = select(ReviewRow).order_by(ReviewRow.started_at.desc())
        if repo:
            q = q.where(ReviewRow.repo == repo)
        if decision:
            q = q.where(ReviewRow.decision == decision)
        q = q.offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_review_to_dict(r) for r in rows]


async def get_active_reviews() -> list[dict[str, Any]]:
    """Get reviews that haven't finished yet (live progress)."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(ReviewRow.finished_at.is_(None))
            .order_by(ReviewRow.started_at.desc())
        )
        rows = (await session.scalars(q)).all()
        return [_review_to_dict(r) for r in rows]


async def get_stats() -> dict[str, Any]:
    """Aggregate stats for the dashboard."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(ReviewRow.id)))

        decision_counts: dict[str, int] = {}
        for decision_val in ("auto_approve", "human_review", "reject", "hard_block"):
            c = await session.scalar(
                select(func.count(ReviewRow.id)).where(ReviewRow.decision == decision_val)
            )
            decision_counts[decision_val] = c or 0

        risk_tier_counts: dict[str, int] = {}
        for tier in ("trivial", "low", "medium", "high"):
            c = await session.scalar(
                select(func.count(ReviewRow.id)).where(ReviewRow.risk_tier == tier)
            )
            risk_tier_counts[tier] = c or 0

        avg_score = await session.scalar(
            select(func.avg(ReviewRow.combined_score)).where(ReviewRow.decision != "pending")
        )
        avg_duration = await session.scalar(
            select(func.avg(ReviewRow.duration_ms)).where(ReviewRow.duration_ms.isnot(None))
        )
        avg_cost = await session.scalar(
            select(func.avg(ReviewRow.cost_usd)).where(ReviewRow.decision != "pending")
        )
        total_cost = await session.scalar(
            select(func.sum(ReviewRow.cost_usd)).where(ReviewRow.decision != "pending")
        )

        # Top repos by review count
        top_repos_q = (
            select(ReviewRow.repo, func.count(ReviewRow.id).label("cnt"))
            .group_by(ReviewRow.repo)
            .order_by(func.count(ReviewRow.id).desc())
            .limit(10)
        )
        top_repos = (await session.execute(top_repos_q)).all()

        # Finding severity distribution
        severity_counts: dict[str, int] = {}
        for sev in ("low", "medium", "high", "critical"):
            c = await session.scalar(
                select(func.count(FindingRow.id)).where(FindingRow.severity == sev)
            )
            severity_counts[sev] = c or 0

        pending = await session.scalar(
            select(func.count(ReviewRow.id)).where(ReviewRow.finished_at.is_(None))
        )

        return {
            "total_reviews": total or 0,
            "active_reviews": pending or 0,
            "decision_counts": decision_counts,
            "risk_tier_counts": risk_tier_counts,
            "severity_counts": severity_counts,
            "avg_score": round(avg_score, 2) if avg_score else 0.0,
            "avg_duration_ms": int(avg_duration) if avg_duration else 0,
            "avg_cost_usd": round(avg_cost, 4) if avg_cost else 0.0,
            "total_cost_usd": round(total_cost, 4) if total_cost else 0.0,
            "top_repos": [{"repo": r[0], "count": r[1]} for r in top_repos],
        }


# ---------------------------------------------------------------------------
# Prompt overrides
# ---------------------------------------------------------------------------

async def get_prompt_override(agent_name: str) -> str | None:
    """Return the override content for an agent, or None if no override exists."""
    try:
        async with async_session() as session:
            row = await session.get(PromptOverrideRow, agent_name)
            return row.content if row else None
    except Exception:
        return None


# Known agent names — used as fallback when prompts dir is missing (e.g. in Docker)
_KNOWN_AGENTS = [
    "architecture_intent",
    "code_quality_observability",
    "hotspot",
    "performance",
    "security_privacy",
    "test_quality",
]


async def get_all_prompts() -> list[dict[str, Any]]:
    """Return all agent prompts with override status and file defaults."""
    from pr_guardian.agents.prompt_composer import PROMPTS_DIR, load_prompt

    # Discover agents from prompt files, fall back to known list
    discovered = sorted(p.parent.name for p in PROMPTS_DIR.glob("*/base.md"))
    agents = discovered or _KNOWN_AGENTS

    overrides: dict[str, PromptOverrideRow] = {}
    try:
        async with async_session() as session:
            overrides = {
                r.agent_name: r
                for r in (await session.scalars(select(PromptOverrideRow))).all()
            }
    except Exception:
        log.warning("prompt_overrides_table_missing")

    result = []
    for name in agents:
        default_content = load_prompt(f"{name}/base.md") or ""
        ovr = overrides.get(name)
        result.append({
            "agent_name": name,
            "content": ovr.content if ovr else default_content,
            "default_content": default_content,
            "is_override": ovr is not None,
            "updated_at": ovr.updated_at.isoformat() if ovr else None,
        })
    return result


async def set_prompt_override(agent_name: str, content: str) -> None:
    """Create or update a prompt override for an agent."""
    async with async_session() as session:
        row = await session.get(PromptOverrideRow, agent_name)
        if row:
            row.content = content
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(PromptOverrideRow(agent_name=agent_name, content=content))
        await session.commit()


async def delete_prompt_override(agent_name: str) -> bool:
    """Delete a prompt override, reverting to the file default. Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(PromptOverrideRow, agent_name)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _review_to_dict(row: ReviewRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pr_id": row.pr_id,
        "repo": row.repo,
        "platform": row.platform,
        "author": row.author,
        "title": row.title,
        "source_branch": row.source_branch,
        "target_branch": row.target_branch,
        "head_commit_sha": row.head_commit_sha,
        "pr_url": row.pr_url,
        "risk_tier": row.risk_tier,
        "repo_risk_class": row.repo_risk_class,
        "combined_score": row.combined_score,
        "decision": row.decision,
        "mechanical_passed": row.mechanical_passed,
        "override_reasons": row.override_reasons,
        "summary": row.summary,
        "stage": row.stage,
        "stage_detail": row.stage_detail,
        "pipeline_log": row.pipeline_log or [],
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "cost_usd": row.cost_usd,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": row.duration_ms,
        "mechanical_results": [
            {
                "tool": m.tool,
                "passed": m.passed,
                "severity": m.severity,
                "findings": m.findings,
                "error": m.error,
            }
            for m in row.mechanical_results
        ],
        "agent_results": [
            {
                "agent_name": a.agent_name,
                "verdict": a.verdict,
                "languages_reviewed": a.languages_reviewed,
                "error": a.error,
                "findings": [
                    {
                        "severity": f.severity,
                        "certainty": f.certainty,
                        "category": f.category,
                        "language": f.language,
                        "file": f.file,
                        "line": f.line,
                        "description": f.description,
                        "suggestion": f.suggestion,
                        "cwe": f.cwe,
                    }
                    for f in a.findings
                ],
            }
            for a in row.agent_results
        ],
    }
