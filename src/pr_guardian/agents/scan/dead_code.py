"""Dead code agent: identifies likely dead or unused code in stale files."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class DeadCodeAgent(ScanBaseAgent):
    agent_name = "dead_code"
    prompt_dir = "maintenance/dead_code"
