from __future__ import annotations

from pathlib import Path


def _find_prompts_dir() -> Path:
    """Resolve prompts directory — works both from source tree and pip-installed package."""
    # Source tree: src/pr_guardian/agents/prompt_composer.py → ../../../../prompts
    source_dir = Path(__file__).parent.parent.parent.parent / "prompts"
    if source_dir.is_dir():
        return source_dir
    # Docker /app layout: prompts/ sits next to src/
    app_dir = Path("/app/prompts")
    if app_dir.is_dir():
        return app_dir
    # Last resort — return the source path (will just find nothing)
    return source_dir


PROMPTS_DIR = _find_prompts_dir()

CROSS_LANGUAGE_SECTION = """
## CROSS-LANGUAGE CONCERNS

When this PR spans multiple languages, check:
- Data contracts between layers (API request/response shapes match frontend types?)
- Shared constants/enums that must stay in sync across languages
- Migration + code changes that must deploy atomically
- Error handling across language boundaries
- Authentication/authorization applied consistently across all endpoints
"""


def load_prompt(relative_path: str) -> str | None:
    """Load a prompt file from the prompts directory."""
    path = PROMPTS_DIR / relative_path
    if path.exists():
        return path.read_text().strip()
    return None


def build_agent_prompt(
    agent_type: str,
    languages: list[str],
    base_override: str | None = None,
    repo_guidelines: str | None = None,
) -> str:
    """Compose system prompt from base + language-specific sections + repo guidelines."""
    parts: list[str] = []

    if base_override:
        parts.append(base_override)
    elif base := load_prompt(f"{agent_type}/base.md"):
        parts.append(base)
    else:
        parts.append(f"You are a {agent_type.replace('_', ' ')} review agent for PR Guardian.")

    if repo_guidelines:
        parts.append(
            "## REPOSITORY REVIEW GUIDELINES\n\n"
            "The following guidelines were provided by the repository maintainers. "
            "Apply them as additional review criteria.\n\n"
            + repo_guidelines
        )

    for lang in languages:
        lang_prompt = load_prompt(f"{agent_type}/{lang}.md")
        if lang_prompt:
            parts.append(f"\n## {lang.upper()}-SPECIFIC REVIEW\n\n{lang_prompt}")

    if len(languages) > 1:
        parts.append(CROSS_LANGUAGE_SECTION)

    return "\n\n---\n\n".join(parts)
