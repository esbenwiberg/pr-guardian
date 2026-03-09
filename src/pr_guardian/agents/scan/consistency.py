"""Consistency agent: checks for inconsistent patterns across recent changes."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class ConsistencyAgent(ScanBaseAgent):
    agent_name = "consistency"
    prompt_dir = "recent_changes/consistency"
