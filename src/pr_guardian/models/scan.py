"""Domain models for scan-based reviews (recent changes + maintenance)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pr_guardian.models.findings import Certainty, Severity, Verdict


class ScanType(str, Enum):
    RECENT_CHANGES = "recent_changes"
    MAINTENANCE = "maintenance"


@dataclass
class ScanFinding:
    """A single finding from a scan agent."""
    severity: Severity
    certainty: Certainty
    category: str
    file: str
    line: int | None
    description: str
    suggestion: str = ""
    agent_name: str = ""
    priority: float = 0.0
    last_modified: str | None = None
    effort_estimate: str | None = None  # "small", "medium", "large"


@dataclass
class ScanAgentResult:
    """Output from a single scan agent."""
    agent_name: str
    verdict: Verdict
    findings: list[ScanFinding] = field(default_factory=list)
    summary: str = ""
    error: str | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class ScanResult:
    """Full result of a scan run."""
    scan_id: str
    scan_type: ScanType
    repo: str
    platform: str
    started_at: str
    finished_at: str | None = None
    # Scan parameters
    time_window_days: int = 7
    staleness_months: int = 6
    # Results
    agent_results: list[ScanAgentResult] = field(default_factory=list)
    total_findings: int = 0
    summary: str = ""
    pipeline_log: list[dict] = field(default_factory=list)
    # Cost tracking
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ScanContext:
    """Lightweight context passed to scan agents (replaces ReviewContext)."""
    scan_id: str
    scan_type: ScanType
    repo: str
    platform: str
    # For recent changes
    merged_prs: list[dict] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    changes_by_module: dict[str, list[dict]] = field(default_factory=dict)
    change_summary: str = ""
    time_window_days: int = 7
    # For maintenance
    stale_files: list[dict] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)
    staleness_months: int = 6
