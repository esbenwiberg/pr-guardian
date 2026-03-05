from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.languages.registry import get_tool_config
from pr_guardian.mechanical.api_contracts import run_api_contract_check
from pr_guardian.mechanical.deps import run_npm_audit, run_pip_audit
from pr_guardian.mechanical.gitleaks import run_gitleaks
from pr_guardian.mechanical.migration_safety import run_migration_safety
from pr_guardian.mechanical.pii_scanner import run_pii_scanner
from pr_guardian.mechanical.results import MechanicalCheckResult
from pr_guardian.mechanical.semgrep import run_semgrep
from pr_guardian.models.languages import LanguageMap

log = structlog.get_logger()


async def run_mechanical_checks(
    repo_path: Path,
    language_map: LanguageMap,
    changed_files: list[str],
    config: GuardianConfig,
    target_branch: str = "main",
) -> list[MechanicalCheckResult]:
    """Run all applicable mechanical checks in parallel."""
    tasks: list[asyncio.Task[MechanicalCheckResult]] = []

    # Universal checks (always run)
    tasks.append(asyncio.create_task(run_gitleaks(repo_path)))
    tasks.append(asyncio.create_task(run_semgrep(repo_path)))
    tasks.append(asyncio.create_task(run_pii_scanner(repo_path, changed_files)))
    tasks.append(asyncio.create_task(
        run_api_contract_check(repo_path, changed_files, target_branch)
    ))
    tasks.append(asyncio.create_task(run_migration_safety(repo_path, changed_files)))

    # Language-conditional checks
    for lang in language_map.languages:
        tool_cfg = get_tool_config(lang)

        if lang in ("typescript", "javascript") and tool_cfg.mechanical_tools.get("npm_audit"):
            tasks.append(asyncio.create_task(run_npm_audit(repo_path)))

        if lang == "python" and tool_cfg.mechanical_tools.get("pip_audit"):
            tasks.append(asyncio.create_task(run_pip_audit(repo_path)))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    final: list[MechanicalCheckResult] = []
    for r in results:
        if isinstance(r, MechanicalCheckResult):
            final.append(r)
        elif isinstance(r, Exception):
            log.error("mechanical_check_failed", error=str(r))
            final.append(MechanicalCheckResult(
                tool="unknown", passed=False,
                error=str(r),
            ))

    log.info(
        "mechanical_checks_complete",
        total=len(final),
        passed=sum(1 for r in final if r.passed),
        failed=sum(1 for r in final if not r.passed),
    )
    return final


def all_checks_passed(results: list[MechanicalCheckResult]) -> bool:
    """Return True if all mechanical checks passed (no hard failures)."""
    return all(r.passed for r in results)
