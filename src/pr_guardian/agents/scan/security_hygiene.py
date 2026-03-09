"""Security hygiene agent: checks stale files for outdated security patterns."""
from __future__ import annotations

from pr_guardian.agents.scan.base import ScanBaseAgent


class SecurityHygieneAgent(ScanBaseAgent):
    agent_name = "security_hygiene"
    prompt_dir = "maintenance/security_hygiene"
