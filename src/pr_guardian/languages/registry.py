from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageToolConfig:
    """Tools available for a specific language."""
    semgrep_rules: list[str] = field(default_factory=list)
    mechanical_tools: dict[str, bool] = field(default_factory=dict)
    security_prompt: str = ""
    always_trigger_agents: list[str] = field(default_factory=list)


# Default tool configurations per language
DEFAULT_TOOL_CONFIGS: dict[str, LanguageToolConfig] = {
    "python": LanguageToolConfig(
        semgrep_rules=["p/python", "p/owasp-top-ten"],
        mechanical_tools={"ruff": True, "bandit": True, "pip_audit": True},
    ),
    "typescript": LanguageToolConfig(
        semgrep_rules=["p/typescript", "p/owasp-top-ten"],
        mechanical_tools={"biome": True, "dependency_cruiser": True, "npm_audit": True},
    ),
    "javascript": LanguageToolConfig(
        semgrep_rules=["p/javascript", "p/owasp-top-ten"],
        mechanical_tools={"biome": True, "npm_audit": True},
    ),
    "csharp": LanguageToolConfig(
        semgrep_rules=["p/csharp"],
        mechanical_tools={"security_scan": True},
    ),
    "go": LanguageToolConfig(
        semgrep_rules=["p/go"],
        mechanical_tools={"golangci_lint": True, "govulncheck": True},
    ),
    "sql": LanguageToolConfig(
        semgrep_rules=["p/sql"],
        mechanical_tools={"sqlfluff": True},
        always_trigger_agents=["security_privacy"],
    ),
    "terraform": LanguageToolConfig(
        semgrep_rules=[],
        mechanical_tools={"tflint": True, "checkov": True},
        always_trigger_agents=["security_privacy"],
    ),
    "dockerfile": LanguageToolConfig(
        semgrep_rules=[],
        mechanical_tools={"hadolint": True},
        always_trigger_agents=["security_privacy"],
    ),
    "shell": LanguageToolConfig(
        semgrep_rules=[],
        mechanical_tools={"shellcheck": True},
    ),
}


def get_tool_config(language: str) -> LanguageToolConfig:
    """Get tool config for a language, with empty defaults for unknown languages."""
    return DEFAULT_TOOL_CONFIGS.get(language, LanguageToolConfig())
