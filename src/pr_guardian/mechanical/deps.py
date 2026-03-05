from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult


async def run_npm_audit(repo_path: Path) -> MechanicalCheckResult:
    """Run npm audit for JS/TS projects."""
    start = time.monotonic()

    if not (repo_path / "package.json").exists():
        return MechanicalCheckResult(
            tool="npm-audit", passed=True,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "audit", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_path),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except FileNotFoundError:
        return MechanicalCheckResult(
            tool="npm-audit", passed=True,
            error="npm not installed — skipped",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except asyncio.TimeoutError:
        return MechanicalCheckResult(
            tool="npm-audit", passed=False,
            error="npm audit timed out",
            severity=CheckSeverity.ERROR,
            duration_ms=60_000,
        )

    findings: list[CheckFinding] = []
    try:
        data = json.loads(stdout.decode())
        for vuln_name, vuln_info in data.get("vulnerabilities", {}).items():
            sev = vuln_info.get("severity", "moderate")
            check_sev = CheckSeverity.ERROR if sev in ("critical", "high") else CheckSeverity.WARNING
            findings.append(CheckFinding(
                file="package.json", line=None,
                rule=f"npm-vuln-{vuln_name}",
                message=f"{vuln_name}: {vuln_info.get('title', sev)} ({sev})",
                severity=check_sev,
            ))
    except (json.JSONDecodeError, KeyError):
        pass

    has_errors = any(f.severity == CheckSeverity.ERROR for f in findings)
    duration = int((time.monotonic() - start) * 1000)

    return MechanicalCheckResult(
        tool="npm-audit",
        passed=not has_errors,
        severity=CheckSeverity.ERROR if has_errors else CheckSeverity.INFO,
        findings=findings,
        duration_ms=duration,
    )


async def run_pip_audit(repo_path: Path) -> MechanicalCheckResult:
    """Run pip-audit for Python projects."""
    start = time.monotonic()

    has_python_deps = any(
        (repo_path / f).exists()
        for f in ["requirements.txt", "Pipfile", "pyproject.toml"]
    )
    if not has_python_deps:
        return MechanicalCheckResult(
            tool="pip-audit", passed=True,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "pip-audit", "--format", "json", "--strict",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_path),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except FileNotFoundError:
        return MechanicalCheckResult(
            tool="pip-audit", passed=True,
            error="pip-audit not installed — skipped",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except asyncio.TimeoutError:
        return MechanicalCheckResult(
            tool="pip-audit", passed=False,
            error="pip-audit timed out",
            severity=CheckSeverity.ERROR,
            duration_ms=120_000,
        )

    findings: list[CheckFinding] = []
    try:
        data = json.loads(stdout.decode())
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                findings.append(CheckFinding(
                    file="requirements.txt", line=None,
                    rule=vuln.get("id", "unknown"),
                    message=f"{dep['name']}: {vuln.get('description', 'vulnerability')}",
                    severity=CheckSeverity.ERROR,
                ))
    except (json.JSONDecodeError, KeyError):
        pass

    duration = int((time.monotonic() - start) * 1000)
    return MechanicalCheckResult(
        tool="pip-audit",
        passed=len(findings) == 0,
        severity=CheckSeverity.ERROR if findings else CheckSeverity.INFO,
        findings=findings,
        duration_ms=duration,
    )
