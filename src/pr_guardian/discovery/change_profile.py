from __future__ import annotations

from pr_guardian.config.schema import FileRolesConfig
from pr_guardian.discovery.dependency_change import (
    is_dependency_lockfile,
    manifest_change_adds_dependency,
    manifest_change_removes_dependency,
)
from pr_guardian.discovery.file_roles import classify_file_roles
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    FileRole,
    SecuritySurface,
)
from pr_guardian.models.pr import Diff


def build_change_profile(
    changed_files: list[str],
    diff: Diff,
    security_surface: SecuritySurface,
    blast_radius: BlastRadius,
    file_roles_config: FileRolesConfig,
) -> ChangeProfile:
    """Classify each file by role, then derive aggregate signals."""
    file_roles = classify_file_roles(changed_files, file_roles_config)

    profile = ChangeProfile(file_roles=file_roles)

    all_roles = [roles for roles in file_roles.values()]

    # Aggregate from file roles
    profile.has_production_changes = any(FileRole.PRODUCTION in roles for roles in all_roles)
    profile.has_test_changes = any(FileRole.TEST in roles for roles in all_roles)
    profile.has_docs_only = bool(all_roles) and all(
        roles <= {FileRole.DOCS} for roles in all_roles
    )
    profile.has_config_only = bool(all_roles) and all(
        roles <= {FileRole.CONFIG} for roles in all_roles
    )
    profile.has_generated_only = bool(all_roles) and all(
        roles <= {FileRole.GENERATED} for roles in all_roles
    )

    # Risk signals: combine direct surface + blast radius propagation
    profile.touches_security_surface = (
        security_surface.has_hits() or blast_radius.propagates_to_security
    )
    profile.touches_shared_code = blast_radius.touches_shared_code
    profile.touches_api_boundary = (
        any("input_handling" in security_surface.get_classifications(f) for f in changed_files)
        or blast_radius.propagates_to_api
    )
    profile.touches_data_layer = any(
        "data_access" in security_surface.get_classifications(f) for f in changed_files
    )
    # Content-aware: a manifest *touch* is not a dependency *add*. Only flag when
    # the manifest's diff plausibly adds/changes a dependency (fail-safe to True
    # when a patch is missing or unparseable). This stops release-please-style
    # version bumps in package.json from force-escalating to human review.
    patch_by_path = {df.path: df.patch for df in diff.files}
    dependency_manifests = [
        path for path, roles in file_roles.items() if FileRole.DEPENDENCY in roles
    ]
    profile.adds_dependencies = any(
        manifest_change_adds_dependency(path, patch_by_path.get(path, ""))
        for path in dependency_manifests
    )
    profile.removes_dependencies = any(
        manifest_change_removes_dependency(path, patch_by_path.get(path, ""))
        for path in dependency_manifests
    )
    # Lockfiles are classified GENERATED, not DEPENDENCY, so they are not in
    # file_roles' dependency set — check the changed-file list by name. Any
    # lockfile change is a (often transitive) dependency change.
    profile.changes_dependency_lockfile = any(
        is_dependency_lockfile(path) for path in changed_files
    )

    # Check for new API endpoints (heuristic: new files in controller/handler/api dirs)
    _API_SEGMENTS = {"controllers", "controller", "handlers", "handler", "api", "routes"}
    for df in diff.files:
        if df.status == "added":
            path_segments = set(df.path.split("/"))
            if path_segments & _API_SEGMENTS:
                profile.adds_api_endpoints = True
                break

    # Architecture boundary crossing: count unique top-level dirs in changed prod files
    # Require 3+ distinct modules to avoid false positives on typical multi-folder changes
    prod_modules: set[str] = set()
    for f, roles in file_roles.items():
        if FileRole.PRODUCTION in roles:
            parts = f.split("/")
            if len(parts) >= 2:
                prod_modules.add(parts[0] + "/" + parts[1])
    profile.crosses_architecture_boundary = len(prod_modules) > 2

    # Implied agents: driven by WHAT changed
    profile.implied_agents = set()
    if profile.touches_security_surface:
        profile.implied_agents.add("security_privacy")
    if profile.touches_api_boundary:
        profile.implied_agents.add("security_privacy")
        profile.implied_agents.add("performance")
    if profile.touches_data_layer:
        profile.implied_agents.add("performance")
    if profile.crosses_architecture_boundary:
        profile.implied_agents.add("architecture_intent")

    # Trivial shortcut
    profile.skip_agents = (
        profile.has_docs_only
        or profile.has_generated_only
        or (profile.has_config_only and diff.lines_changed < 5)
    )

    return profile
