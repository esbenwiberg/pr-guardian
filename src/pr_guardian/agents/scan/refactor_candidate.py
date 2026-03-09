"""Refactor candidate agent: identifies files that would benefit from refactoring."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class RefactorCandidateAgent(ScanBaseAgent):
    agent_name = "refactor_candidate"
    prompt_dir = "maintenance/refactor_candidate"
