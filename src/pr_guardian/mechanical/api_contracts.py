from __future__ import annotations

import asyncio
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult


API_SPEC_EXTENSIONS = frozenset({".yaml", ".yml", ".json"})
API_SPEC_MARKERS = frozenset({"openapi", "swagger", "api-spec", "api_spec"})


def _is_api_spec(file_path: str) -> bool:
    """Check if a file looks like an API specification."""
    lower = file_path.lower()
    return any(marker in lower for marker in API_SPEC_MARKERS) and any(
        lower.endswith(ext) for ext in API_SPEC_EXTENSIONS
    )


async def run_api_contract_check(
    repo_path: Path,
    changed_files: list[str],
    target_branch: str = "main",
) -> MechanicalCheckResult:
    """Check for breaking API changes using oasdiff (OpenAPI only)."""
    start = time.monotonic()

    spec_files = [f for f in changed_files if _is_api_spec(f)]
    if not spec_files:
        return MechanicalCheckResult(
            tool="api-contracts", passed=True,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    findings: list[CheckFinding] = []

    for spec_file in spec_files:
        try:
            proc = await asyncio.create_subprocess_exec(
                "oasdiff", "breaking",
                "--base", f"origin/{target_branch}:{spec_file}",
                "--revision", str(repo_path / spec_file),
                "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo_path),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError:
            return MechanicalCheckResult(
                tool="api-contracts", passed=True,
                error="oasdiff not installed — skipped",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except asyncio.TimeoutError:
            continue

        if proc.returncode != 0 and stdout.strip():
            findings.append(CheckFinding(
                file=spec_file, line=None,
                rule="api-breaking-change",
                message=f"Breaking API changes detected in {spec_file}",
                severity=CheckSeverity.WARNING,
            ))

    duration = int((time.monotonic() - start) * 1000)
    return MechanicalCheckResult(
        tool="api-contracts",
        passed=True,  # API contracts are warn-only, never block
        severity=CheckSeverity.WARNING if findings else CheckSeverity.INFO,
        findings=findings,
        duration_ms=duration,
    )
