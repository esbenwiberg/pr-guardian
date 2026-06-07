"""
Static checks that GitHub runtime GITHUB_TOKEN fallback patterns do not exist
or reappear in the codebase.

These tests scan source files and documents — no network, no DB, no fixtures.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent

# Matches any line that reads GITHUB_TOKEN from the OS environment at runtime.
# Forms detected:
#   os.environ.get("GITHUB_TOKEN"   os.environ["GITHUB_TOKEN"]   os.getenv("GITHUB_TOKEN"
_GITHUB_TOKEN_ENV_RE = re.compile(
    r"""os\.environ(?:\.get)?\(\s*['"]GITHUB_TOKEN['"]"""
    r"""|os\.getenv\(\s*['"]GITHUB_TOKEN['"]"""
)

# Only scan Guardian runtime source — not tests/ or infra/, which legitimately
# reference GITHUB_TOKEN (monkeypatch, compose env-passthrough, etc.).
_RUNTIME_ROOT = ROOT / "src" / "pr_guardian"


def _runtime_violations(pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for py_file in sorted(_RUNTIME_ROOT.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{py_file.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return hits


def test_no_github_token_runtime_fallback_remains() -> None:
    """No runtime source file may read GITHUB_TOKEN from the OS environment.

    The GITHUB_TOKEN env fallback was removed as part of the GitHub App DevX
    Hardening spec (ADR-010).  All GitHub API calls must use installation tokens
    derived from a stored GitHub App Connection.

    ADO_PAT references are unaffected — this check is GitHub-specific.
    """
    violations = _runtime_violations(_GITHUB_TOKEN_ENV_RE)
    assert not violations, (
        "Found GITHUB_TOKEN env-read(s) in Guardian runtime source.\n"
        "Guardian must use GitHub App Connections (not env tokens) for all GitHub "
        "API calls.  See docs/github-app-setup.md.\n\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_current_docs_describe_github_app_only_runtime() -> None:
    """docs/github-app-setup.md must exist and describe GitHub App-only setup.

    Required content:
    - Mention 'GitHub App' (not GITHUB_TOKEN as a runtime credential)
    - Document App ID and private key
    - Document the guardian/review merge-gate check
    - Document @guardian ChatOps
    """
    doc = ROOT / "docs" / "github-app-setup.md"
    assert doc.exists(), (
        "docs/github-app-setup.md is missing.\n"
        "Create it following the GitHub App DevX Hardening spec — it must "
        "guide operators through creating and installing a GitHub App and "
        "linking it to Guardian without using GITHUB_TOKEN."
    )

    text = doc.read_text(encoding="utf-8")

    assert "GitHub App" in text, "docs/github-app-setup.md must mention 'GitHub App'"
    assert "App ID" in text or "app_id" in text, (
        "docs/github-app-setup.md must document the App ID field"
    )
    assert "private key" in text.lower() or "private_key" in text, (
        "docs/github-app-setup.md must document the private key"
    )
    assert "guardian/review" in text, (
        "docs/github-app-setup.md must document the guardian/review merge-gate check"
    )
    assert "@guardian" in text, "docs/github-app-setup.md must document @guardian ChatOps commands"
