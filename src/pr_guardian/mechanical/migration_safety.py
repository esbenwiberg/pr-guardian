from __future__ import annotations

import re
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult

# Dangerous SQL migration patterns
DANGEROUS_PATTERNS = [
    (re.compile(r'\bDROP\s+TABLE\b', re.I), "DROP TABLE detected", CheckSeverity.ERROR),
    (re.compile(r'\bDROP\s+COLUMN\b', re.I), "DROP COLUMN detected", CheckSeverity.ERROR),
    (re.compile(r'\bTRUNCATE\b', re.I), "TRUNCATE detected", CheckSeverity.ERROR),
    (re.compile(r'\bDELETE\s+FROM\b(?!.*\bWHERE\b)', re.I),
     "DELETE without WHERE clause", CheckSeverity.ERROR),
    (re.compile(r'\bALTER\s+TABLE\b.*\bRENAME\b', re.I),
     "Table/column rename — may break consumers", CheckSeverity.WARNING),
    (re.compile(r'\bALTER\s+TABLE\b.*\bALTER\s+COLUMN\b.*\bTYPE\b', re.I),
     "Column type change — may lose data", CheckSeverity.WARNING),
    (re.compile(r'\bNOT\s+NULL\b', re.I),
     "Adding NOT NULL — will fail if existing data has NULLs", CheckSeverity.WARNING),
    (re.compile(r'\bCREATE\s+INDEX\b(?!.*\bCONCURRENTLY\b)', re.I),
     "CREATE INDEX without CONCURRENTLY — will lock table", CheckSeverity.WARNING),
]


def _is_migration(file_path: str) -> bool:
    lower = file_path.lower()
    return any(seg in lower for seg in ("migration", "alembic", "flyway", "liquibase")) and (
        lower.endswith(".sql") or lower.endswith(".py")
    )


async def run_migration_safety(
    repo_path: Path,
    changed_files: list[str],
) -> MechanicalCheckResult:
    """Check SQL migrations for dangerous patterns."""
    start = time.monotonic()
    migration_files = [f for f in changed_files if _is_migration(f)]

    if not migration_files:
        return MechanicalCheckResult(
            tool="migration-safety", passed=True,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    findings: list[CheckFinding] = []

    for file_path in migration_files:
        full_path = repo_path / file_path
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text(errors="replace")
        except OSError:
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            for pattern, message, severity in DANGEROUS_PATTERNS:
                if pattern.search(line):
                    findings.append(CheckFinding(
                        file=file_path, line=line_num,
                        rule="migration-safety", message=message,
                        severity=severity,
                    ))

    has_errors = any(f.severity == CheckSeverity.ERROR for f in findings)
    duration = int((time.monotonic() - start) * 1000)

    return MechanicalCheckResult(
        tool="migration-safety",
        passed=not has_errors,
        severity=CheckSeverity.ERROR if has_errors else (
            CheckSeverity.WARNING if findings else CheckSeverity.INFO
        ),
        findings=findings,
        duration_ms=duration,
    )
