from __future__ import annotations

from pathlib import Path

import yaml

from pr_guardian.config.schema import GuardianConfig


_SERVICE_DEFAULTS_PATH = Path(__file__).parent / "defaults.yml"


def load_service_defaults() -> dict:
    if _SERVICE_DEFAULTS_PATH.exists():
        return yaml.safe_load(_SERVICE_DEFAULTS_PATH.read_text()) or {}
    return {}


def load_repo_config(repo_path: Path) -> GuardianConfig:
    """Load review.yml from repo root, merge with service defaults."""
    base = load_service_defaults()

    repo_config_path = repo_path / "review.yml"
    if repo_config_path.exists():
        repo_overrides = yaml.safe_load(repo_config_path.read_text()) or {}
    else:
        repo_overrides = {}

    merged = _deep_merge(base, repo_overrides)
    return GuardianConfig(**merged)


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base. Overrides win for scalars."""
    result = base.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
