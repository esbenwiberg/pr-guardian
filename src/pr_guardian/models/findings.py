from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Certainty(str, Enum):
    DETECTED = "detected"
    SUSPECTED = "suspected"
    UNCERTAIN = "uncertain"


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FLAG_HUMAN = "flag_human"


@dataclass
class EvidenceBasis:
    """Structured evidence supporting a finding."""
    saw_full_context: bool = False
    pattern_match: bool = False
    cwe_id: str | None = None
    similar_code_in_repo: bool = False
    suggestion_is_concrete: bool = False
    cross_references: int = 0


@dataclass
class Finding:
    """A single finding from an AI agent."""
    severity: Severity
    certainty: Certainty
    category: str
    language: str
    file: str
    line: int | None
    description: str
    suggestion: str = ""
    cwe: str | None = None
    compliance: str | None = None
    evidence_basis: EvidenceBasis = field(default_factory=EvidenceBasis)


@dataclass
class AgentResult:
    """Output from a single AI agent."""
    agent_name: str
    verdict: Verdict
    languages_reviewed: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    cross_language_findings: list[Finding] = field(default_factory=list)
    error: str | None = None

    # Optional agent-specific extras
    extras: dict = field(default_factory=dict)
