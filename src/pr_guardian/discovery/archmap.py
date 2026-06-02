from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from pr_guardian.models.context import ArchmapClassification, ArchmapContext, ArchmapFile


def parse_archmap_artifact(
    raw: str | bytes,
    *,
    expected_commit: str = "",
    changed_files: list[str] | None = None,
) -> ArchmapContext:
    """Parse an Archmap export artifact and keep only files changed by this PR."""
    raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        payload: object = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return ArchmapContext(error=f"invalid JSON: {exc.msg}")

    if not isinstance(payload, dict):
        return ArchmapContext(error="artifact root is not an object")

    data: Mapping[str, object] = payload
    if data.get("version") != 1:
        return ArchmapContext(error="unsupported artifact version")

    commit = _optional_string(data.get("commit"))
    if expected_commit and commit and commit != expected_commit:
        return ArchmapContext(
            commit=commit,
            generated_at=_optional_string(data.get("generatedAt")) or "",
            error=f"artifact commit {commit} does not match PR head {expected_commit}",
        )

    files_obj = data.get("files")
    if not isinstance(files_obj, dict):
        return ArchmapContext(commit=commit, error="artifact files is not an object")

    changed = {
        normalized
        for path in (changed_files or [])
        if (normalized := _normalize_path(path))
    }
    files: dict[str, ArchmapFile] = {}
    for raw_path, raw_file in files_obj.items():
        if not isinstance(raw_path, str) or not isinstance(raw_file, dict):
            continue
        path = _normalize_path(raw_path)
        if changed and path not in changed:
            continue

        file_data: Mapping[str, object] = raw_file
        classification = file_data.get("class")
        if classification not in ("leaf", "branch", "hub"):
            continue

        files[path] = ArchmapFile(
            path=path,
            classification=cast(ArchmapClassification, classification),
            ca=_int_value(file_data.get("ca")),
            tca=_int_value(file_data.get("tca")),
            instability=_float_value(file_data.get("instability")),
            risk=_optional_int(file_data.get("risk")),
            overridden=file_data.get("overridden") is True,
            reason=_optional_string(file_data.get("reason")) or "",
            dependents=_string_tuple(file_data.get("dependents")),
        )

    scope_requested, scope_missing = _scope_lists(data.get("scope"))
    return ArchmapContext(
        commit=commit,
        generated_at=_optional_string(data.get("generatedAt")) or "",
        files=files,
        scope_requested=scope_requested,
        scope_missing=scope_missing,
    )


def _normalize_path(path: str) -> str:
    return path.strip().removeprefix("./")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _float_value(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _scope_lists(value: object) -> tuple[list[str], list[str]]:
    if not isinstance(value, dict):
        return [], []
    requested = _string_list(value.get("requested"))
    missing = _string_list(value.get("missing"))
    return requested, missing


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_normalize_path(item) for item in value if isinstance(item, str)]
