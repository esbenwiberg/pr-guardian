from __future__ import annotations

import re
import time
from pathlib import Path

from pr_guardian.mechanical.results import CheckFinding, CheckSeverity, MechanicalCheckResult

# PII patterns in log/print statements
PII_LOG_PATTERNS = [
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:password|passwd|secret)', re.I),
     "password/secret in log statement", CheckSeverity.ERROR),
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:ssn|social.security)', re.I),
     "SSN in log statement", CheckSeverity.ERROR),
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:credit.card|card.number)', re.I),
     "credit card in log statement", CheckSeverity.ERROR),
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:email|e-mail)', re.I),
     "email address in log statement", CheckSeverity.WARNING),
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:phone|mobile)', re.I),
     "phone number in log statement", CheckSeverity.WARNING),
    (re.compile(r'(?:log|logger|console\.log|print)\s*[.(].*(?:user\.name|customer\.|patient\.)', re.I),
     "PII entity in log statement", CheckSeverity.WARNING),
]

# Real-looking PII in test fixtures
TEST_PII_PATTERNS = [
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), "real-looking email in test data", CheckSeverity.WARNING),
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), "SSN-like pattern in test data", CheckSeverity.ERROR),
    (re.compile(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'), "credit card-like pattern", CheckSeverity.ERROR),
]


async def run_pii_scanner(
    repo_path: Path,
    changed_files: list[str],
) -> MechanicalCheckResult:
    """Scan changed files for PII in logs and test fixtures."""
    start = time.monotonic()
    findings: list[CheckFinding] = []

    for file_path in changed_files:
        full_path = repo_path / file_path
        if not full_path.exists() or not full_path.is_file():
            continue

        try:
            content = full_path.read_text(errors="replace")
        except OSError:
            continue

        is_test = any(seg in file_path for seg in ("test", "spec", "fixture", "__tests__"))

        for line_num, line in enumerate(content.splitlines(), 1):
            # Check log patterns in all files
            for pattern, description, severity in PII_LOG_PATTERNS:
                if pattern.search(line):
                    findings.append(CheckFinding(
                        file=file_path, line=line_num,
                        rule="pii-in-logs", message=description,
                        severity=severity,
                    ))

            # Check test PII patterns only in test files
            if is_test:
                for pattern, description, severity in TEST_PII_PATTERNS:
                    if pattern.search(line):
                        findings.append(CheckFinding(
                            file=file_path, line=line_num,
                            rule="pii-in-tests", message=description,
                            severity=severity,
                        ))

    has_errors = any(f.severity == CheckSeverity.ERROR for f in findings)
    duration = int((time.monotonic() - start) * 1000)

    return MechanicalCheckResult(
        tool="pii-scanner",
        passed=not has_errors,
        severity=CheckSeverity.ERROR if has_errors else (
            CheckSeverity.WARNING if findings else CheckSeverity.INFO
        ),
        findings=findings,
        duration_ms=duration,
    )
