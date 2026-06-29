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

from pr_guardian import dev_diff_store
from pr_guardian.core.pr_like_synthesis import SYNTHESIS_DISPLAY_NAME
from pr_guardian.persistence.database import async_session, close_db, init_db
from pr_guardian.persistence.models import (
    AdminRow,
    AgentResultRow,
    ApiKeyRow,
    ConnectionRow,
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
            ScanFindingRow,
            ScanAgentResultRow,
            ScanRow,
            PostedInlineCommentRow,
            ReviewRow,
            PromptOverrideRow,
            GlobalConfigRow,
            ApiKeyRow,
            AdminRow,
            SyncedPRRow,
            SyncSourceRow,
            ConnectionRow,
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
    duration_ms = int((finished - started).total_seconds() * 1000) if finished else None
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


def _patch_for(focus_lines: list[int]) -> tuple[str, int]:
    """Build a unified-diff hunk whose new-file side covers each focus line, so
    the dashboard's hunk extractor can render a snippet around a finding's line.
    Returns ``(patch_text, additions)``."""
    lines = sorted({n for n in focus_lines if n}) or [1]
    start = max(1, lines[0] - 3)
    end = lines[-1] + 3
    span = end - start + 1
    body = [f"@@ -{start},{span} +{start},{span} @@"]
    for ln in range(start, end + 1):
        if ln in lines:
            body.append(f"+    # L{ln}: changed in this PR")
        else:
            body.append(f"     # L{ln}: surrounding context")
    return "\n".join(body) + "\n", len(lines)


def _diff_file(path: str, focus_lines: list[int]) -> dict:
    patch, additions = _patch_for(focus_lines)
    return {
        "path": path,
        "status": "modified",
        "old_path": None,
        "additions": additions,
        "deletions": 0,
        "patch": patch,
    }


def _clean_companions(seed_path: str) -> list[str]:
    """Test + docs siblings that carry NO findings, so the wizard clusters at
    least one *clean* capability (exercises the 'Also reviewed (clean)' wrap-up)
    and the review shows a realistic multi-file diff."""
    stem = seed_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return [f"tests/test_{stem}.py", f"docs/{stem}.md"]


def _build_dev_diffs(
    rows: list, defaults: dict[uuid.UUID, dict[str, list[int]]]
) -> dict[str, dict]:
    """Derive a realistic per-review stored diff from the seeded findings.

    Files with findings get a hunk covering each finding line (so 'Show code'
    works); clean companion files are added so the Wizard/Chapters show real
    LOC, multiple files, and ≥1 clean capability."""
    agent_review = {a.id: a.review_id for a in rows if isinstance(a, AgentResultRow)}
    review_files: dict[uuid.UUID, dict[str, list[int]]] = {}
    for r in rows:
        if isinstance(r, FindingRow):
            rid = agent_review.get(r.agent_result_id)
            if rid:
                review_files.setdefault(rid, {}).setdefault(r.file, []).append(r.line or 1)
        elif isinstance(r, MechanicalResultRow):
            for mf in r.findings or []:
                if mf.get("file"):
                    review_files.setdefault(r.review_id, {}).setdefault(mf["file"], []).append(
                        mf.get("line") or 1
                    )
    for rid, file_lines in defaults.items():
        review_files.setdefault(rid, file_lines)

    review_by_id = {r.id: r for r in rows if isinstance(r, ReviewRow)}
    diffs: dict[str, dict] = {}
    for rid, file_lines in review_files.items():
        review = review_by_id.get(rid)
        if review is None:
            continue
        files = [_diff_file(p, lines) for p, lines in file_lines.items()]
        for clean in _clean_companions(next(iter(file_lines))):
            if clean not in file_lines:
                files.append(_diff_file(clean, [2, 3]))
        diffs[str(rid)] = {"pr_id": review.pr_id, "repo": review.repo, "files": files}
    return diffs


async def _seed_reviews() -> None:
    rows: list = []

    # 1. Clean auto-approve (typo fix)
    r1 = _review(
        repo="esbenwiberg/pr-guardian",
        pr_id="101",
        author="esbenwiberg",
        title="Fix typo in README",
        decision="auto_approve",
        risk_tier="trivial",
        score=0.2,
        started=_ago(hours=26),
        finished=_ago(hours=26, minutes=-3),
        summary="Trivial doc change — auto-approved.",
        cost=0.0014,
    )
    rows.append(r1)
    a1 = _agent(
        r1.id,
        "code_quality_observability",
        "pass",
        ["markdown"],
        "No issues; documentation-only change.",
    )
    rows.append(a1)

    # 2. Human review — a couple of medium findings
    r2 = _review(
        repo="esbenwiberg/pr-guardian",
        pr_id="117",
        author="alice",
        title="Add retry logic to LLM client",
        decision="human_review",
        risk_tier="medium",
        score=5.8,
        started=_ago(hours=4),
        finished=_ago(hours=4, minutes=-7),
        summary="Retry semantics need a closer look — bounded backoff missing.",
        cost=0.0321,
    )
    rows.append(r2)
    a2a = _agent(
        r2.id,
        "code_quality_observability",
        "findings",
        ["python"],
        "Retry loop lacks jitter; logging at warn level would help.",
    )
    rows.append(a2a)
    rows.append(
        _finding(
            a2a.id,
            severity="medium",
            certainty="detected",
            category="reliability",
            file="src/pr_guardian/llm/client.py",
            line=142,
            description="Unbounded retry on 5xx without backoff jitter.",
            suggestion="Use exponential backoff with jitter (full-jitter).",
        )
    )
    a2b = _agent(r2.id, "performance", "findings", ["python"])
    rows.append(a2b)
    rows.append(
        _finding(
            a2b.id,
            severity="low",
            certainty="suspected",
            category="performance",
            file="src/pr_guardian/llm/client.py",
            line=180,
            description="Per-call httpx client instantiation is wasteful.",
            suggestion="Reuse a module-level AsyncClient.",
        )
    )

    # 3. Reject — critical security
    r3 = _review(
        repo="esbenwiberg/orcha",
        pr_id="42",
        author="bob",
        title="Add admin bypass for debug header",
        decision="reject",
        risk_tier="high",
        score=9.2,
        started=_ago(days=1, hours=2),
        finished=_ago(days=1, hours=2, minutes=-11),
        summary="Intentional auth bypass on a trusted header — blocked.",
        cost=0.0604,
        mechanical_passed=True,
    )
    rows.append(r3)
    a3a = _agent(
        r3.id,
        "security_privacy",
        "findings",
        ["python"],
        "Critical: trusted-header admin override is an auth bypass.",
    )
    rows.append(a3a)
    rows.append(
        _finding(
            a3a.id,
            severity="critical",
            certainty="detected",
            category="auth_bypass",
            file="src/orcha/auth/middleware.py",
            line=58,
            description="Admin granted when X-Debug-Admin header present.",
            suggestion="Remove the bypass. Debug auth must go through same path.",
            cwe="CWE-287",
        )
    )
    rows.append(
        _finding(
            a3a.id,
            severity="high",
            certainty="detected",
            category="logging",
            file="src/orcha/auth/middleware.py",
            line=60,
            description="No audit log when admin bypass fires.",
            suggestion="Emit structured audit event on every admin path.",
            cwe="CWE-778",
        )
    )
    a3b = _agent(r3.id, "architecture_intent", "findings", ["python"])
    rows.append(a3b)
    rows.append(
        _finding(
            a3b.id,
            severity="medium",
            certainty="suspected",
            category="architecture",
            file="src/orcha/auth/middleware.py",
            line=55,
            description="Auth decisions leaking into request parsing layer.",
        )
    )

    # 4. Hard block — mechanical failure (secret detected)
    r4 = _review(
        repo="esbenwiberg/orcha",
        pr_id="55",
        author="alice",
        title="Update CI config",
        decision="hard_block",
        risk_tier="high",
        score=10.0,
        started=_ago(hours=8),
        finished=_ago(hours=8, minutes=-1),
        summary="Gitleaks detected an AWS access key. Hard blocked.",
        cost=0.0021,
        mechanical_passed=False,
    )
    rows.append(r4)
    m4 = MechanicalResultRow(
        id=uuid.uuid4(),
        review_id=r4.id,
        tool="gitleaks",
        passed=False,
        severity="critical",
        findings=[
            {
                "rule": "aws-access-token",
                "file": ".github/workflows/deploy.yml",
                "line": 14,
                "match": "AKIA****REDACTED****",
            }
        ],
    )
    rows.append(m4)

    # 5. In-progress — no finished_at
    r5 = _review(
        repo="esbenwiberg/pr-guardian",
        pr_id="123",
        author="esbenwiberg",
        title="Refactor triage classifier",
        decision="pending",
        risk_tier="",
        score=0.0,
        started=_ago(minutes=2),
        finished=None,
        stage="agent_review",
        stage_detail="security_privacy running…",
        summary="",
        cost=0.0,
    )
    rows.append(r5)

    # 6. Older auto-approve — test tweak
    r6 = _review(
        repo="esbenwiberg/pr-guardian",
        pr_id="88",
        author="alice",
        title="Tighten test assertions in triage",
        decision="auto_approve",
        risk_tier="low",
        score=1.4,
        started=_ago(days=3),
        finished=_ago(days=3, minutes=-5),
        summary="Low-risk test change — auto-approved.",
        cost=0.0098,
    )
    rows.append(r6)
    a6 = _agent(
        r6.id,
        "test_quality",
        "pass",
        ["python"],
        "Assertions now cover previously-unchecked paths.",
    )
    rows.append(a6)

    # 7. Inline-comment review — demonstrates PostedInlineCommentRow wiring
    r7 = _review(
        repo="esbenwiberg/pr-guardian",
        pr_id="130",
        author="bob",
        title="Add rate limiting to API endpoints",
        decision="human_review",
        risk_tier="medium",
        score=6.1,
        started=_ago(hours=1),
        finished=_ago(hours=1, minutes=-9),
        summary="Rate limiting implementation has two gaps that need review.",
        cost=0.0287,
        comment_mode="inline",
    )
    rows.append(r7)
    a7 = _agent(
        r7.id,
        "security_privacy",
        "findings",
        ["python"],
        "Missing rate limit on unauthenticated endpoints; key leakage risk.",
    )
    rows.append(a7)
    rows.append(
        _finding(
            a7.id,
            severity="high",
            certainty="detected",
            category="rate_limiting",
            file="src/pr_guardian/api/review.py",
            line=58,
            description="POST /api/review has no rate limit; trivially abusable by unauthenticated callers.",
            suggestion="Apply a per-IP rate limiter (e.g. slowapi) to this endpoint.",
        )
    )
    rows.append(
        _finding(
            a7.id,
            severity="medium",
            certainty="suspected",
            category="info_disclosure",
            file="src/pr_guardian/api/review.py",
            line=71,
            description="Error message from _hydrate_pr leaks internal exception detail to the caller.",
            suggestion="Return a generic 422 message; log the full error server-side only.",
        )
    )
    rows.append(
        PostedInlineCommentRow(
            id=uuid.uuid4(),
            review_id=r7.id,
            platform_comment_id="gh-comment-001",
            platform="github",
            pr_id="130",
            repo="esbenwiberg/pr-guardian",
        )
    )
    rows.append(
        PostedInlineCommentRow(
            id=uuid.uuid4(),
            review_id=r7.id,
            platform_comment_id="gh-comment-002",
            platform="github",
            pr_id="130",
            repo="esbenwiberg/pr-guardian",
        )
    )

    # PostedInlineCommentRow has a FK to reviews but no ORM relationship(), so the
    # unit-of-work has no dependency edge to order parent-before-child. Flush the
    # review/agent/finding rows first, then the comments.
    comment_rows = [r for r in rows if isinstance(r, PostedInlineCommentRow)]
    parent_rows = [r for r in rows if not isinstance(r, PostedInlineCommentRow)]
    async with async_session() as s:
        s.add_all(parent_rows)
        await s.flush()
        s.add_all(comment_rows)
        await s.commit()

    # Write the dev stored-diff sidecar so the dashboard renders real hunks,
    # LOC, files, and clean capabilities locally (no platform connection). The
    # clean auto-approve reviews (no findings) get a representative file each.
    clean_defaults = {
        r1.id: {"README.md": [3]},
        r6.id: {"tests/triage/test_classifier.py": [22, 23]},
    }
    diffs = _build_dev_diffs(rows, clean_defaults)
    store_path = dev_diff_store.save_all(diffs)
    print(f"[dev_seed] wrote {len(diffs)} stored diffs to {store_path}")


def _scan_finding(
    *,
    severity: str,
    certainty: str,
    category: str,
    file: str,
    line: int | None,
    description: str,
    suggestion: str = "",
) -> ScanFindingRow:
    return ScanFindingRow(
        id=uuid.uuid4(),
        severity=severity,
        certainty=certainty,
        category=category,
        file=file,
        line=line,
        description=description,
        suggestion=suggestion,
    )


def _scan_agent(
    *,
    agent_name: str,
    verdict: str,
    summary: str,
    findings: list[ScanFindingRow] | None = None,
    error: str | None = None,
) -> ScanAgentResultRow:
    return ScanAgentResultRow(
        id=uuid.uuid4(),
        agent_name=agent_name,
        verdict=verdict,
        summary=summary,
        error=error,
        findings=findings or [],
    )


async def _seed_scans() -> None:
    """Seed a couple of scans so the /scans dashboard renders non-empty.

    Includes a Deep PR Review (recent_changes_deep) whose per-PR cards use the
    ``PR #<n>: <title>`` identity in ``agent_name`` — deliberately with a long
    title to exercise the widened (TEXT) column. A capped varchar(64) here would
    truncate-crash the save and strand the scan at ``scan_report`` with 0 findings
    (the exact bug this seed lets you verify locally — see migration 006)."""
    started = _ago(hours=2)
    finished = _ago(hours=2, minutes=-3)

    # --- Deep PR Review: one card per re-reviewed merged PR ---
    deep_prs = [
        _scan_agent(
            agent_name=(
                'PR #94: feat(scans): deep per-PR review scan ("fat nightly") '
                "+ readable summary cards"
            ),
            verdict="warn",
            summary=(
                "**Human review** · score 4.20 · "
                "[PR #94](https://github.com/esbenwiberg/pr-guardian/pull/94)\n\n"
                "Re-review surfaced a reliability gap in the scan save path."
            ),
            findings=[
                _scan_finding(
                    severity="medium",
                    certainty="detected",
                    category="Code Quality",
                    file="src/pr_guardian/persistence/storage.py",
                    line=2642,
                    description="Per-agent flush inside the save loop means a single "
                    "oversized column rolls back the whole scan.",
                    suggestion="Validate/cap field widths before insert, or save per-PR.",
                ),
                _scan_finding(
                    severity="low",
                    certainty="suspected",
                    category="Test Quality",
                    file="tests/core/test_pr_like_scan.py",
                    line=1,
                    description="No test covers a PR title longer than the agent_name column.",
                    suggestion="Add a Postgres-backed regression for long PR titles.",
                ),
            ],
        ),
        _scan_agent(
            agent_name="PR #92: fix(scans): authenticate GitHub scans via App installation",
            verdict="pass",
            summary=(
                "**Auto-approve** · score 1.10 · "
                "[PR #92](https://github.com/esbenwiberg/pr-guardian/pull/92)\n\n"
                "Clean at full depth."
            ),
            findings=[],
        ),
        _scan_agent(
            agent_name="PR #88: chore: bump dependencies",
            verdict="flag_human",
            summary=(
                "**Reject** · score 8.40 · "
                "[PR #88](https://github.com/esbenwiberg/pr-guardian/pull/88)\n\n"
                "Transitive dep with a known advisory pulled in."
            ),
            findings=[
                _scan_finding(
                    severity="high",
                    certainty="detected",
                    category="Security/Privacy",
                    file="requirements.txt",
                    line=12,
                    description="Bumped transitive dependency has a published CVE.",
                    suggestion="Pin to a patched version.",
                ),
            ],
        ),
    ]
    deep_findings = sum(len(a.findings) for a in deep_prs)

    # Cross-PR synthesis card: the narrative the real deep scan prepends over the
    # per-PR outcomes (see core/pr_like_synthesis.py). Findings=[] so it doesn't
    # inflate counts; the dashboard hoists it above the per-PR grid.
    synthesis = _scan_agent(
        agent_name=SYNTHESIS_DISPLAY_NAME,
        verdict="pass",
        summary=(
            "**Gate effectiveness**\n"
            f"- {1} of {len(deep_prs)} PRs would need human attention at full depth "
            "(#88 — reject) that the thin daytime gate let merge.\n\n"
            "**Recurring issues**\n"
            "- Scan-save robustness flagged in #94 (oversized column rolls back the save) — "
            "same class as the earlier `category` truncation; worth a width-validation guard, "
            "not another one-off fix.\n\n"
            "**Hotspots**\n"
            "- `src/pr_guardian/persistence/storage.py` is where the recurring save-path risk "
            "concentrates (#94)."
        ),
        findings=[],
    )

    deep_scan = ScanRow(
        id=uuid.uuid4(),
        scan_type="recent_changes_deep",
        repo="esbenwiberg/pr-guardian",
        platform="github",
        time_window_days=7,
        total_findings=deep_findings,
        summary=(
            f"Deep re-review of {len(deep_prs)} merged PR(s): 1 would need human "
            f"attention (reject/block at full depth), {deep_findings} finding(s) total."
        ),
        stage="complete",
        pipeline_log=[
            {
                "ts": started.isoformat(),
                "level": "info",
                "stage": "discovery",
                "msg": f"Found {len(deep_prs)} merged PR(s) in 7 days.",
            },
            {
                "ts": finished.isoformat(),
                "level": "info",
                "stage": "report",
                "msg": "Deep re-review complete.",
            },
        ],
        total_input_tokens=48000,
        total_output_tokens=9200,
        cost_usd=0.3764,
        scan_source="scan",
        started_at=started,
        finished_at=finished,
        duration_ms=int((finished - started).total_seconds() * 1000),
        agent_results=[synthesis, *deep_prs],
    )

    # --- Regular Recent Changes scan (macro, findings-only) ---
    rc_agent = _scan_agent(
        agent_name="recent_changes",
        verdict="findings",
        summary="2 findings across the last 7 days of changes.",
        findings=[
            _scan_finding(
                severity="medium",
                certainty="detected",
                category="reliability",
                file="src/pr_guardian/core/orchestrator.py",
                line=210,
                description="Unawaited task could swallow exceptions on shutdown.",
                suggestion="Await the gather or attach a done-callback.",
            ),
            _scan_finding(
                severity="low",
                certainty="suspected",
                category="performance",
                file="src/pr_guardian/discovery/diff.py",
                line=88,
                description="Repeated regex compilation in a hot loop.",
                suggestion="Hoist the compiled pattern to module scope.",
            ),
        ],
    )
    rc_started = _ago(hours=5)
    rc_finished = _ago(hours=5, minutes=-1)
    rc_scan = ScanRow(
        id=uuid.uuid4(),
        scan_type="recent_changes",
        repo="esbenwiberg/pr-guardian",
        platform="github",
        time_window_days=7,
        total_findings=len(rc_agent.findings),
        summary="Recent-changes scan: 2 finding(s) across 7 days.",
        stage="complete",
        pipeline_log=[],
        total_input_tokens=12000,
        total_output_tokens=2400,
        cost_usd=0.0912,
        scan_source="scan",
        started_at=rc_started,
        finished_at=rc_finished,
        duration_ms=int((rc_finished - rc_started).total_seconds() * 1000),
        agent_results=[rc_agent],
    )

    async with async_session() as s:
        s.add_all([deep_scan, rc_scan])
        await s.commit()


async def _seed_dismissals() -> None:
    # One dismissal on the rejected PR — validator can see an already-dismissed finding
    from pr_guardian.persistence.storage import finding_signature

    sig = finding_signature("src/orcha/auth/middleware.py", "architecture", "architecture_intent")
    async with async_session() as s:
        s.add(
            FindingDismissalRow(
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
                    "certainty": "suspected",
                    "description": "Auth decisions leaking into request parsing layer.",
                },
                active=True,
            )
        )
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
    connection: ConnectionRow | None = None,
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
        connection_id=connection.id if connection else None,
        connection_snapshot=_connection_snapshot(connection) if connection else None,
        pr_created_at=_ago(days=created_days_ago),
        pr_updated_at=_ago(hours=updated_hours_ago),
        synced_at=NOW,
    )


def _connection_snapshot(connection: ConnectionRow | None) -> dict | None:
    if connection is None:
        return None
    return {
        "id": str(connection.id),
        "name": connection.name,
        "platform": connection.platform,
        "org_url": connection.org_url,
        "token_prefix": connection.token_prefix,
        "health_status": connection.health_status,
        "sync_enabled": connection.sync_enabled,
    }


async def _seed_pr_dashboard() -> None:
    github_connection = ConnectionRow(
        id=uuid.uuid4(),
        name="Demo GitHub Browse",
        platform="github",
        token_prefix="demo-gh...",
        health_status="healthy",
        health_message="seeded",
        sync_enabled=True,
        created_by="dev_seed",
        updated_by="dev_seed",
    )
    ado_connection = ConnectionRow(
        id=uuid.uuid4(),
        name="Demo ADO Browse",
        platform="ado",
        org_url="https://dev.azure.com/contextand",
        token_prefix="demo-ado...",
        health_status="healthy",
        health_message="seeded",
        sync_enabled=True,
        created_by="dev_seed",
        updated_by="dev_seed",
    )
    sources = [
        SyncSourceRow(
            id=uuid.uuid4(),
            platform="github",
            org="esbenwiberg",
            repo="pr-guardian",
            repo_url="https://github.com/esbenwiberg/pr-guardian",
            connection_id=github_connection.id,
            connection_snapshot=_connection_snapshot(github_connection),
            last_synced_at=_ago(minutes=3),
            is_active=True,
        ),
        SyncSourceRow(
            id=uuid.uuid4(),
            platform="github",
            org="esbenwiberg",
            repo="orcha",
            repo_url="https://github.com/esbenwiberg/orcha",
            connection_id=github_connection.id,
            connection_snapshot=_connection_snapshot(github_connection),
            last_synced_at=_ago(minutes=3),
            is_active=True,
        ),
        SyncSourceRow(
            id=uuid.uuid4(),
            platform="ado",
            org="contextand",
            project="Platform",
            repo="infra-core",
            repo_url="https://dev.azure.com/contextand/Platform/_git/infra-core",
            connection_id=ado_connection.id,
            connection_snapshot=_connection_snapshot(ado_connection),
            last_synced_at=_ago(minutes=3),
            is_active=True,
        ),
    ]

    prs = [
        # My PRs (author = DEV_ADMIN_EMAIL → github handle esbenwiberg)
        _synced_pr(
            platform="github",
            pr_id="131",
            org="esbenwiberg",
            repo="pr-guardian",
            title="Add PR dashboard sync worker",
            author="esbenwiberg",
            source_branch="feat/pr-dashboard",
            approval_status="approved",
            reviewers=["alice", "bob"],
            comment_count=4,
            created_days_ago=2,
            updated_hours_ago=3,
            connection=github_connection,
        ),
        _synced_pr(
            platform="github",
            pr_id="132",
            org="esbenwiberg",
            repo="pr-guardian",
            title="Bump uvicorn to 0.30.1",
            author="esbenwiberg",
            source_branch="chore/uvicorn-bump",
            approval_status="pending",
            reviewers=["alice"],
            comment_count=0,
            created_days_ago=1,
            updated_hours_ago=10,
            connection=github_connection,
        ),
        # Review queue — user is a reviewer
        _synced_pr(
            platform="github",
            pr_id="133",
            org="esbenwiberg",
            repo="orcha",
            title="Replace polling loop with webhook receiver",
            author="alice",
            source_branch="feat/webhooks",
            approval_status="pending",
            reviewers=["esbenwiberg", "carol"],
            comment_count=7,
            created_days_ago=3,
            updated_hours_ago=2,
            connection=github_connection,
        ),
        _synced_pr(
            platform="github",
            pr_id="134",
            org="esbenwiberg",
            repo="pr-guardian",
            title="Refactor triage classifier — extract scorer",
            author="bob",
            source_branch="refactor/triage-scorer",
            approval_status="changes_requested",
            reviewers=["esbenwiberg"],
            comment_count=12,
            created_days_ago=4,
            updated_hours_ago=5,
            has_conflicts=True,
            connection=github_connection,
        ),
        _synced_pr(
            platform="ado",
            pr_id="201",
            org="contextand",
            project="Platform",
            repo="infra-core",
            title="Migrate Terraform state to remote backend",
            author="carol@contextand.com",
            author_display="Carol Jensen",
            source_branch="feat/tf-remote-state",
            approval_status="pending",
            reviewers=["ewi@contextand.com"],
            comment_count=3,
            created_days_ago=1,
            updated_hours_ago=4,
            connection=ado_connection,
        ),
        # Stale PRs (>5 days old, low activity)
        _synced_pr(
            platform="github",
            pr_id="119",
            org="esbenwiberg",
            repo="orcha",
            title="WIP: experiment with streaming LLM responses",
            author="alice",
            source_branch="experiment/streaming",
            approval_status="pending",
            reviewers=[],
            comment_count=1,
            created_days_ago=14,
            updated_hours_ago=72,
            is_draft=True,
            connection=github_connection,
        ),
        _synced_pr(
            platform="github",
            pr_id="122",
            org="esbenwiberg",
            repo="pr-guardian",
            title="Add OpenTelemetry tracing spans",
            author="bob",
            source_branch="feat/otel",
            approval_status="pending",
            reviewers=["esbenwiberg"],
            comment_count=2,
            created_days_ago=9,
            updated_hours_ago=48,
            connection=github_connection,
        ),
        _synced_pr(
            platform="ado",
            pr_id="188",
            org="contextand",
            project="Platform",
            repo="infra-core",
            title="Upgrade PostgreSQL 15 → 16",
            author="dave@contextand.com",
            author_display="Dave Olsen",
            source_branch="chore/pg16",
            approval_status="approved",
            reviewers=["ewi@contextand.com", "carol@contextand.com"],
            comment_count=5,
            created_days_ago=7,
            updated_hours_ago=30,
            connection=ado_connection,
        ),
        # A couple more active PRs
        _synced_pr(
            platform="github",
            pr_id="135",
            org="esbenwiberg",
            repo="pr-guardian",
            title="Add inline comment posting to GitHub reviews",
            author="carol",
            source_branch="feat/inline-comments",
            approval_status="approved",
            reviewers=["esbenwiberg", "alice"],
            comment_count=8,
            created_days_ago=0,
            updated_hours_ago=1,
            connection=github_connection,
        ),
        _synced_pr(
            platform="ado",
            pr_id="205",
            org="contextand",
            project="Platform",
            repo="infra-core",
            title="Add alerting rules for sync worker lag",
            author="ewi@contextand.com",
            author_display="Esben Wiberg",
            source_branch="feat/sync-alerts",
            approval_status="pending",
            reviewers=["dave@contextand.com"],
            comment_count=0,
            created_days_ago=0,
            updated_hours_ago=0,
            connection=ado_connection,
        ),
    ]

    identity = UserIdentityRow(
        email=DEV_ADMIN_EMAIL,
        github_handle="esbenwiberg",
        ado_upn="ewi@contextand.com",
        updated_at=NOW,
    )

    async with async_session() as s:
        s.add_all([github_connection, ado_connection])
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
    await _seed_scans()
    await _seed_dismissals()
    await _seed_admin()
    await _seed_pr_dashboard()

    # Dispose engine so uvicorn worker starts clean.
    await close_db()
    # Reset module-level engine handle so the next get picks up fresh state.
    from pr_guardian.persistence import database as _db

    _db._engine = None
    _db._session_factory = None

    print(
        "[dev_seed] seeded 7 reviews, 2 scans (1 deep PR review), 1 dismissal, 1 admin, "
        "10 synced PRs, 3 sync sources, 1 identity"
    )


if __name__ == "__main__":
    asyncio.run(main())
