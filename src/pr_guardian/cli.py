from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

log = structlog.get_logger()


@click.group()
def main():
    """PR Guardian — automated PR review pipeline."""
    pass


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8000, type=int, help="Bind port")
def serve(host: str, port: int):
    """Start the PR Guardian service."""
    import uvicorn

    uvicorn.run("pr_guardian.main:app", host=host, port=port, reload=False)


@main.command("detect-languages")
@click.option("--diff-target", default="main", help="Target branch for diff")
@click.option("--output", "output_path", default=None, help="Output JSON file")
@click.argument("files", nargs=-1)
def detect_languages_cmd(diff_target: str, output_path: str | None, files: tuple[str, ...]):
    """Detect languages in changed files."""
    from pr_guardian.languages.detector import detect_languages

    file_list = list(files) if files else []
    if not file_list:
        # Read from stdin
        file_list = [line.strip() for line in sys.stdin if line.strip()]

    result = detect_languages(file_list)
    output = {
        "primary_language": result.primary_language,
        "language_count": result.language_count,
        "cross_stack": result.cross_stack,
        "languages": result.languages,
    }

    if output_path:
        Path(output_path).write_text(json.dumps(output, indent=2))
        click.echo(f"Written to {output_path}")
    else:
        click.echo(json.dumps(output, indent=2))


@main.command("validate")
def validate_config():
    """Validate Guardian Profile policy defaults."""
    from pr_guardian.config.profile_resolver import resolve_default_profile_config

    try:
        resolved = asyncio.run(resolve_default_profile_config())
        config = resolved.config
        click.echo("✓ Profile policy defaults are valid")
        click.echo(f"  Repo risk class: {config.repo_risk_class}")
        click.echo(f"  Auto-approve: {'enabled' if config.auto_approve.enabled else 'disabled'}")
        click.echo(f"  LLM provider: {config.llm.default_provider}")
    except Exception as e:
        click.echo(f"✗ Configuration error: {e}", err=True)
        sys.exit(1)


@main.command("dry-run")
@click.option("--repo-path", default=".", help="Local repository path for context")
@click.option("--diff-target", default="main", help="Target branch")
@click.argument("files", nargs=-1)
def dry_run(repo_path: str, diff_target: str, files: tuple[str, ...]):
    """Run triage classification without AI agents."""
    from pr_guardian.config.profile_resolver import resolve_default_profile_config
    from pr_guardian.discovery.blast_radius import compute_blast_radius
    from pr_guardian.discovery.change_profile import build_change_profile
    from pr_guardian.discovery.dep_graph import build_dep_graph
    from pr_guardian.languages.detector import detect_languages
    from pr_guardian.models.context import RepoRiskClass, ReviewContext
    from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
    from pr_guardian.triage.classifier import classify
    from pr_guardian.triage.surface_map import build_security_surface

    file_list = list(files) if files else []
    if not file_list:
        file_list = [line.strip() for line in sys.stdin if line.strip()]

    local_repo_path = Path(repo_path)
    config = asyncio.run(resolve_default_profile_config()).config

    diff = Diff(files=[DiffFile(path=f, status="modified") for f in file_list])
    language_map = detect_languages(file_list)
    security_surface = build_security_surface(config.security_surface, file_list)
    dep_graph = build_dep_graph(config.path_risk.critical_consumers or None)
    blast_radius = compute_blast_radius(file_list, security_surface, dep_graph)
    change_profile = build_change_profile(
        file_list,
        diff,
        security_surface,
        blast_radius,
        config.file_roles,
    )

    risk_class_map = {
        "standard": RepoRiskClass.STANDARD,
        "elevated": RepoRiskClass.ELEVATED,
        "critical": RepoRiskClass.CRITICAL,
    }

    context = ReviewContext(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="dry-run",
            repo="local",
            repo_url="",
            source_branch="feature",
            target_branch=diff_target,
            author="cli",
            title="Dry run",
            head_commit_sha="",
        ),
        repo_path=local_repo_path,
        diff=diff,
        changed_files=file_list,
        lines_changed=diff.lines_changed,
        language_map=language_map,
        primary_language=language_map.primary_language,
        cross_stack=language_map.cross_stack,
        repo_config=config.model_dump(),
        repo_risk_class=risk_class_map.get(config.repo_risk_class, RepoRiskClass.STANDARD),
        hotspots=set(),
        security_surface=security_surface,
        blast_radius=blast_radius,
        change_profile=change_profile,
    )

    triage_result = classify(context, config)

    click.echo(f"\nRisk Tier: {triage_result.risk_tier.value.upper()}")
    click.echo(f"Agents: {', '.join(sorted(triage_result.agent_set)) or 'none'}")
    click.echo("Reasons:")
    for r in triage_result.reasons:
        click.echo(f"  - {r}")


@main.command("scan-recent")
@click.option("--repo", required=True, help="Repository (owner/repo)")
@click.option("--platform", default="github", help="Platform (github, ado)")
@click.option("--days", default=7, type=int, help="Time window in days")
@click.option("--since", default=None, help="ISO date override for start of window")
@click.option("--base", "base_ref", default=None, help="Base commit/ref (range mode)")
@click.option("--head", "head_ref", default=None, help="Head commit/ref (range mode; default branch)")
def scan_recent(
    repo: str,
    platform: str,
    days: int,
    since: str | None,
    base_ref: str | None,
    head_ref: str | None,
):
    """Run a recent changes scan on merged code (time window or base..head range)."""
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core.recent_changes import run_recent_changes_scan
    from pr_guardian.platform.factory import create_adapter

    adapter = create_adapter(platform)
    config = GuardianConfig()

    async def _run():
        try:
            result = await run_recent_changes_scan(
                repo=repo,
                platform=platform,
                adapter=adapter,
                config=config,
                time_window_days=days,
                since=since,
                base_ref=base_ref,
                head_ref=head_ref,
            )
            click.echo(f"\nScan complete: {result.scan_type.value}")
            click.echo(f"  Findings: {result.total_findings}")
            click.echo(f"  Cost: ${result.cost_usd:.4f}")
            if result.summary:
                click.echo(f"  Summary: {result.summary}")
            for ar in result.agent_results:
                click.echo(
                    f"  Agent {ar.agent_name}: {ar.verdict.value}, {len(ar.findings)} finding(s)"
                )
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.run(_run())


@main.command("scan-maintenance")
@click.option("--repo", required=True, help="Repository (owner/repo)")
@click.option("--platform", default="github", help="Platform (github, ado)")
@click.option("--staleness", default=6, type=int, help="Months since last modification")
@click.option("--max-files", default=50, type=int, help="Max stale files to analyze")
def scan_maintenance(repo: str, platform: str, staleness: int, max_files: int):
    """Run a maintenance scan to find stale files needing attention."""
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core.maintenance import run_maintenance_scan
    from pr_guardian.platform.factory import create_adapter

    adapter = create_adapter(platform)
    config = GuardianConfig()

    async def _run():
        try:
            result = await run_maintenance_scan(
                repo=repo,
                platform=platform,
                adapter=adapter,
                config=config,
                staleness_months=staleness,
                max_files=max_files,
            )
            click.echo(f"\nScan complete: {result.scan_type.value}")
            click.echo(f"  Findings: {result.total_findings}")
            click.echo(f"  Cost: ${result.cost_usd:.4f}")
            if result.summary:
                click.echo(f"  Summary: {result.summary}")
            for ar in result.agent_results:
                click.echo(
                    f"  Agent {ar.agent_name}: {ar.verdict.value}, {len(ar.findings)} finding(s)"
                )
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.run(_run())


@main.command("review-range")
@click.option("--repo", required=True, help="Repository (owner/repo)")
@click.option("--platform", default="github", help="Platform (github, ado)")
@click.option("--branch", default="main", help="Branch being reviewed")
@click.option("--since-commit", default=None, help="Base commit/ref (head defaults to branch)")
@click.option("--since", default=None, help="ISO-8601 timestamp; resolves base/head from history")
@click.option("--head", default=None, help="Explicit head ref/SHA (default: branch HEAD)")
def review_range(repo, platform, branch, since_commit, since, head):
    """Review a commit range (since commit / since time) with the full PR pipeline."""
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core.orchestrator import run_review
    from pr_guardian.core.range_review import (
        RangeResolutionError,
        build_range_diff,
        build_range_pr,
        resolve_range,
    )
    from pr_guardian.platform.factory import create_adapter

    if bool(since_commit) == bool(since):
        click.echo("Provide exactly one of --since-commit or --since.", err=True)
        sys.exit(1)

    adapter = create_adapter(platform)
    config = GuardianConfig()

    async def _run():
        try:
            base_ref, head_ref = await resolve_range(
                adapter,
                repo,
                branch=branch,
                since_commit=since_commit,
                since_time=since,
                head=head,
            )
            click.echo(f"Range: {base_ref[:12]}..{head_ref[:12]} on {branch}")
            diff, meta = await build_range_diff(adapter, repo, base_ref, head_ref)
            click.echo(f"Diff: {meta['files_changed']} file(s) changed.")
            pr = build_range_pr(repo, platform, base_ref, head_ref, branch, "cli")
            result = await run_review(
                pr,
                adapter,
                service_config=config,
                post_comment=False,
                diff_override=diff,
                skip_platform_side_effects=True,
            )
            click.echo(f"\nDecision: {result.decision.value.upper()}")
            click.echo(f"  Risk tier: {result.risk_tier.value}")
            click.echo(f"  Score: {result.combined_score:.2f}")
            click.echo(f"  Cost: ${result.cost_usd:.4f}")
            for ar in result.agent_results:
                if ar.findings:
                    click.echo(f"  {ar.agent_name}: {len(ar.findings)} finding(s)")
        except RangeResolutionError as e:
            click.echo(f"Could not resolve range: {e}", err=True)
            sys.exit(1)
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.run(_run())


def _local_git_diff(repo_path: str, base: str | None, head: str):
    """Build a Diff from a local git checkout. Dev/self-validation only.

    With ``base`` set, diffs ``base...head``; otherwise diffs the working tree
    against ``head`` (default HEAD). Shells git directly — never used by the
    hosted pipeline, only the local CLI.
    """
    import subprocess
    from typing import cast

    from pr_guardian.models.pr import Diff, DiffFile, FileStatus

    rng = [f"{base}...{head}"] if base else [head]

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    name_status = _git("diff", "--name-status", *rng).splitlines()
    numstat = {}
    for line in _git("diff", "--numstat", *rng).splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add = 0 if parts[0] == "-" else int(parts[0])
            dele = 0 if parts[1] == "-" else int(parts[1])
            numstat[parts[-1]] = (add, dele)

    files: list = []
    for line in name_status:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][0]
        path = parts[-1]
        patch = _git("diff", *rng, "--", path)
        add, dele = numstat.get(path, (0, 0))
        files.append(
            DiffFile(
                path=path,
                status=cast(FileStatus, status_map.get(code, "modified")),
                old_path=parts[1] if code == "R" and len(parts) >= 3 else None,
                additions=add,
                deletions=dele,
                patch=patch,
            )
        )
    return Diff(files=files)


@main.command("review-local")
@click.option("--repo-path", default=".", help="Local git repo to review")
@click.option("--base", default=None, help="Base ref (diff base...head); omit to review working tree")
@click.option("--head", default="HEAD", help="Head ref (default HEAD)")
@click.option("--branch", default="main", help="Target branch for policy purposes")
def review_local(repo_path, base, head, branch):
    """Review a LOCAL git checkout with the full pipeline. Dev/self-validation only.

    Pair with GUARDIAN_LLM_PROVIDER=claude-cli (real, offline LLM) or =fake
    (deterministic) — no GitHub, no API key, no DB.
    """
    import os

    from pr_guardian.config.loader import apply_global_settings
    from pr_guardian.config.schema import GuardianConfig
    from pr_guardian.core.orchestrator import run_review
    from pr_guardian.core.range_review import build_range_pr

    provider = os.environ.get("GUARDIAN_LLM_PROVIDER", "(config default)")
    diff = _local_git_diff(repo_path, base, head)
    if not diff.files:
        click.echo("No changes to review in the given range.")
        return
    repo_name = Path(repo_path).resolve().name

    async def _run():
        config = await apply_global_settings(GuardianConfig())
        pr = build_range_pr(repo_name, "github", base or "WORKTREE", head, branch, "local")
        click.echo(
            f"Reviewing {len(diff.files)} file(s) from {repo_name} "
            f"({base + '...' if base else 'working tree vs '}{head}) via provider={provider}"
        )
        result = await run_review(
            pr,
            _NullAdapter(),
            service_config=config,
            post_comment=False,
            diff_override=diff,
            skip_platform_side_effects=True,
        )
        click.echo(f"\nDecision: {result.decision.value.upper()}")
        click.echo(f"  Risk tier: {result.risk_tier.value}   Score: {result.combined_score:.2f}")
        click.echo(f"  Cost: ${result.cost_usd:.4f}   Tokens: "
                   f"{result.total_input_tokens}+{result.total_output_tokens}")
        n = 0
        for ar in result.agent_results:
            for f in ar.findings:
                n += 1
                click.echo(f"  [{f.severity.value}/{f.certainty.value}] {f.category} "
                           f"({ar.agent_name}) {f.file}:{f.line or '?'}")
                click.echo(f"      {f.description[:140]}")
        if n == 0:
            click.echo("  No findings surfaced.")

    asyncio.run(_run())


class _NullAdapter:
    """No-op adapter for local reviews — the diff is provided directly and all
    platform side effects are skipped, so only archmap lookup is exercised."""

    async def fetch_archmap_artifact(self, pr):
        return None

    async def close(self):
        pass


@main.command("reviews")
@click.option("--limit", default=20, type=int, help="Max reviews to show")
@click.option("--repo", default=None, help="Filter by repo")
@click.option("--decision", default=None, help="Filter by decision")
@click.option("--json-output", "as_json", is_flag=True, help="Output raw JSON")
def list_reviews_cmd(limit, repo, decision, as_json):
    """List recent reviews."""
    from pr_guardian.persistence import storage

    async def _run():
        rows = await storage.list_reviews(limit=limit, repo=repo, decision=decision)
        if as_json:
            click.echo(json.dumps(rows, indent=2, default=str))
            return
        if not rows:
            click.echo("No reviews found.")
            return
        for r in rows:
            decision_str = (r.get("decision") or "pending").upper()
            risk = (r.get("risk_tier") or "?").upper()
            score = r.get("combined_score")
            score_str = f"{score:.1f}" if score is not None else "—"
            click.echo(
                f"  {r['id'][:8]}  {decision_str:<14} {risk:<8} score={score_str:<5}  "
                f"{r.get('repo', ''):<30} {r.get('title', '')[:50]}"
            )

    asyncio.run(_run())


@main.command("review")
@click.argument("review_id")
@click.option("--json-output", "as_json", is_flag=True, help="Output raw JSON")
def show_review_cmd(review_id, as_json):
    """Show review detail with findings."""
    import uuid as uuid_mod
    from pr_guardian.persistence import storage
    from pr_guardian.persistence.storage import finding_signature

    async def _run():
        try:
            rid = uuid_mod.UUID(review_id)
        except ValueError:
            click.echo(f"Invalid review ID: {review_id}", err=True)
            sys.exit(1)

        row = await storage.get_review(rid)
        if not row:
            click.echo("Review not found.", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json.dumps(row, indent=2, default=str))
            return

        decision_str = (row.get("decision") or "pending").upper()
        risk = (row.get("risk_tier") or "?").upper()
        score = row.get("combined_score")

        click.echo(f"\n{'=' * 70}")
        click.echo(f"Review: {row['id']}")
        click.echo(
            f"PR:     {row.get('repo', '')} #{row.get('pr_id', '')} — {row.get('title', '')}"
        )
        click.echo(f"Decision: {decision_str}   Risk: {risk}   Score: {score}")
        click.echo(
            f"Cost: ${row.get('cost_usd', 0):.4f}   Duration: {row.get('duration_ms', 0)}ms"
        )
        click.echo(f"{'=' * 70}")

        # Enrich with dismissals
        dismissals = []
        sig_map = {}
        try:
            dismissals = await storage.get_active_dismissals(
                row["pr_id"],
                row["repo"],
                row["platform"],
            )
            sig_map = {d["signature"]: d for d in dismissals}
        except Exception:
            pass

        finding_num = 0
        for agent in row.get("agent_results", []):
            if not agent.get("findings"):
                continue
            click.echo(f"\n  Agent: {agent['agent_name']}  (verdict: {agent.get('verdict', '?')})")
            click.echo(f"  {'—' * 60}")
            for f in agent["findings"]:
                finding_num += 1
                sig = finding_signature(
                    f.get("file", ""),
                    f.get("category", ""),
                    agent["agent_name"],
                )
                dismissed = sig_map.get(sig)
                dismiss_tag = f" [DISMISSED: {dismissed['status']}]" if dismissed else ""
                click.echo(
                    f"    [{finding_num}] {f.get('severity', '?').upper()}/{f.get('certainty', '?').upper()}  "
                    f"{f.get('category', '')}{dismiss_tag}"
                )
                click.echo(f"        File: {f.get('file', '?')}:{f.get('line', '?')}")
                click.echo(f"        {f.get('description', '')[:120]}")
                if f.get("suggestion"):
                    click.echo(f"        → {f['suggestion'][:120]}")
                click.echo(f"        ID: {f.get('id', '?')}")

        if finding_num == 0:
            click.echo("\n  No findings.")

        click.echo()

    asyncio.run(_run())


@main.command("dismiss")
@click.argument("finding_id")
@click.option(
    "--status",
    required=True,
    type=click.Choice(["by_design", "false_positive", "acknowledged", "will_fix"]),
)
@click.option("--comment", default="", help="Optional comment")
def dismiss_cmd(finding_id, status, comment):
    """Dismiss a finding by ID."""
    import uuid as uuid_mod
    from pr_guardian.persistence import storage
    from pr_guardian.persistence.storage import finding_signature
    from pr_guardian.persistence.database import async_session as get_session
    from pr_guardian.persistence.models import FindingRow, AgentResultRow, ReviewRow

    async def _run():
        try:
            fid = uuid_mod.UUID(finding_id)
        except ValueError:
            click.echo(f"Invalid finding ID: {finding_id}", err=True)
            sys.exit(1)

        # Look up finding context
        async with get_session() as session:
            from sqlalchemy import select as sel

            f_row = (await session.scalars(sel(FindingRow).where(FindingRow.id == fid))).first()
            if not f_row:
                click.echo("Finding not found.", err=True)
                sys.exit(1)
            ar_row = await session.get(AgentResultRow, f_row.agent_result_id)
            r_row = await session.get(ReviewRow, ar_row.review_id)

        finding_dict = {
            "file": f_row.file,
            "line": f_row.line,
            "category": f_row.category,
            "severity": f_row.severity,
            "certainty": f_row.certainty,
            "description": f_row.description,
        }

        dismissal_id = await storage.upsert_dismissal(
            pr_id=r_row.pr_id,
            repo=r_row.repo,
            platform=r_row.platform,
            finding=finding_dict,
            agent_name=ar_row.agent_name,
            status=status,
            comment=comment,
        )
        sig = finding_signature(f_row.file, f_row.category, ar_row.agent_name)
        click.echo(f"Dismissed: {dismissal_id} (sig={sig})")

    asyncio.run(_run())


@main.command("batch-dismiss")
@click.argument("review_id")
@click.option(
    "--status",
    required=True,
    type=click.Choice(["by_design", "false_positive", "acknowledged", "will_fix"]),
)
@click.option("--comment", default="", help="Optional comment")
@click.option(
    "--finding-ids", default=None, help="Comma-separated finding IDs (default: all findings)"
)
@click.option(
    "--severity",
    default=None,
    type=click.Choice(["low", "medium", "high", "critical"], case_sensitive=False),
    help="Only dismiss findings with this severity or lower",
)
def batch_dismiss_cmd(review_id, status, comment, finding_ids, severity):
    """Batch dismiss findings from a review."""
    import uuid as uuid_mod
    from pr_guardian.persistence import storage

    SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    async def _run():
        try:
            rid = uuid_mod.UUID(review_id)
        except ValueError:
            click.echo(f"Invalid review ID: {review_id}", err=True)
            sys.exit(1)

        row = await storage.get_review(rid)
        if not row:
            click.echo("Review not found.", err=True)
            sys.exit(1)

        # Parse optional finding IDs filter
        target_ids = None
        if finding_ids:
            target_ids = set()
            for fid_str in finding_ids.split(","):
                try:
                    target_ids.add(str(uuid_mod.UUID(fid_str.strip())))
                except ValueError:
                    click.echo(f"Invalid finding ID: {fid_str.strip()}", err=True)
                    sys.exit(1)

        # Severity threshold
        max_severity = SEVERITY_ORDER[severity] if severity else 999

        dismissed_count = 0
        skipped_count = 0

        for agent in row.get("agent_results", []):
            for f in agent.get("findings", []):
                fid = f.get("id", "")

                # Filter by finding IDs if specified
                if target_ids is not None and fid not in target_ids:
                    skipped_count += 1
                    continue

                # Filter by severity threshold
                f_sev = SEVERITY_ORDER.get(f.get("severity", "low"), 0)
                if f_sev > max_severity:
                    skipped_count += 1
                    continue

                finding_dict = {
                    "file": f.get("file", ""),
                    "line": f.get("line"),
                    "category": f.get("category", ""),
                    "severity": f.get("severity", ""),
                    "certainty": f.get("certainty", ""),
                    "description": f.get("description", ""),
                }

                await storage.upsert_dismissal(
                    pr_id=row["pr_id"],
                    repo=row["repo"],
                    platform=row["platform"],
                    finding=finding_dict,
                    agent_name=agent["agent_name"],
                    status=status,
                    comment=comment,
                )
                dismissed_count += 1

        click.echo(f"Dismissed {dismissed_count} finding(s), skipped {skipped_count}.")

    asyncio.run(_run())


@main.command("my-reviews")
@click.argument("author")
@click.option("--limit", default=10, type=int, help="Max reviews to show")
@click.option("--decision", default=None, help="Filter by decision")
@click.option("--json-output", "as_json", is_flag=True, help="Output raw JSON")
def my_reviews_cmd(author, limit, decision, as_json):
    """Show recent reviews for a specific PR author."""
    from pr_guardian.persistence import storage

    async def _run():
        rows = await storage.list_reviews(limit=limit, author=author, decision=decision)
        if as_json:
            click.echo(json.dumps(rows, indent=2, default=str))
            return
        if not rows:
            click.echo(f"No reviews found for author '{author}'.")
            return
        click.echo(f"\nReviews for {author} (latest {limit}):\n")
        for r in rows:
            decision_str = (r.get("decision") or "pending").upper()
            risk = (r.get("risk_tier") or "?").upper()
            score = r.get("combined_score")
            score_str = f"{score:.1f}" if score is not None else "—"
            findings = sum(len(a.get("findings", [])) for a in r.get("agent_results", []))
            click.echo(
                f"  {r['id'][:8]}  {decision_str:<14} {risk:<8} score={score_str:<5}  "
                f"findings={findings:<3} {r.get('repo', ''):<30} {r.get('title', '')[:40]}"
            )

    asyncio.run(_run())


@main.command("re-review")
@click.argument("review_id")
@click.option("--post-comment/--no-comment", default=True, help="Post comment to PR")
def re_review_cmd(review_id, post_comment):
    """Re-evaluate original findings against incremental changes.

    Does NOT run a full review. Instead, takes the original findings (minus
    dismissed ones), fetches only the diff since the last reviewed commit,
    and asks each agent whether its findings are still valid.
    """
    import uuid as uuid_mod
    from pr_guardian.persistence import storage
    from pr_guardian.core.orchestrator import run_re_review
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    async def _run():
        try:
            rid = uuid_mod.UUID(review_id)
        except ValueError:
            click.echo(f"Invalid review ID: {review_id}", err=True)
            sys.exit(1)

        review = await storage.get_review(rid)
        if not review:
            click.echo("Review not found.", err=True)
            sys.exit(1)

        pr_url = review.get("pr_url")
        if not pr_url:
            click.echo("Review has no PR URL — cannot re-review.", err=True)
            sys.exit(1)

        total_findings = sum(len(a.get("findings", [])) for a in review.get("agent_results", []))
        click.echo(
            f"Re-evaluating {review['repo']} #{review['pr_id']} — "
            f"{total_findings} original finding(s)..."
        )

        stub, platform_name = _parse_pr_url(pr_url)
        adapter = create_adapter(platform_name)

        try:
            pr = await _hydrate_pr(adapter, stub, platform_name)
        except Exception as e:
            click.echo(f"Failed to fetch PR info: {e}", err=True)
            sys.exit(1)

        try:
            result = await run_re_review(
                pr,
                adapter,
                original_review=review,
                post_comment=post_comment,
            )
            kept = sum(len(a.findings) for a in result.agent_results)
            summary = result.dismissal_summary or {}
            click.echo("\nRe-review complete!")
            click.echo(f"  Decision: {result.decision.value.upper()}")
            click.echo(f"  Findings kept: {kept}")
            click.echo(f"  Findings resolved: {summary.get('resolved', 0)}")
            click.echo(f"  Findings dismissed: {summary.get('dismissed', 0)}")
            click.echo(f"  Cost: ${result.cost_usd:.4f}")
        except Exception as e:
            click.echo(f"Re-review failed: {e}", err=True)
            sys.exit(1)
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
