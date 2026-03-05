from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult


async def run_gitleaks(repo_path: Path) -> MechanicalCheckResult:
    """Run gitleaks secret detection — hard block on any finding."""
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            "gitleaks", "detect", "--source", str(repo_path),
            "--report-format", "json", "--report-path", "/dev/stdout",
            "--no-git",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_path),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except FileNotFoundError:
        return MechanicalCheckResult(
            tool="gitleaks", passed=True,
            error="gitleaks not installed — skipped",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except asyncio.TimeoutError:
        return MechanicalCheckResult(
            tool="gitleaks", passed=False,
            error="gitleaks timed out after 30s",
            severity=CheckSeverity.ERROR,
            duration_ms=30_000,
        )

    findings: list[CheckFinding] = []
    try:
        results = json.loads(stdout.decode()) if stdout.strip() else []
        for leak in results:
            findings.append(CheckFinding(
                file=leak.get("File", ""),
                line=leak.get("StartLine"),
                rule=leak.get("RuleID", "secret-detected"),
                message=f"Secret detected: {leak.get('Description', 'unknown')}",
                severity=CheckSeverity.ERROR,
            ))
    except (json.JSONDecodeError, KeyError):
        pass

    duration = int((time.monotonic() - start) * 1000)
    return MechanicalCheckResult(
        tool="gitleaks",
        passed=len(findings) == 0,
        severity=CheckSeverity.ERROR if findings else CheckSeverity.INFO,
        findings=findings,
        duration_ms=duration,
    )
