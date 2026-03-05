from __future__ import annotations

from dataclasses import dataclass, field


RUNTIME_LANGUAGES = frozenset({
    "python", "typescript", "javascript", "csharp", "go",
    "java", "kotlin", "rust", "sql", "shell", "powershell",
})


@dataclass
class LanguageMap:
    """Files grouped by detected language."""
    languages: dict[str, list[str]] = field(default_factory=dict)
    primary_language: str = "unknown"
    language_count: int = 0
    cross_stack: bool = False

    def files(self, lang: str) -> list[str]:
        return self.languages.get(lang, [])

    def has(self, lang: str) -> bool:
        return lang in self.languages
