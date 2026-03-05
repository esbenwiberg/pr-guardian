from __future__ import annotations

from pr_guardian.agents.base import BaseAgent


class CodeQualityObservabilityAgent(BaseAgent):
    agent_name = "code_quality_observability"
    prompt_dir = "code_quality_observability"
