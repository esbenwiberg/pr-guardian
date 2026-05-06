"""Service layer: save review results and query for the dashboard."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pr_guardian.models.findings import AgentResult as AgentResultDomain
from pr_guardian.models.output import ReviewResult
from pr_guardian.models.pr import PlatformPR
from pr_guardian.persistence.database import async_session
from pr_guardian.persistence.models import (
    AdminRow,
    AgentResultRow,
    ApiKeyRow,
    FindingDismissalRow,
    FindingRow,
    GithubPatRow,
    GlobalConfigRow,
    MechanicalResultRow,
    PostedInlineCommentRow,
    PromptOverrideRow,
    ReviewRow,
    ScanAgentResultRow,
    ScanFindingRow,
    ScanIssueRow,
    ScanRow,
    SyncSourceRow,
    SyncedPRRow,
    UserIdentityRow,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

async def create_review_record(pr: PlatformPR, *, comment_mode: str = "none") -> uuid.UUID:
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
        comment_mode=comment_mode,
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


async def append_review_log_entry(review_id: uuid.UUID, entry: dict[str, Any]) -> bool:
    """Append a structured event onto a review's pipeline_log. Returns True on success."""
    async with async_session() as session:
        row = await session.get(ReviewRow, review_id)
        if not row:
            return False
        existing = list(row.pipeline_log or [])
        existing.append(entry)
        row.pipeline_log = existing
        await session.commit()
        return True


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
        row.trust_tier = result.trust_tier.value if result.trust_tier else ""
        row.trust_tier_details = {
            "reasons": result.trust_tier_reasons,
            "files": result.trust_tier_files,
            "reviewer_group_override": result.reviewer_group_override,
            "escalated_from": result.escalated_from,
        } if result.trust_tier else None
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
                agent_name=ar.agent_name[:64],
                verdict=ar.verdict.value[:16],
                languages_reviewed=ar.languages_reviewed,
                error=ar.error,
                verdict_explanation=ar.verdict_explanation,
            )
            session.add(ar_row)
            await session.flush()  # get the id

            for f in ar.findings:
                session.add(FindingRow(
                    agent_result_id=ar_row.id,
                    severity=f.severity.value[:16],
                    certainty=f.certainty.value[:16],
                    category=f.category[:128],
                    language=f.language[:32],
                    file=f.file,
                    line=f.line,
                    description=f.description,
                    suggestion=f.suggestion,
                    cwe=f.cwe[:32] if f.cwe else None,
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
    author: str | None = None,
) -> list[dict[str, Any]]:
    """List reviews with optional filters, newest first."""
    async with async_session() as session:
        q = select(ReviewRow).order_by(ReviewRow.started_at.desc())
        if repo:
            q = q.where(ReviewRow.repo == repo)
        if decision:
            q = q.where(ReviewRow.decision == decision)
        if author:
            q = q.where(ReviewRow.author == author)
        q = q.offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_review_to_dict(r) for r in rows]


async def find_review_by_pr_url(pr_url: str) -> dict[str, Any] | None:
    """Find the most recent completed review for a given PR URL."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(ReviewRow.pr_url == pr_url)
            .where(ReviewRow.finished_at.isnot(None))
            .order_by(ReviewRow.finished_at.desc())
        )
        row = (await session.scalars(q)).first()
        return _review_to_dict(row) if row else None


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
# Global config (dashboard settings)
# ---------------------------------------------------------------------------


async def get_global_config() -> dict[str, str]:
    """Return all global config key-value pairs (secrets are decrypted)."""
    from pr_guardian.persistence.crypto import SECRET_KEYS, decrypt

    try:
        async with async_session() as session:
            rows = (await session.scalars(select(GlobalConfigRow))).all()
            result: dict[str, str] = {}
            for r in rows:
                result[r.key] = decrypt(r.value) if r.key in SECRET_KEYS else r.value
            return result
    except Exception:
        return {}


async def set_global_config(key: str, value: str) -> None:
    """Create or update a global config entry (secrets are encrypted)."""
    from pr_guardian.persistence.crypto import SECRET_KEYS, encrypt

    stored = encrypt(value) if key in SECRET_KEYS else value

    async with async_session() as session:
        row = await session.get(GlobalConfigRow, key)
        if row:
            row.value = stored
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(GlobalConfigRow(key=key, value=stored))
        await session.commit()


async def delete_global_config(key: str) -> bool:
    """Delete a global config entry. Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(GlobalConfigRow, key)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# GitHub PAT management
# ---------------------------------------------------------------------------


def _pat_to_dict(row: GithubPatRow) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "token_prefix": row.token_prefix,
        "is_default": row.is_default,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def list_github_pats() -> list[dict]:
    """Return all configured GitHub PATs (token never included)."""
    try:
        async with async_session() as session:
            rows = (
                await session.scalars(select(GithubPatRow).order_by(GithubPatRow.created_at))
            ).all()
            return [_pat_to_dict(r) for r in rows]
    except Exception:
        log.warning("list_github_pats_failed", hint="DB unavailable; returning empty list")
        return []


async def create_github_pat(
    name: str,
    token: str,
    description: str = "",
    is_default: bool = False,
) -> dict:
    """Store a new named GitHub PAT (token is encrypted)."""
    from sqlalchemy import update as sa_update

    from pr_guardian.persistence.crypto import encrypt

    async with async_session() as session:
        if is_default:
            await session.execute(sa_update(GithubPatRow).values(is_default=False))
        row = GithubPatRow(
            name=name,
            description=description,
            encrypted_token=encrypt(token),
            token_prefix=token[:8] + "..." if len(token) > 8 else token,
            is_default=is_default,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _pat_to_dict(row)


async def update_github_pat(
    pat_id: uuid.UUID,
    *,
    name: str | None = None,
    token: str | None = None,
    description: str | None = None,
    is_default: bool | None = None,
) -> dict | None:
    """Update fields on an existing PAT. Returns updated dict or None if not found."""
    from sqlalchemy import update as sa_update

    from pr_guardian.persistence.crypto import encrypt

    async with async_session() as session:
        row = await session.get(GithubPatRow, pat_id)
        if not row:
            return None
        if is_default is True:
            await session.execute(
                sa_update(GithubPatRow)
                .where(GithubPatRow.id != pat_id)
                .values(is_default=False)
            )
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if token is not None:
            row.encrypted_token = encrypt(token)
            row.token_prefix = token[:8] + "..." if len(token) > 8 else token
        if is_default is not None:
            row.is_default = is_default
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        return _pat_to_dict(row)


async def delete_github_pat(pat_id: uuid.UUID) -> bool:
    """Delete a PAT by id. Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(GithubPatRow, pat_id)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def resolve_github_token(pat_name: str | None = None) -> str:
    """Resolve the GitHub token to use for a review.

    Priority: named PAT (if pat_name given) > default PAT in DB > GITHUB_TOKEN env var.

    When pat_name is explicitly provided, raises LookupError if no matching PAT is found.
    Falls back gracefully to GITHUB_TOKEN env var only when no pat_name is given and the DB
    has no default PAT configured (or the DB is unavailable).
    """
    import os

    from pr_guardian.persistence.crypto import decrypt

    try:
        async with async_session() as session:
            if pat_name:
                row = (
                    await session.scalars(
                        select(GithubPatRow).where(GithubPatRow.name == pat_name)
                    )
                ).first()
                if not row:
                    raise LookupError(f"GitHub PAT not found: {pat_name!r}")
                try:
                    decrypted = decrypt(row.encrypted_token)
                except Exception:
                    raise LookupError(f"GitHub PAT {pat_name!r} has a corrupted token")
                if not decrypted:
                    raise LookupError(f"GitHub PAT {pat_name!r} has a corrupted token")
                return decrypted
            else:
                row = (
                    await session.scalars(
                        select(GithubPatRow).where(GithubPatRow.is_default.is_(True))
                    )
                ).first()
                if row:
                    decrypted = decrypt(row.encrypted_token)
                    if decrypted:
                        return decrypted
    except LookupError:
        raise
    except Exception:
        log.warning("resolve_github_token_failed", hint="DB unavailable or decrypt error; falling back to env var")
    return os.environ.get("GITHUB_TOKEN", "")


# ---------------------------------------------------------------------------
# Finding dismissals (feedback loop)
# ---------------------------------------------------------------------------


def finding_signature(file: str, category: str, agent_name: str) -> str:
    """Stable hash that survives line-number shifts."""
    raw = f"{file}::{category}::{agent_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def upsert_dismissal(
    pr_id: str,
    repo: str,
    platform: str,
    finding: dict,
    agent_name: str,
    status: str,
    comment: str,
) -> uuid.UUID:
    """Create or update a dismissal. Computes signature from finding fields."""
    sig = finding_signature(finding["file"], finding["category"], agent_name)
    source = {
        "file": finding.get("file", ""),
        "line": finding.get("line"),
        "category": finding.get("category", ""),
        "agent_name": agent_name,
        "severity": finding.get("severity", ""),
        "certainty": finding.get("certainty", ""),
        "description": (finding.get("description", "") or "")[:500],
    }
    async with async_session() as session:
        # Check for existing active dismissal with same signature
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.signature == sig)
            .where(FindingDismissalRow.active.is_(True))
        )
        existing = (await session.scalars(q)).first()
        if existing:
            existing.status = status
            existing.comment = comment
            existing.source_finding = source
            existing.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return existing.id

        row = FindingDismissalRow(
            pr_id=pr_id,
            repo=repo,
            platform=platform,
            signature=sig,
            status=status,
            comment=comment,
            source_finding=source,
            active=True,
        )
        session.add(row)
        await session.commit()
        return row.id


async def remove_dismissal(dismissal_id: uuid.UUID) -> bool:
    """Delete a dismissal (un-dismiss). Returns True if deleted."""
    async with async_session() as session:
        row = await session.get(FindingDismissalRow, dismissal_id)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def get_active_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
) -> list[dict[str, Any]]:
    """All active dismissals for a PR."""
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(True))
        )
        rows = (await session.scalars(q)).all()
        return [_dismissal_to_dict(r) for r in rows]


async def get_archived_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
) -> list[dict[str, Any]]:
    """Inactive (archived) dismissals for a PR — findings resolved in later reviews."""
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(False))
            .order_by(FindingDismissalRow.updated_at.desc())
        )
        rows = (await session.scalars(q)).all()
        return [_dismissal_to_dict(r) for r in rows]


async def match_dismissals_to_findings(
    pr_id: str,
    repo: str,
    platform: str,
    findings_with_agent: list[dict],
) -> dict[str, dict]:
    """Returns {signature: dismissal_dict} for findings that match an active dismissal."""
    dismissals = await get_active_dismissals(pr_id, repo, platform)
    sig_map = {d["signature"]: d for d in dismissals}

    matched: dict[str, dict] = {}
    for f in findings_with_agent:
        sig = finding_signature(f["file"], f["category"], f["agent_name"])
        if sig in sig_map:
            matched[sig] = sig_map[sig]
    return matched


async def archive_stale_dismissals(
    pr_id: str,
    repo: str,
    platform: str,
    active_signatures: set[str],
) -> int:
    """Mark dismissals as inactive if their signature didn't appear in the latest review."""
    count = 0
    async with async_session() as session:
        q = (
            select(FindingDismissalRow)
            .where(FindingDismissalRow.repo == repo)
            .where(FindingDismissalRow.pr_id == pr_id)
            .where(FindingDismissalRow.platform == platform)
            .where(FindingDismissalRow.active.is_(True))
        )
        rows = (await session.scalars(q)).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.signature not in active_signatures:
                row.active = False
                row.updated_at = now
                count += 1
        if count:
            await session.commit()
    return count


def _dismissal_to_dict(row: FindingDismissalRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pr_id": row.pr_id,
        "repo": row.repo,
        "platform": row.platform,
        "signature": row.signature,
        "status": row.status,
        "comment": row.comment,
        "source_finding": row.source_finding,
        "active": row.active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Scan operations
# ---------------------------------------------------------------------------


async def create_scan_record(
    scan_type: str,
    repo: str,
    platform: str,
    time_window_days: int = 7,
    staleness_months: int = 6,
) -> uuid.UUID:
    """Insert a pending scan row when a scan starts."""
    row = ScanRow(
        scan_type=scan_type,
        repo=repo,
        platform=platform,
        time_window_days=time_window_days,
        staleness_months=staleness_months,
        stage="discovery",
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        log.debug("scan_record_created", scan_id=str(row.id), scan_type=scan_type)
        return row.id


async def update_scan_stage(scan_id: uuid.UUID, stage: str, detail: str = "") -> None:
    """Update the pipeline stage for live-progress tracking."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if row:
            row.stage = stage
            row.stage_detail = detail
            await session.commit()


async def mark_scan_failed(
    scan_id: uuid.UUID,
    error: str,
    pipeline_log: list[dict] | None = None,
) -> None:
    """Mark a scan as failed."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if row:
            now = datetime.now(timezone.utc)
            row.stage = "error"
            row.stage_detail = error[:500]
            row.finished_at = now
            if pipeline_log is not None:
                row.pipeline_log = pipeline_log
            if row.started_at:
                row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
            await session.commit()


async def save_scan_result(scan_id: uuid.UUID, result) -> None:
    """Persist the full scan result once the pipeline finishes.

    Accepts a ScanResult dataclass from models.scan.
    """
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if not row:
            log.warning("scan_row_not_found", scan_id=str(scan_id))
            return

        now = datetime.now(timezone.utc)
        row.total_findings = result.total_findings
        row.summary = result.summary
        row.pipeline_log = result.pipeline_log
        row.total_input_tokens = result.total_input_tokens
        row.total_output_tokens = result.total_output_tokens
        row.cost_usd = result.cost_usd
        row.stage = "complete"
        row.finished_at = now
        if row.started_at:
            row.duration_ms = int((now - row.started_at).total_seconds() * 1000)

        for ar in result.agent_results:
            ar_row = ScanAgentResultRow(
                scan_id=scan_id,
                agent_name=ar.agent_name,
                verdict=ar.verdict.value,
                summary=ar.summary,
                error=ar.error,
            )
            session.add(ar_row)
            await session.flush()

            for f in ar.findings:
                session.add(ScanFindingRow(
                    agent_result_id=ar_row.id,
                    severity=f.severity.value,
                    certainty=f.certainty.value,
                    category=f.category,
                    file=f.file,
                    line=f.line,
                    description=f.description,
                    suggestion=f.suggestion,
                    priority=f.priority,
                    last_modified=f.last_modified,
                    effort_estimate=f.effort_estimate,
                ))

        await session.commit()
        log.info("scan_result_saved", scan_id=str(scan_id), scan_type=result.scan_type.value)


async def get_scan(scan_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a single scan with all nested data."""
    async with async_session() as session:
        row = await session.get(ScanRow, scan_id)
        if not row:
            return None
        return _scan_to_dict(row)


async def list_scans(
    limit: int = 50,
    offset: int = 0,
    repo: str | None = None,
    scan_type: str | None = None,
) -> list[dict[str, Any]]:
    """List scans with optional filters, newest first."""
    async with async_session() as session:
        q = select(ScanRow).order_by(ScanRow.started_at.desc())
        if repo:
            q = q.where(ScanRow.repo == repo)
        if scan_type:
            q = q.where(ScanRow.scan_type == scan_type)
        q = q.offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_scan_to_dict(r) for r in rows]


async def get_scan_stats() -> dict[str, Any]:
    """Aggregate stats for scans."""
    async with async_session() as session:
        total = await session.scalar(select(func.count(ScanRow.id))) or 0

        type_counts: dict[str, int] = {}
        for st in ("recent_changes", "maintenance"):
            c = await session.scalar(
                select(func.count(ScanRow.id)).where(ScanRow.scan_type == st)
            )
            type_counts[st] = c or 0

        total_cost = await session.scalar(
            select(func.sum(ScanRow.cost_usd)).where(ScanRow.stage == "complete")
        )
        avg_cost = await session.scalar(
            select(func.avg(ScanRow.cost_usd)).where(ScanRow.stage == "complete")
        )

        severity_counts: dict[str, int] = {}
        for sev in ("low", "medium", "high", "critical"):
            c = await session.scalar(
                select(func.count(ScanFindingRow.id)).where(ScanFindingRow.severity == sev)
            )
            severity_counts[sev] = c or 0

        return {
            "total_scans": total,
            "type_counts": type_counts,
            "severity_counts": severity_counts,
            "total_cost_usd": round(total_cost, 4) if total_cost else 0.0,
            "avg_cost_usd": round(avg_cost, 4) if avg_cost else 0.0,
        }


async def create_scan_issue(
    scan_id: uuid.UUID,
    finding_ids: list[str],
    issue_url: str,
    issue_number: str,
    title: str,
    platform: str,
    repo: str,
) -> uuid.UUID:
    """Persist a platform issue that was created from scan findings."""
    row = ScanIssueRow(
        scan_id=scan_id,
        finding_ids=finding_ids,
        issue_url=issue_url,
        issue_number=str(issue_number),
        title=title,
        platform=platform,
        repo=repo,
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        return row.id


async def get_scan_issues(scan_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return all issues created for a given scan."""
    async with async_session() as session:
        q = select(ScanIssueRow).where(ScanIssueRow.scan_id == scan_id)
        rows = (await session.scalars(q)).all()
        return [
            {
                "id": str(r.id),
                "scan_id": str(r.scan_id),
                "finding_ids": r.finding_ids or [],
                "issue_url": r.issue_url,
                "issue_number": r.issue_number,
                "title": r.title,
                "platform": r.platform,
                "repo": r.repo,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def _scan_to_dict(row: ScanRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scan_type": row.scan_type,
        "repo": row.repo,
        "platform": row.platform,
        "time_window_days": row.time_window_days,
        "staleness_months": row.staleness_months,
        "total_findings": row.total_findings,
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
        "agent_results": [
            {
                "agent_name": a.agent_name,
                "verdict": a.verdict,
                "summary": a.summary,
                "error": a.error,
                "findings": [
                    {
                        "id": str(f.id),
                        "severity": f.severity,
                        "certainty": f.certainty,
                        "category": f.category,
                        "file": f.file,
                        "line": f.line,
                        "description": f.description,
                        "suggestion": f.suggestion,
                        "priority": f.priority,
                        "last_modified": f.last_modified,
                        "effort_estimate": f.effort_estimate,
                    }
                    for f in a.findings
                ],
            }
            for a in row.agent_results
        ],
    }


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
        "trust_tier": row.trust_tier,
        "trust_tier_details": row.trust_tier_details,
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
                "verdict_explanation": a.verdict_explanation,
                "findings": [
                    {
                        "id": str(f.id),
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


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------


async def is_admin(email: str) -> bool:
    """Check whether an email is in the admin list."""
    async with async_session() as session:
        row = await session.get(AdminRow, email.lower())
        return row is not None


async def list_admins() -> list[dict[str, Any]]:
    """Return all admin records."""
    async with async_session() as session:
        rows = (await session.scalars(
            select(AdminRow).order_by(AdminRow.created_at)
        )).all()
        return [
            {"email": r.email, "added_by": r.added_by, "created_at": r.created_at.isoformat()}
            for r in rows
        ]


async def add_admin(email: str, added_by: str = "system") -> bool:
    """Add an admin. Returns False if already exists."""
    email = email.lower().strip()
    async with async_session() as session:
        existing = await session.get(AdminRow, email)
        if existing:
            return False
        session.add(AdminRow(email=email, added_by=added_by))
        await session.commit()
        return True


async def remove_admin(email: str) -> bool:
    """Remove an admin. Returns False if not found."""
    email = email.lower().strip()
    async with async_session() as session:
        row = await session.get(AdminRow, email)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def admin_count() -> int:
    """Return total number of admins."""
    async with async_session() as session:
        return await session.scalar(select(func.count()).select_from(AdminRow)) or 0


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(
    name: str,
    scopes: list[str],
    created_by: str,
    expires_in_days: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate an API key, store the hash, return (full_key, metadata).

    The full key is only returned once — it is never stored.
    """
    raw_key = "prg_" + secrets.token_hex(16)
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:8]
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days
        else None
    )

    row = ApiKeyRow(
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=scopes,
        created_by=created_by,
        expires_at=expires_at,
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()
        return raw_key, _api_key_to_dict(row)


async def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """Validate a raw API key. Returns key metadata or None if invalid.

    Updates last_used_at on success.
    """
    key_hash = _hash_key(raw_key)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        row = await session.scalar(
            select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash)
        )
        if not row:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at < now:
            return None
        row.last_used_at = now
        await session.commit()
        return _api_key_to_dict(row)


async def list_api_keys() -> list[dict[str, Any]]:
    """List all API keys (hash never exposed)."""
    async with async_session() as session:
        rows = (await session.scalars(
            select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())
        )).all()
        return [_api_key_to_dict(r) for r in rows]


async def revoke_api_key(key_id: uuid.UUID) -> bool:
    """Revoke an API key. Returns False if not found."""
    async with async_session() as session:
        row = await session.get(ApiKeyRow, key_id)
        if not row:
            return False
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        return True


async def delete_api_key(key_id: uuid.UUID) -> bool:
    """Permanently delete an API key. Returns False if not found."""
    async with async_session() as session:
        row = await session.get(ApiKeyRow, key_id)
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


def _api_key_to_dict(row: ApiKeyRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "key_prefix": row.key_prefix,
        "scopes": row.scopes,
        "created_by": row.created_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "created_at": row.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Inline comment helpers
# ---------------------------------------------------------------------------


async def save_inline_comment_ids(
    review_id: uuid.UUID,
    ids: list[str],
    platform: str,
    pr_id: str,
    repo: str,
) -> None:
    """Persist platform-native comment IDs for a review."""
    async with async_session() as session:
        for comment_id in ids:
            session.add(PostedInlineCommentRow(
                review_id=review_id,
                platform_comment_id=comment_id,
                platform=platform,
                pr_id=pr_id,
                repo=repo,
            ))
        await session.commit()


async def load_inline_comment_ids(review_id: uuid.UUID) -> list[str]:
    """Return all platform comment IDs previously saved for a review."""
    async with async_session() as session:
        rows = (await session.scalars(
            select(PostedInlineCommentRow).where(
                PostedInlineCommentRow.review_id == review_id
            )
        )).all()
        return [r.platform_comment_id for r in rows]


# ---------------------------------------------------------------------------
# PR Dashboard: user identity, sync sources, cached open PRs
# ---------------------------------------------------------------------------

_STALE_DAYS = 5


async def get_user_identity(email: str) -> dict[str, Any] | None:
    """Return the GitHub handle + ADO UPN for a user, or None if not configured."""
    async with async_session() as session:
        row = await session.get(UserIdentityRow, email.lower())
        if not row:
            return None
        return {
            "email": row.email,
            "github_handle": row.github_handle,
            "ado_upn": row.ado_upn,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


async def upsert_user_identity(
    email: str,
    github_handle: str | None,
    ado_upn: str | None,
) -> None:
    """Create or update the identity mapping for a user."""
    email = email.lower().strip()
    async with async_session() as session:
        row = await session.get(UserIdentityRow, email)
        if row:
            row.github_handle = github_handle or None
            row.ado_upn = ado_upn or None
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(UserIdentityRow(
                email=email,
                github_handle=github_handle or None,
                ado_upn=ado_upn or None,
            ))
        await session.commit()


async def upsert_sync_source(
    platform: str,
    org: str,
    project: str,
    repo: str,
    repo_url: str,
) -> None:
    """Register a repo as an active sync source."""
    async with async_session() as session:
        q = (
            select(SyncSourceRow)
            .where(SyncSourceRow.platform == platform)
            .where(SyncSourceRow.repo == repo)
            .where(SyncSourceRow.project == project)
        )
        row = (await session.scalars(q)).first()
        if row:
            row.is_active = True
            row.org = org
            row.repo_url = repo_url
        else:
            session.add(SyncSourceRow(
                platform=platform,
                org=org,
                project=project,
                repo=repo,
                repo_url=repo_url,
                is_active=True,
            ))
        await session.commit()


async def mark_sync_source_synced(platform: str, repo: str, project: str = "") -> None:
    """Update last_synced_at for a sync source."""
    async with async_session() as session:
        q = (
            select(SyncSourceRow)
            .where(SyncSourceRow.platform == platform)
            .where(SyncSourceRow.repo == repo)
            .where(SyncSourceRow.project == project)
        )
        row = (await session.scalars(q)).first()
        if row:
            row.last_synced_at = datetime.now(timezone.utc)
            await session.commit()


async def upsert_synced_pr(data: dict[str, Any]) -> None:
    """Create or update a cached PR record."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.now(timezone.utc)
    pr_created_at = _parse_dt(data.get("pr_created_at"))
    pr_updated_at = _parse_dt(data.get("pr_updated_at"))

    values = {
        "platform": data["platform"],
        "pr_id": str(data["pr_id"]),
        "org": data.get("org", ""),
        "project": data.get("project", ""),
        "repo": data.get("repo", ""),
        "title": data.get("title", ""),
        "author": data.get("author", ""),
        "author_display": data.get("author_display") or data.get("author", ""),
        "pr_url": data.get("pr_url", ""),
        "source_branch": data.get("source_branch", ""),
        "target_branch": data.get("target_branch", ""),
        "is_draft": bool(data.get("is_draft", False)),
        "has_conflicts": bool(data.get("has_conflicts", False)),
        "approval_status": data.get("approval_status", "pending"),
        "reviewers": data.get("reviewers") or [],
        "comment_count": int(data.get("comment_count", 0)),
        "pr_created_at": pr_created_at,
        "pr_updated_at": pr_updated_at,
        "synced_at": now,
    }

    async with async_session() as session:
        stmt = pg_insert(SyncedPRRow).values(id=uuid.uuid4(), **values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_synced_pr",
            set_={k: v for k, v in values.items() if k not in ("platform", "pr_id", "repo", "project")},
        )
        await session.execute(stmt)
        await session.commit()


async def delete_closed_prs(platform: str, repo: str, project: str, open_pr_ids: list[str]) -> None:
    """Remove PRs that are no longer open in the given repo."""
    from sqlalchemy import delete

    async with async_session() as session:
        q = (
            delete(SyncedPRRow)
            .where(SyncedPRRow.platform == platform)
            .where(SyncedPRRow.repo == repo)
            .where(SyncedPRRow.project == project)
        )
        if open_pr_ids:
            q = q.where(SyncedPRRow.pr_id.notin_(open_pr_ids))
        await session.execute(q)
        await session.commit()


async def get_synced_pr(pr_uuid: str) -> dict[str, Any] | None:
    """Fetch a single synced PR by its UUID."""
    async with async_session() as session:
        try:
            row = await session.get(SyncedPRRow, uuid.UUID(pr_uuid))
        except ValueError:
            return None
        if not row:
            return None
        return _synced_pr_to_dict(row)


async def list_synced_prs(
    *,
    view: str | None = None,
    github_handle: str | None = None,
    ado_upn: str | None = None,
    platform: str | None = None,
    org: str | None = None,
    repo: str | None = None,
    author: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """List cached open PRs with filters. Returns (items, total_count)."""
    import json
    from sqlalchemy import cast, or_
    from sqlalchemy.dialects.postgresql import JSONB

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
    user_handles = [h for h in [github_handle, ado_upn] if h]

    async with async_session() as session:
        q = select(SyncedPRRow)

        # View filters
        if view == "mine" and user_handles:
            q = q.where(SyncedPRRow.author.in_(user_handles))
        elif view == "queue" and user_handles:
            conditions = [
                SyncedPRRow.reviewers.op("@>")(cast(json.dumps([h]), JSONB))
                for h in user_handles
            ]
            q = q.where(or_(*conditions))
        elif view == "stale":
            q = q.where(SyncedPRRow.pr_updated_at < stale_cutoff)

        # Extra filters
        if platform:
            q = q.where(SyncedPRRow.platform == platform)
        if org:
            q = q.where(SyncedPRRow.org == org)
        if repo:
            q = q.where(SyncedPRRow.repo == repo)
        if author:
            q = q.where(SyncedPRRow.author == author)
        if search:
            q = q.where(SyncedPRRow.title.ilike(f"%{search}%"))

        total = await session.scalar(select(func.count()).select_from(q.subquery()))
        q = q.order_by(SyncedPRRow.pr_updated_at.desc().nullslast()).offset(offset).limit(limit)
        rows = (await session.scalars(q)).all()
        return [_synced_pr_to_dict(r) for r in rows], int(total or 0)


async def get_pr_dashboard_summary(
    github_handle: str | None = None,
    ado_upn: str | None = None,
) -> dict[str, Any]:
    """Compute counts for the 4 dashboard summary cards."""
    import json
    from sqlalchemy import cast, or_
    from sqlalchemy.dialects.postgresql import JSONB

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
    user_handles = [h for h in [github_handle, ado_upn] if h]

    async with async_session() as session:
        total_open = await session.scalar(select(func.count(SyncedPRRow.id))) or 0

        if user_handles:
            mine_q = select(func.count(SyncedPRRow.id)).where(
                SyncedPRRow.author.in_(user_handles)
            )
            mine_total = await session.scalar(mine_q) or 0

            attention_q = select(func.count(SyncedPRRow.id)).where(
                SyncedPRRow.author.in_(user_handles)
            ).where(
                or_(
                    SyncedPRRow.approval_status == "changes_requested",
                    SyncedPRRow.pr_updated_at < stale_cutoff,
                    SyncedPRRow.approval_status == "approved",
                )
            )
            mine_attention = await session.scalar(attention_q) or 0

            queue_conditions = [
                SyncedPRRow.reviewers.op("@>")(cast(json.dumps([h]), JSONB))
                for h in user_handles
            ]
            queue_q = select(func.count(SyncedPRRow.id)).where(or_(*queue_conditions))
            queue_total = await session.scalar(queue_q) or 0
        else:
            mine_total = mine_attention = queue_total = 0

        stale_total = await session.scalar(
            select(func.count(SyncedPRRow.id)).where(
                SyncedPRRow.pr_updated_at < stale_cutoff
            )
        ) or 0

        repo_count = await session.scalar(
            select(func.count(func.distinct(SyncedPRRow.repo)))
        ) or 0

        oldest_stale = await session.scalar(
            select(func.min(SyncedPRRow.pr_updated_at)).where(
                SyncedPRRow.pr_updated_at < stale_cutoff
            )
        )
        oldest_days = None
        if oldest_stale:
            oldest_days = (datetime.now(timezone.utc) - oldest_stale).days

        return {
            "mine": {"total": mine_total, "needs_attention": mine_attention},
            "queue": {"total": queue_total},
            "stale": {"total": stale_total, "oldest_days": oldest_days},
            "all": {"total": total_open, "repo_count": repo_count},
        }


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _synced_pr_to_dict(row: SyncedPRRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "platform": row.platform,
        "pr_id": row.pr_id,
        "org": row.org,
        "project": row.project,
        "repo": row.repo,
        "title": row.title,
        "author": row.author,
        "author_display": row.author_display,
        "pr_url": row.pr_url,
        "source_branch": row.source_branch,
        "target_branch": row.target_branch,
        "is_draft": row.is_draft,
        "has_conflicts": row.has_conflicts,
        "approval_status": row.approval_status,
        "reviewers": row.reviewers or [],
        "comment_count": row.comment_count,
        "pr_created_at": row.pr_created_at.isoformat() if row.pr_created_at else None,
        "pr_updated_at": row.pr_updated_at.isoformat() if row.pr_updated_at else None,
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }
