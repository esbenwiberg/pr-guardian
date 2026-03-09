"""Architecture drift agent: detects gradual erosion of architecture boundaries."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class ArchitectureDriftAgent(ScanBaseAgent):
    agent_name = "architecture_drift"
    prompt_dir = "recent_changes/architecture_drift"
