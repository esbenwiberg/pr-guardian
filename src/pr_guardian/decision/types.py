from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StickyTriggerKind = Literal[
    "new_dep",
    "path_risk",
    "hotspot",
    "trust_tier",
    "repo_risk",
    "high_diff",
    "archmap_hub",
]


@dataclass(frozen=True)
class StickyTrigger:
    kind: StickyTriggerKind
    label: str  # short human label, e.g. "New dependency added"
    source: str  # stable id for verification, e.g. "requests==2.32.3", "src/auth/"
    reason: str  # one-line explanation
