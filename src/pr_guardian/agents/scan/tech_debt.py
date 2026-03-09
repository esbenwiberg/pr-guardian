"""Tech debt agent: identifies technical debt in stale files."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class TechDebtAgent(ScanBaseAgent):
    agent_name = "tech_debt"
    prompt_dir = "maintenance/tech_debt"
