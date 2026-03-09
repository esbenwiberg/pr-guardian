"""Integration risk agent: identifies risks from multiple changes interacting."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class IntegrationRiskAgent(ScanBaseAgent):
    agent_name = "integration_risk"
    prompt_dir = "recent_changes/integration_risk"
