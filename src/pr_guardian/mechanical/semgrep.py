from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult


async def run_semgrep(
    repo_path: Path,
    rules: str = "auto",
    target_files: list[str] | None = None,
) -> MechanicalCheckResult:
    """Run semgrep SAST scanner on the repo."""
    start = time.monotonic()
    cmd = ["semgrep", "scan", "--json", "--quiet"]

    if rules != "auto":
        cmd.extend(["--config", rules])
    else:
        cmd.extend(["--config", "auto"])

    if target_files:
        cmd.extend(target_files)
    else:
        cmd.append(str(repo_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_path),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except FileNotFoundError:
        return MechanicalCheckResult(
            tool="semgrep", passed=True,
            error="semgrep not installed — skipped",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except asyncio.TimeoutError:
        return MechanicalCheckResult(
            tool="semgrep", passed=False,
            error="semgrep timed out after 120s",
            severity=CheckSeverity.ERROR,
            duration_ms=120_000,
        )

    findings: list[CheckFinding] = []
    try:
        data = json.loads(stdout.decode())
        for result in data.get("results", []):
            sev = _map_severity(result.get("extra", {}).get("severity", "WARNING"))
            findings.append(CheckFinding(
                file=result.get("path", ""),
                line=result.get("start", {}).get("line"),
                rule=result.get("check_id", ""),
                message=result.get("extra", {}).get("message", ""),
                severity=sev,
            ))
    except (json.JSONDecodeError, KeyError):
        pass

    has_errors = any(f.severity == CheckSeverity.ERROR for f in findings)
    duration = int((time.monotonic() - start) * 1000)

    return MechanicalCheckResult(
        tool="semgrep",
        passed=not has_errors,
        severity=CheckSeverity.ERROR if has_errors else CheckSeverity.WARNING,
        findings=findings,
        duration_ms=duration,
    )


def _map_severity(semgrep_severity: str) -> CheckSeverity:
    mapping = {
        "ERROR": CheckSeverity.ERROR,
        "WARNING": CheckSeverity.WARNING,
        "INFO": CheckSeverity.INFO,
    }
    return mapping.get(semgrep_severity.upper(), CheckSeverity.WARNING)
