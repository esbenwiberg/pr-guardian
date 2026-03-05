from __future__ import annotations

from fnmatch import fnmatch

from pr_guardian.config.schema import SecuritySurfaceConfig
from pr_guardian.models.context import SecuritySurface


def build_security_surface(
    config: SecuritySurfaceConfig,
    changed_files: list[str],
) -> SecuritySurface:
    """Match changed files against security surface patterns from config."""
    surface = SecuritySurface()

    pattern_map = {
        "security_critical": config.security_critical,
        "input_handling": config.input_handling,
        "data_access": config.data_access,
        "configuration": config.configuration,
        "infrastructure": config.infrastructure,
    }

    for file_path in changed_files:
        for classification, globs in pattern_map.items():
            if any(fnmatch(file_path, g) for g in globs):
                surface.classify(file_path, classification)

    return surface
