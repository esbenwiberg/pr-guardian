"""Content-aware dependency-change detection.

The triage `adds_dependencies` signal used to fire whenever a dependency
*manifest* file was touched — but touching `package.json` to bump the
project's own `version` (release-please, hand bumps) is not the same as adding
a dependency. That false positive force-escalated trivial release PRs to human
review.

This module inspects the manifest *diff* and answers a narrower question: does
this change plausibly add or change a dependency? It is deliberately
**fail-safe** — for a review gate, over-escalating is acceptable, under-
escalating is not. So when a patch is missing, truncated, or we cannot confi-
dently parse it, we return ``True`` (keep escalating). We only return ``False``
when we can positively prove the changed lines touch non-dependency content
(project metadata, scripts, build config, comments, structure).

The decision is based on *added* lines only: the signal is "adds dependencies".
A version bump of an existing dependency still shows an added line, so it is
caught; a pure removal is not treated as an addition.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Literal

LineKind = Literal["add", "del", "ctx"]


def manifest_change_adds_dependency(path: str, patch: str) -> bool:
    """True if the manifest patch plausibly adds/changes a dependency.

    Fail-safe: returns True for an empty/unparseable patch or an unrecognized
    manifest, so the caller never *under*-escalates. Returns False only when the
    added lines are provably non-dependency content (e.g. a project version
    bump).
    """
    # No patch to inspect (large/binary file omitted by the platform, or a
    # synthetic diff). Can't prove it's clean → keep escalating.
    if not patch.strip():
        return True

    detector = _detector_for(path)
    if detector is None:
        # Classified as a dependency file but we have no parser for it → safe.
        return True
    return detector(patch)


def _detector_for(path: str):
    basename = os.path.basename(path).lower()
    if basename == "package.json":
        return _npm
    if basename in {"requirements.txt", "pipfile"} or basename.startswith("requirements"):
        # requirements*.txt and Pipfile (Pipfile is TOML but its package
        # sections map cleanly onto the requirements-style "any entry is a dep").
        return _pipfile if basename == "pipfile" else _requirements
    if basename == "pyproject.toml":
        return _pyproject
    if basename == "cargo.toml":
        return _cargo
    if basename == "go.mod":
        return _go_mod
    if basename == "pom.xml":
        return _pom
    if basename == "build.gradle":
        return _gradle
    if basename == "packages.config":
        return _packages_config
    if basename.endswith(".csproj"):
        return _csproj
    return None


def _iter_patch_lines(patch: str) -> Iterator[tuple[LineKind, str]]:
    """Yield (kind, text) for diff body lines, stripping the +/-/space prefix.

    Hunk headers (@@), file headers (+++/---), and "\\ No newline" markers are
    skipped.
    """
    for raw in patch.splitlines():
        if raw.startswith(("@@", "+++", "---", "\\")):
            continue
        if raw.startswith("+"):
            yield "add", raw[1:]
        elif raw.startswith("-"):
            yield "del", raw[1:]
        elif raw.startswith(" "):
            yield "ctx", raw[1:]
        elif raw == "":
            yield "ctx", ""
        # any other leading char is not part of a unified-diff body → skip


# --------------------------------------------------------------------------- #
# npm — package.json
# --------------------------------------------------------------------------- #

_NPM_DEP_SECTIONS = frozenset(
    {
        "dependencies",
        "devdependencies",
        "peerdependencies",
        "optionaldependencies",
        "bundleddependencies",
        "bundledependencies",
        "overrides",
        "resolutions",
    }
)

# Top-level package.json keys that are NOT dependencies. Used as a fallback when
# section context is unavailable.
_NPM_METADATA_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "keywords",
        "homepage",
        "bugs",
        "license",
        "author",
        "contributors",
        "funding",
        "files",
        "main",
        "browser",
        "module",
        "types",
        "typings",
        "exports",
        "imports",
        "bin",
        "man",
        "directories",
        "repository",
        "scripts",
        "config",
        "type",
        "private",
        "publishconfig",
        "workspaces",
        "engines",
        "os",
        "cpu",
        "packagemanager",
        "sideeffects",
        "browserslist",
    }
)

_NPM_TOP_KEY = re.compile(r'^  "([^"]+)"\s*:')
_NPM_ENTRY = re.compile(r'^\s*"([^"]+)"\s*:')


def _npm(patch: str) -> bool:
    # Current top-level section, tracked from 2-space-indented keys (standard
    # JSON formatting, which release-please / npm / prettier all emit).
    section: str | None = None
    for kind, text in _iter_patch_lines(patch):
        top = _NPM_TOP_KEY.match(text)
        if top:
            section = top.group(1).lower()
        if kind == "add" and _npm_add_is_dep(text, section):
            return True
    return False


def _npm_add_is_dep(text: str, section: str | None) -> bool:
    stripped = text.strip()
    if not stripped or stripped in {"{", "}", "},", "[", "]", "],"}:
        return False

    top = _NPM_TOP_KEY.match(text)
    if top:
        # A top-level key line: it's a section header, not an entry. Only the
        # opening of a dependency block itself counts (whole-block add).
        return top.group(1).lower() in _NPM_DEP_SECTIONS

    # Nested entry line — classify by enclosing section when we know it.
    if section in _NPM_DEP_SECTIONS:
        return True
    if section is not None:
        # Inside a known non-dependency block (scripts, config, engines, ...).
        return False

    # Section unknown (its opener wasn't in the patch window). Fall back to the
    # metadata allowlist: an unknown "name": ... entry is treated as a possible
    # dependency (safe direction).
    entry = _NPM_ENTRY.match(text)
    if entry:
        return entry.group(1).lower() not in _NPM_METADATA_KEYS
    return False


# --------------------------------------------------------------------------- #
# Python — requirements*.txt
# --------------------------------------------------------------------------- #


def _requirements(patch: str) -> bool:
    for kind, text in _iter_patch_lines(patch):
        if kind != "add":
            continue
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Any added requirement spec, include (-r/-c), or option line touches the
        # resolved dependency set.
        return True
    return False


# --------------------------------------------------------------------------- #
# Python — pyproject.toml / Pipfile (TOML tables)
# --------------------------------------------------------------------------- #

_TOML_TABLE = re.compile(r"^\s*\[+([^\]]+)\]+")
_TOML_ARRAY_KEY = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*=\s*\[")


def _pyproject(patch: str) -> bool:
    section: str | None = None
    in_dep_array = False
    for kind, text in _iter_patch_lines(patch):
        table = _TOML_TABLE.match(text)
        if table:
            section = table.group(1).strip().lower()
            in_dep_array = False
        arr = _TOML_ARRAY_KEY.match(text)
        if arr:
            key = arr.group(1).lower()
            in_dep_array = "dependencies" in key or key == "requires"
        if text.strip() == "]":
            in_dep_array = False

        if kind == "add" and _pyproject_add_is_dep(text, section, in_dep_array):
            return True
    return False


def _pyproject_add_is_dep(text: str, section: str | None, in_dep_array: bool) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if in_dep_array:
        return True
    if _TOML_ARRAY_KEY.match(text):
        key = _TOML_ARRAY_KEY.match(text).group(1).lower()  # type: ignore[union-attr]
        if "dependencies" in key or key == "requires":
            return True
    if section and "dependencies" in section:
        return True
    if section == "build-system":
        # Only the requires array (already handled) and its string entries.
        return bool(re.match(r"^\s*requires\s*=", text))
    return False


def _pipfile(patch: str) -> bool:
    section: str | None = None
    for kind, text in _iter_patch_lines(patch):
        table = _TOML_TABLE.match(text)
        if table:
            section = table.group(1).strip().lower()
        if kind == "add":
            stripped = text.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if section in {"packages", "dev-packages"}:
                return True
    return False


# --------------------------------------------------------------------------- #
# Rust — Cargo.toml
# --------------------------------------------------------------------------- #


def _cargo(patch: str) -> bool:
    section: str | None = None
    for kind, text in _iter_patch_lines(patch):
        table = _TOML_TABLE.match(text)
        if table:
            section = table.group(1).strip().lower()
        if kind == "add":
            stripped = text.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if section and "dependencies" in section:
                return True
    return False


# --------------------------------------------------------------------------- #
# Go — go.mod
# --------------------------------------------------------------------------- #

_GO_BLOCK = re.compile(r"^\s*(require|replace|exclude)\s*\(")
_GO_DIRECTIVE = re.compile(r"^\s*(require|replace|exclude)\s")


def _go_mod(patch: str) -> bool:
    in_block = False
    for kind, text in _iter_patch_lines(patch):
        if _GO_BLOCK.match(text):
            in_block = True
        elif text.strip() == ")":
            in_block = False

        if kind == "add":
            stripped = text.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped == ")" or _GO_BLOCK.match(text):
                continue
            if in_block or _GO_DIRECTIVE.match(text):
                return True
    return False


# --------------------------------------------------------------------------- #
# Maven — pom.xml
# --------------------------------------------------------------------------- #


def _pom(patch: str) -> bool:
    in_deps = False
    for kind, text in _iter_patch_lines(patch):
        if "<dependencies>" in text or "<dependencyManagement>" in text:
            in_deps = True
        if "</dependencies>" in text or "</dependencyManagement>" in text:
            in_deps = False

        if kind == "add":
            if in_deps:
                # Ignore pure structural/whitespace additions inside the block.
                if text.strip():
                    return True
            elif "<artifactId>" in text or "<groupId>" in text:
                # Dependency coordinates even when the block opener is outside
                # the patch window.
                return True
    return False


# --------------------------------------------------------------------------- #
# Gradle — build.gradle
# --------------------------------------------------------------------------- #

_GRADLE_DEPS_OPEN = re.compile(r"^\s*dependencies\s*\{")
_GRADLE_CONFIG = re.compile(
    r"^(implementation|api|compileOnly|compileOnlyApi|runtimeOnly|"
    r"testImplementation|testRuntimeOnly|testCompileOnly|androidTestImplementation|"
    r"annotationProcessor|kapt|ksp|classpath|compile|testCompile|provided)\b"
)


def _gradle(patch: str) -> bool:
    in_deps = False
    for kind, text in _iter_patch_lines(patch):
        if _GRADLE_DEPS_OPEN.search(text):
            in_deps = True
        elif text.strip() == "}":
            in_deps = False

        if kind == "add":
            stripped = text.strip()
            if not stripped or stripped.startswith("//") or stripped in {"}", "{"}:
                continue
            if in_deps or _GRADLE_CONFIG.match(stripped):
                return True
    return False


# --------------------------------------------------------------------------- #
# NuGet — packages.config / *.csproj
# --------------------------------------------------------------------------- #


def _packages_config(patch: str) -> bool:
    for kind, text in _iter_patch_lines(patch):
        if kind == "add" and "<package " in text:
            return True
    return False


def _csproj(patch: str) -> bool:
    for kind, text in _iter_patch_lines(patch):
        if kind == "add" and ("<PackageReference" in text or "<PackageVersion" in text):
            return True
    return False
