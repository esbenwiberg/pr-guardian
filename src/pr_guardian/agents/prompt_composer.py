from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"

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
) -> str:
    """Compose system prompt from base + language-specific sections."""
    parts: list[str] = []

    if base_override:
        parts.append(base_override)
    elif base := load_prompt(f"{agent_type}/base.md"):
        parts.append(base)
    else:
        parts.append(f"You are a {agent_type.replace('_', ' ')} review agent for PR Guardian.")

    for lang in languages:
        lang_prompt = load_prompt(f"{agent_type}/{lang}.md")
        if lang_prompt:
            parts.append(f"\n## {lang.upper()}-SPECIFIC REVIEW\n\n{lang_prompt}")

    if len(languages) > 1:
        parts.append(CROSS_LANGUAGE_SECTION)

    return "\n\n---\n\n".join(parts)
