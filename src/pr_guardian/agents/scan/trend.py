"""Trend agent: analyzes patterns and trends in recent code changes."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class TrendAgent(ScanBaseAgent):
    agent_name = "trend"
    prompt_dir = "recent_changes/trend"
