from __future__ import annotations

from pr_guardian.agents.base import BaseAgent


class HotspotAgent(BaseAgent):
    agent_name = "hotspot"
    prompt_dir = "hotspot"
