from __future__ import annotations

from pr_guardian.agents.base import BaseAgent


class TestQualityAgent(BaseAgent):
    agent_name = "test_quality"
    prompt_dir = "test_quality"
