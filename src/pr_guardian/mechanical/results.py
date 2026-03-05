from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CheckSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class CheckFinding:
    """A single finding from a mechanical check."""
    file: str
    line: int | None
    rule: str
    message: str
    severity: CheckSeverity = CheckSeverity.WARNING


@dataclass
class MechanicalCheckResult:
    """Result from a single mechanical check tool."""
    tool: str
    passed: bool
    severity: CheckSeverity = CheckSeverity.INFO
    findings: list[CheckFinding] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0

    @property
    def has_blocking_findings(self) -> bool:
        return any(f.severity == CheckSeverity.ERROR for f in self.findings)
