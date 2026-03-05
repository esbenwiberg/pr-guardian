from __future__ import annotations

import os

from pr_guardian.models.languages import LanguageMap, RUNTIME_LANGUAGES

EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".cs": "csharp",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".sql": "sql",
    ".tf": "terraform",
    ".bicep": "bicep",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".csproj": "xml",
    ".md": "markdown",
    ".toml": "toml",
    ".ini": "config",
    ".cfg": "config",
    ".env": "config",
    ".lock": "lockfile",
}

FILENAME_MAP: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    "Jenkinsfile": "groovy",
    "Vagrantfile": "ruby",
    ".gitignore": "config",
    ".dockerignore": "config",
}


def identify_language(file_path: str) -> str:
    """Identify language for a single file path. Pure lookup, ~0ms."""
    basename = os.path.basename(file_path)

    # Check filename matches first (Dockerfile, Makefile, etc.)
    if basename in FILENAME_MAP:
        return FILENAME_MAP[basename]

    # Check if basename starts with known prefix (e.g. Dockerfile.prod)
    for prefix, lang in FILENAME_MAP.items():
        if basename.startswith(prefix):
            return lang

    # Extension-based lookup
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    return EXTENSION_MAP.get(ext, "unknown")


def detect_languages(changed_files: list[str]) -> LanguageMap:
    """Group changed files by language. Pure dict lookup."""
    if not changed_files:
        return LanguageMap()

    groups: dict[str, list[str]] = {}
    for path in changed_files:
        lang = identify_language(path)
        groups.setdefault(lang, []).append(path)

    # Primary = language with most files
    primary = max(groups, key=lambda lang: len(groups[lang]))

    # Cross-stack: >1 runtime language
    runtime_langs = {lang for lang in groups if lang in RUNTIME_LANGUAGES}

    return LanguageMap(
        languages=groups,
        primary_language=primary,
        language_count=len(groups),
        cross_stack=len(runtime_langs) > 1,
    )
