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
@click.option("--config", "config_path", default="review.yml", help="Config file path")
def validate_config(config_path: str):
    """Validate a review.yml configuration file."""
    from pr_guardian.config.loader import load_repo_config

    try:
        config = load_repo_config(Path(config_path).parent)
        click.echo("✓ Configuration is valid")
        click.echo(f"  Repo risk class: {config.repo_risk_class}")
        click.echo(f"  Auto-approve: {'enabled' if config.auto_approve.enabled else 'disabled'}")
        click.echo(f"  LLM provider: {config.llm.default_provider}")
    except Exception as e:
        click.echo(f"✗ Configuration error: {e}", err=True)
        sys.exit(1)


@main.command("dry-run")
@click.option("--config", "config_path", default=".", help="Repo path with review.yml")
@click.option("--diff-target", default="main", help="Target branch")
@click.argument("files", nargs=-1)
def dry_run(config_path: str, diff_target: str, files: tuple[str, ...]):
    """Run triage classification without AI agents."""
    from pr_guardian.config.loader import load_repo_config
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

    repo_path = Path(config_path)
    config = load_repo_config(repo_path)

    diff = Diff(files=[DiffFile(path=f, status="modified") for f in file_list])
    language_map = detect_languages(file_list)
    security_surface = build_security_surface(config.security_surface, file_list)
    dep_graph = build_dep_graph(config.path_risk.critical_consumers or None)
    blast_radius = compute_blast_radius(file_list, security_surface, dep_graph)
    change_profile = build_change_profile(
        file_list, diff, security_surface, blast_radius, config.file_roles,
    )

    risk_class_map = {
        "standard": RepoRiskClass.STANDARD,
        "elevated": RepoRiskClass.ELEVATED,
        "critical": RepoRiskClass.CRITICAL,
    }

    context = ReviewContext(
        pr=PlatformPR(
            platform=Platform.GITHUB, pr_id="dry-run", repo="local",
            repo_url="", source_branch="feature", target_branch=diff_target,
            author="cli", title="Dry run", head_commit_sha="",
        ),
        repo_path=repo_path,
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
    click.echo(f"Reasons:")
    for r in triage_result.reasons:
        click.echo(f"  - {r}")


@main.command("scan-recent")
@click.option("--repo", required=True, help="Repository (owner/repo)")
@click.option("--platform", default="github", help="Platform (github, ado)")
@click.option("--days", default=7, type=int, help="Time window in days")
@click.option("--since", default=None, help="ISO date override for start of window")
def scan_recent(repo: str, platform: str, days: int, since: str | None):
    """Run a recent changes scan on merged code."""
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
            )
            click.echo(f"\nScan complete: {result.scan_type.value}")
            click.echo(f"  Findings: {result.total_findings}")
            click.echo(f"  Cost: ${result.cost_usd:.4f}")
            if result.summary:
                click.echo(f"  Summary: {result.summary}")
            for ar in result.agent_results:
                click.echo(f"  Agent {ar.agent_name}: {ar.verdict.value}, {len(ar.findings)} finding(s)")
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
                click.echo(f"  Agent {ar.agent_name}: {ar.verdict.value}, {len(ar.findings)} finding(s)")
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
