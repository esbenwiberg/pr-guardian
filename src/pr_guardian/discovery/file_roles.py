from __future__ import annotations

from fnmatch import fnmatch

from pr_guardian.config.schema import FileRolesConfig
from pr_guardian.models.context import FileRole


# Default conventions when no config provided
DEFAULT_DEPENDENCY_FILES = frozenset({
    "package.json", "requirements.txt", "Pipfile", "pyproject.toml",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle",
    "*.csproj", "packages.config",
})


def classify_file_roles(
    changed_files: list[str],
    file_roles_config: FileRolesConfig,
) -> dict[str, set[FileRole]]:
    """Classify each changed file by role using config patterns."""
    result: dict[str, set[FileRole]] = {}

    for file_path in changed_files:
        roles: set[FileRole] = set()

        if _matches_any(file_path, file_roles_config.test_patterns):
            roles.add(FileRole.TEST)
        if _matches_any(file_path, file_roles_config.docs_patterns):
            roles.add(FileRole.DOCS)
        if _matches_any(file_path, file_roles_config.generated_patterns):
            roles.add(FileRole.GENERATED)
        if _matches_any(file_path, file_roles_config.build_patterns):
            roles.add(FileRole.BUILD)
        if _is_dependency_file(file_path):
            roles.add(FileRole.DEPENDENCY)
        if _is_infra_file(file_path):
            roles.add(FileRole.INFRA)
        if _is_config_file(file_path):
            roles.add(FileRole.CONFIG)

        # Default: if no special role, it's production code
        if not roles:
            roles.add(FileRole.PRODUCTION)

        result[file_path] = roles

    return result


def _matches_any(file_path: str, patterns: list[str]) -> bool:
    return any(fnmatch(file_path, p) for p in patterns)


def _is_dependency_file(file_path: str) -> bool:
    import os
    basename = os.path.basename(file_path)
    if basename in {"package.json", "requirements.txt", "Pipfile", "go.mod",
                    "Cargo.toml", "pom.xml", "build.gradle", "packages.config"}:
        return True
    if basename == "pyproject.toml":
        return True
    return basename.endswith(".csproj")


def _is_infra_file(file_path: str) -> bool:
    infra_patterns = ["**/terraform/**", "**/docker/**", "**/k8s/**",
                      "**/infra/**", "**/.github/**", "**/azure-pipelines*"]
    return _matches_any(file_path, infra_patterns)


def _is_config_file(file_path: str) -> bool:
    config_patterns = ["**/config/**", "**/.env*", "**/settings*",
                       "**/*.config.*", "**/appsettings*"]
    return _matches_any(file_path, config_patterns)
