"""Nuke-and-seed demo data for the agent/validator sandbox.

Drops all reviews, scans, dismissals, admins, api_keys, prompt_overrides,
global_config — then inserts a realistic mix so the dashboard renders
non-empty pages for browser-based validation.

Invoked from scripts/agent-serve.sh before uvicorn starts. Requires
DATABASE_URL to be set. Safe to run repeatedly; each run starts from a
clean slate.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from pr_guardian.persistence.database import _get_engine, async_session, close_db, init_db
from pr_guardian.persistence.models import (
    AdminRow,
    AgentResultRow,
    ApiKeyRow,
    Base,
    FindingDismissalRow,
    FindingRow,
    GlobalConfigRow,
    MechanicalResultRow,
    PostedInlineCommentRow,
    PromptOverrideRow,
    ReviewRow,
    ScanAgentResultRow,
    ScanFindingRow,
    ScanRow,
    SyncedPRRow,
    SyncSourceRow,
    UserIdentityRow,
)

DEV_ADMIN_EMAIL = "ewi@projectum.com"

NOW = datetime.now(timezone.utc)


def _ago(minutes: int = 0, hours: int = 0, days: int = 0) -> datetime:
    return NOW - timedelta(minutes=minutes, hours=hours, days=days)


async def _wipe() -> None:
    async with async_session() as s:
        # Order matters only for non-cascading tables — cascades handle the rest.
        for model in (
            FindingDismissalRow,
            ScanRow,
            PostedInlineCommentRow,
            ReviewRow,
            PromptOverrideRow,
            GlobalConfigRow,
            ApiKeyRow,
            AdminRow,
            SyncedPRRow,
            SyncSourceRow,
            UserIdentityRow,
        ):
            await s.execute(delete(model))
        await s.commit()


def _review(
    *,
    repo: str,
    pr_id: str,
    author: str,
    title: str,
    decision: str,
    risk_tier: str,
    score: float,
    started: datetime,
    finished: datetime | None,
    stage: str = "complete",
    stage_detail: str = "",
    summary: str = "",
    cost: float = 0.0,
    mechanical_passed: bool = True,
    comment_mode: str = "none",
) -> ReviewRow:
    duration_ms = (
        int((finished - started).total_seconds() * 1000) if finished else None
    )
    return ReviewRow(
        id=uuid.uuid4(),
        pr_id=pr_id,
        repo=repo,
        platform="github",
        author=author,
        title=title,
        source_branch=f"feat/{pr_id}",
        target_branch="main",
        head_commit_sha=uuid.uuid4().hex[:40],
        pr_url=f"https://github.com/{repo}/pull/{pr_id}",
        risk_tier=risk_tier,
        repo_risk_class="standard",
        trust_tier="",
        combined_score=score,
        decision=decision,
        mechanical_passed=mechanical_passed,
        override_reasons=[],
        summary=summary,
        stage=stage,
        stage_detail=stage_detail,
        pipeline_log=[],
        total_input_tokens=1200,
        total_output_tokens=400,
        cost_usd=cost,
        started_at=started,
        finished_at=finished,
        duration_ms=duration_ms,
        comment_mode=comment_mode,
    )


def _agent(
    review_id: uuid.UUID,
    name: str,
    verdict: str,
    languages: list[str],
    explanation: str = "",
) -> AgentResultRow:
    return AgentResultRow(
        id=uuid.uuid4(),
        review_id=review_id,
        agent_name=name,
        verdict=verdict,
        languages_reviewed=languages,
        verdict_explanation=explanation,
    )


def _finding(
    agent_result_id: uuid.UUID,
    *,
    severity: str,
    certainty: str,
    category: str,
    file: str,
    line: int | None,
    description: str,
    suggestion: str = "",
    language: str = "python",
    cwe: str | None = None,
) -> FindingRow:
    return FindingRow(
        id=uuid.uuid4(),
        agent_result_id=agent_result_id,
        severity=severity,
        certainty=certainty,
        category=category,
        language=language,
        file=file,
        line=line,
        description=description,
        suggestion=suggestion,
        cwe=cwe,
    )


async def _seed_reviews() -> None:
    rows: list = []

    # 1. Clean auto-approve (typo fix)
    r1 = _review(
        repo="esbenwiberg/pr-guardian", pr_id="101", author="esbenwiberg",
        title="Fix typo in README", decision="auto_approve", risk_tier="trivial",
        score=0.2, started=_ago(hours=26), finished=_ago(hours=26, minutes=-3),
        summary="Trivial doc change — auto-approved.", cost=0.0014,
    )
    rows.append(r1)
    a1 = _agent(r1.id, "code_quality_observability", "pass", ["markdown"],
                "No issues; documentation-only change.")
    rows.append(a1)

    # 2. Human review — a couple of medium findings
    r2 = _review(
        repo="esbenwiberg/pr-guardian", pr_id="117", author="alice",
        title="Add retry logic to LLM client", decision="human_review",
        risk_tier="medium", score=5.8, started=_ago(hours=4),
        finished=_ago(hours=4, minutes=-7),
        summary="Retry semantics need a closer look — bounded backoff missing.",
        cost=0.0321,
    )
    rows.append(r2)
    a2a = _agent(r2.id, "code_quality_observability", "findings", ["python"],
                 "Retry loop lacks jitter; logging at warn level would help.")
    rows.append(a2a)
    rows.append(_finding(a2a.id, severity="medium", certainty="high",
                         category="reliability", file="src/pr_guardian/llm/client.py",
                         line=142, description="Unbounded retry on 5xx without backoff jitter.",
                         suggestion="Use exponential backoff with jitter (full-jitter)."))
    a2b = _agent(r2.id, "performance", "findings", ["python"])
    rows.append(a2b)
    rows.append(_finding(a2b.id, severity="low", certainty="medium",
                         category="performance", file="src/pr_guardian/llm/client.py",
                         line=180, description="Per-call httpx client instantiation is wasteful.",
                         suggestion="Reuse a module-level AsyncClient."))

    # 3. Reject — critical security
    r3 = _review(
        repo="esbenwiberg/orcha", pr_id="42", author="bob",
        title="Add admin bypass for debug header", decision="reject",
        risk_tier="high", score=9.2, started=_ago(days=1, hours=2),
        finished=_ago(days=1, hours=2, minutes=-11),
        summary="Intentional auth bypass on a trusted header — blocked.",
        cost=0.0604, mechanical_passed=True,
    )
    rows.append(r3)
    a3a = _agent(r3.id, "security_privacy", "findings", ["python"],
                 "Critical: trusted-header admin override is an auth bypass.")
    rows.append(a3a)
    rows.append(_finding(a3a.id, severity="critical", certainty="high",
                         category="auth_bypass", file="src/orcha/auth/middleware.py",
                         line=58, description="Admin granted when X-Debug-Admin header present.",
                         suggestion="Remove the bypass. Debug auth must go through same path.",
                         cwe="CWE-287"))
    rows.append(_finding(a3a.id, severity="high", certainty="high",
                         category="logging", file="src/orcha/auth/middleware.py",
                         line=60, description="No audit log when admin bypass fires.",
                         suggestion="Emit structured audit event on every admin path.",
                         cwe="CWE-778"))
    a3b = _agent(r3.id, "architecture_intent", "findings", ["python"])
    rows.append(a3b)
    rows.append(_finding(a3b.id, severity="medium", certainty="medium",
                         category="architecture", file="src/orcha/auth/middleware.py",
                         line=55, description="Auth decisions leaking into request parsing layer."))

    # 4. Hard block — mechanical failure (secret detected)
    r4 = _review(
        repo="esbenwiberg/orcha", pr_id="55", author="alice",
        title="Update CI config", decision="hard_block", risk_tier="high",
        score=10.0, started=_ago(hours=8), finished=_ago(hours=8, minutes=-1),
        summary="Gitleaks detected an AWS access key. Hard blocked.",
        cost=0.0021, mechanical_passed=False,
    )
    rows.append(r4)
    m4 = MechanicalResultRow(
        id=uuid.uuid4(), review_id=r4.id, tool="gitleaks", passed=False,
        severity="critical",
        findings=[{
            "rule": "aws-access-token",
            "file": ".github/workflows/deploy.yml",
            "line": 14,
            "match": "AKIA****REDACTED****",
        }],
    )
    rows.append(m4)

    # 5. In-progress — no finished_at
    r5 = _review(
        repo="esbenwiberg/pr-guardian", pr_id="123", author="esbenwiberg",
        title="Refactor triage classifier", decision="pending",
        risk_tier="", score=0.0, started=_ago(minutes=2), finished=None,
        stage="agent_review", stage_detail="security_privacy running…",
        summary="", cost=0.0,
    )
    rows.append(r5)

    # 6. Older auto-approve — test tweak
    r6 = _review(
        repo="esbenwiberg/pr-guardian", pr_id="88", author="alice",
        title="Tighten test assertions in triage", decision="auto_approve",
        risk_tier="low", score=1.4, started=_ago(days=3),
        finished=_ago(days=3, minutes=-5),
        summary="Low-risk test change — auto-approved.", cost=0.0098,
    )
    rows.append(r6)
    a6 = _agent(r6.id, "test_quality", "pass", ["python"],
                "Assertions now cover previously-unchecked paths.")
    rows.append(a6)

    # 7. Inline-comment review — demonstrates PostedInlineCommentRow wiring
    r7 = _review(
        repo="esbenwiberg/pr-guardian", pr_id="130", author="bob",
        title="Add rate limiting to API endpoints", decision="human_review",
        risk_tier="medium", score=6.1, started=_ago(hours=1),
        finished=_ago(hours=1, minutes=-9),
        summary="Rate limiting implementation has two gaps that need review.",
        cost=0.0287, comment_mode="inline",
    )
    rows.append(r7)
    a7 = _agent(r7.id, "security_privacy", "findings", ["python"],
                "Missing rate limit on unauthenticated endpoints; key leakage risk.")
    rows.append(a7)
    rows.append(_finding(a7.id, severity="high", certainty="high",
                         category="rate_limiting",
                         file="src/pr_guardian/api/review.py", line=58,
                         description="POST /api/review has no rate limit; trivially abusable by unauthenticated callers.",
                         suggestion="Apply a per-IP rate limiter (e.g. slowapi) to this endpoint."))
    rows.append(_finding(a7.id, severity="medium", certainty="medium",
                         category="info_disclosure",
                         file="src/pr_guardian/api/review.py", line=71,
                         description="Error message from _hydrate_pr leaks internal exception detail to the caller.",
                         suggestion="Return a generic 422 message; log the full error server-side only."))
    rows.append(PostedInlineCommentRow(
        id=uuid.uuid4(), review_id=r7.id,
        platform_comment_id="gh-comment-001", platform="github",
        pr_id="130", repo="esbenwiberg/pr-guardian",
    ))
    rows.append(PostedInlineCommentRow(
        id=uuid.uuid4(), review_id=r7.id,
        platform_comment_id="gh-comment-002", platform="github",
        pr_id="130", repo="esbenwiberg/pr-guardian",
    ))

    async with async_session() as s:
        s.add_all(rows)
        await s.commit()


async def _seed_dismissals() -> None:
    # One dismissal on the rejected PR — validator can see an already-dismissed finding
    from pr_guardian.persistence.storage import finding_signature
    sig = finding_signature(
        "src/orcha/auth/middleware.py", "architecture", "architecture_intent"
    )
    async with async_session() as s:
        s.add(FindingDismissalRow(
            id=uuid.uuid4(),
            pr_id="42",
            repo="esbenwiberg/orcha",
            platform="github",
            signature=sig,
            status="by_design",
            comment="Middleware layering is a known compromise — tracked separately.",
            source_finding={
                "file": "src/orcha/auth/middleware.py",
                "line": 55,
                "category": "architecture",
                "agent_name": "architecture_intent",
                "severity": "medium",
                "certainty": "medium",
                "description": "Auth decisions leaking into request parsing layer.",
            },
            active=True,
        ))
        await s.commit()


async def _seed_admin() -> None:
    async with async_session() as s:
        s.add(AdminRow(email=DEV_ADMIN_EMAIL, added_by="dev_seed"))
        await s.commit()


def _synced_pr(
    *,
    platform: str,
    pr_id: str,
    org: str,
    repo: str,
    project: str = "",
    title: str,
    author: str,
    author_display: str = "",
    source_branch: str,
    target_branch: str = "main",
    approval_status: str = "pending",
    reviewers: list | None = None,
    comment_count: int = 0,
    is_draft: bool = False,
    has_conflicts: bool = False,
    created_days_ago: int = 0,
    updated_hours_ago: int = 1,
) -> SyncedPRRow:
    if platform == "github":
        pr_url = f"https://github.com/{org}/{repo}/pull/{pr_id}"
    else:
        pr_url = f"https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{pr_id}"
    return SyncedPRRow(
        id=uuid.uuid4(),
        platform=platform,
        pr_id=pr_id,
        org=org,
        project=project,
        repo=repo,
        title=title,
        author=author,
        author_display=author_display or author,
        pr_url=pr_url,
        source_branch=source_branch,
        target_branch=target_branch,
        is_draft=is_draft,
        has_conflicts=has_conflicts,
        approval_status=approval_status,
        reviewers=reviewers or [],
        comment_count=comment_count,
        pr_created_at=_ago(days=created_days_ago),
        pr_updated_at=_ago(hours=updated_hours_ago),
        synced_at=NOW,
    )


async def _seed_pr_dashboard() -> None:
    sources = [
        SyncSourceRow(
            id=uuid.uuid4(), platform="github", org="esbenwiberg",
            repo="pr-guardian", repo_url="https://github.com/esbenwiberg/pr-guardian",
            last_synced_at=_ago(minutes=3), is_active=True,
        ),
        SyncSourceRow(
            id=uuid.uuid4(), platform="github", org="esbenwiberg",
            repo="orcha", repo_url="https://github.com/esbenwiberg/orcha",
            last_synced_at=_ago(minutes=3), is_active=True,
        ),
        SyncSourceRow(
            id=uuid.uuid4(), platform="ado", org="contextand",
            project="Platform", repo="infra-core",
            repo_url="https://dev.azure.com/contextand/Platform/_git/infra-core",
            last_synced_at=_ago(minutes=3), is_active=True,
        ),
    ]

    prs = [
        # My PRs (author = DEV_ADMIN_EMAIL → github handle esbenwiberg)
        _synced_pr(
            platform="github", pr_id="131", org="esbenwiberg", repo="pr-guardian",
            title="Add PR dashboard sync worker", author="esbenwiberg",
            source_branch="feat/pr-dashboard", approval_status="approved",
            reviewers=["alice", "bob"], comment_count=4, created_days_ago=2,
            updated_hours_ago=3,
        ),
        _synced_pr(
            platform="github", pr_id="132", org="esbenwiberg", repo="pr-guardian",
            title="Bump uvicorn to 0.30.1", author="esbenwiberg",
            source_branch="chore/uvicorn-bump", approval_status="pending",
            reviewers=["alice"], comment_count=0, created_days_ago=1,
            updated_hours_ago=10,
        ),
        # Review queue — user is a reviewer
        _synced_pr(
            platform="github", pr_id="133", org="esbenwiberg", repo="orcha",
            title="Replace polling loop with webhook receiver", author="alice",
            source_branch="feat/webhooks", approval_status="pending",
            reviewers=["esbenwiberg", "carol"], comment_count=7,
            created_days_ago=3, updated_hours_ago=2,
        ),
        _synced_pr(
            platform="github", pr_id="134", org="esbenwiberg", repo="pr-guardian",
            title="Refactor triage classifier — extract scorer", author="bob",
            source_branch="refactor/triage-scorer", approval_status="changes_requested",
            reviewers=["esbenwiberg"], comment_count=12, created_days_ago=4,
            updated_hours_ago=5, has_conflicts=True,
        ),
        _synced_pr(
            platform="ado", pr_id="201", org="contextand", project="Platform",
            repo="infra-core", title="Migrate Terraform state to remote backend",
            author="carol@contextand.com", author_display="Carol Jensen",
            source_branch="feat/tf-remote-state", approval_status="pending",
            reviewers=["ewi@contextand.com"], comment_count=3,
            created_days_ago=1, updated_hours_ago=4,
        ),
        # Stale PRs (>5 days old, low activity)
        _synced_pr(
            platform="github", pr_id="119", org="esbenwiberg", repo="orcha",
            title="WIP: experiment with streaming LLM responses", author="alice",
            source_branch="experiment/streaming", approval_status="pending",
            reviewers=[], comment_count=1, created_days_ago=14,
            updated_hours_ago=72, is_draft=True,
        ),
        _synced_pr(
            platform="github", pr_id="122", org="esbenwiberg", repo="pr-guardian",
            title="Add OpenTelemetry tracing spans", author="bob",
            source_branch="feat/otel", approval_status="pending",
            reviewers=["esbenwiberg"], comment_count=2, created_days_ago=9,
            updated_hours_ago=48,
        ),
        _synced_pr(
            platform="ado", pr_id="188", org="contextand", project="Platform",
            repo="infra-core", title="Upgrade PostgreSQL 15 → 16",
            author="dave@contextand.com", author_display="Dave Olsen",
            source_branch="chore/pg16", approval_status="approved",
            reviewers=["ewi@contextand.com", "carol@contextand.com"],
            comment_count=5, created_days_ago=7, updated_hours_ago=30,
        ),
        # A couple more active PRs
        _synced_pr(
            platform="github", pr_id="135", org="esbenwiberg", repo="pr-guardian",
            title="Add inline comment posting to GitHub reviews", author="carol",
            source_branch="feat/inline-comments", approval_status="approved",
            reviewers=["esbenwiberg", "alice"], comment_count=8,
            created_days_ago=0, updated_hours_ago=1,
        ),
        _synced_pr(
            platform="ado", pr_id="205", org="contextand", project="Platform",
            repo="infra-core", title="Add alerting rules for sync worker lag",
            author="ewi@contextand.com", author_display="Esben Wiberg",
            source_branch="feat/sync-alerts", approval_status="pending",
            reviewers=["dave@contextand.com"], comment_count=0,
            created_days_ago=0, updated_hours_ago=0,
        ),
    ]

    identity = UserIdentityRow(
        email=DEV_ADMIN_EMAIL,
        github_handle="esbenwiberg",
        ado_upn="ewi@contextand.com",
        updated_at=NOW,
    )

    async with async_session() as s:
        s.add_all(sources)
        s.add_all(prs)
        s.add(identity)
        await s.commit()


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL not set — refusing to seed.")

    # Create tables if missing (idempotent). Uvicorn's lifespan will also call
    # this on startup, but we need it now for the seed inserts.
    await init_db()

    await _wipe()
    await _seed_reviews()
    await _seed_dismissals()
    await _seed_admin()
    await _seed_pr_dashboard()

    # Dispose engine so uvicorn worker starts clean.
    await close_db()
    # Reset module-level engine handle so the next get picks up fresh state.
    from pr_guardian.persistence import database as _db
    _db._engine = None
    _db._session_factory = None

    print("[dev_seed] seeded 7 reviews, 1 dismissal, 1 admin, 10 synced PRs, 3 sync sources, 1 identity")


if __name__ == "__main__":
    asyncio.run(main())
