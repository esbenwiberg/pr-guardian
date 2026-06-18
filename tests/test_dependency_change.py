"""Content-aware dependency-change detection.

Each manifest gets two anchor cases: a *version bump / metadata edit* that must
NOT be read as a dependency add (the release-please false positive we are
killing), and a *real dependency add* that must still flag. Plus fail-safe
behavior and an end-to-end check through ``build_change_profile``.
"""

import pytest

from pr_guardian.config.schema import FileRolesConfig
from pr_guardian.discovery.change_profile import build_change_profile
from pr_guardian.discovery.dependency_change import manifest_change_adds_dependency
from pr_guardian.models.context import BlastRadius, SecuritySurface
from pr_guardian.models.pr import Diff, DiffFile

# The actual release-please #320 diff: a project version bump only.
NPM_VERSION_BUMP = """\
@@ -1,6 +1,6 @@
 {
   "name": "portfolio-simulation",
-  "version": "0.3.0",
+  "version": "0.3.1",
   "sentimental": 1,
   "private": true,
"""

NPM_DEP_ADD = """\
@@ -10,6 +10,7 @@
   "dependencies": {
     "axios": "^1.6.0",
+    "left-pad": "^1.3.0",
     "react": "^18.0.0"
   },
"""

NPM_SCRIPTS_EDIT = """\
@@ -5,6 +5,7 @@
   "scripts": {
     "build": "tsc",
+    "lint": "eslint .",
     "test": "jest"
   },
"""

NPM_NEW_DEP_BLOCK = """\
@@ -8,3 +8,5 @@
   "private": true,
+  "dependencies": {
+    "left-pad": "^1.3.0"
+  }
 }
"""

PYPROJECT_VERSION_BUMP = """\
@@ -1,5 +1,5 @@
 [project]
 name = "foo"
-version = "1.2.3"
+version = "1.2.4"
 requires-python = ">=3.12"
"""

PYPROJECT_DEP_ADD = """\
@@ -5,6 +5,7 @@
 dependencies = [
   "httpx>=0.27",
+  "pydantic>=2",
 ]
"""

PYPROJECT_POETRY_DEP_ADD = """\
@@ -3,4 +3,5 @@
 [tool.poetry.dependencies]
 python = "^3.12"
+httpx = "^0.27"
"""

REQUIREMENTS_DEP_ADD = """\
@@ -1,2 +1,3 @@
 flask==3.0.0
+requests==2.31.0
"""

REQUIREMENTS_COMMENT_ONLY = """\
@@ -1,2 +1,3 @@
 flask==3.0.0
+# pin for the security advisory
"""

CARGO_VERSION_BUMP = """\
@@ -1,4 +1,4 @@
 [package]
 name = "foo"
-version = "0.1.0"
+version = "0.2.0"
"""

CARGO_DEP_ADD = """\
@@ -5,3 +5,4 @@
 [dependencies]
 serde = "1"
+tokio = "1"
"""

GO_MOD_DEP_ADD = """\
@@ -3,4 +3,5 @@
 require (
 \tgithub.com/foo/bar v1.2.3
+\tgithub.com/baz/qux v0.1.0
 )
"""

GO_MOD_MODULE_RENAME = """\
@@ -1,3 +1,3 @@
-module github.com/old/name
+module github.com/new/name

 go 1.22
"""

POM_VERSION_BUMP = """\
@@ -4,7 +4,7 @@
   <artifactId>my-app</artifactId>
-  <version>1.0.0</version>
+  <version>1.0.1</version>
   <packaging>jar</packaging>
"""

POM_DEP_ADD = """\
@@ -10,6 +10,10 @@
   <dependencies>
+    <dependency>
+      <groupId>org.foo</groupId>
+      <artifactId>bar</artifactId>
+    </dependency>
   </dependencies>
"""

GRADLE_VERSION_BUMP = """\
@@ -1,3 +1,3 @@
-version = '1.0.0'
+version = '1.0.1'
 group = 'com.example'
"""

GRADLE_DEP_ADD = """\
@@ -5,5 +5,6 @@
 dependencies {
     implementation 'com.google.guava:guava:32.0'
+    implementation 'org.apache.commons:commons-lang3:3.12'
 }
"""

CSPROJ_VERSION_BUMP = """\
@@ -2,7 +2,7 @@
   <PropertyGroup>
-    <Version>1.0.0</Version>
+    <Version>1.0.1</Version>
   </PropertyGroup>
"""

CSPROJ_DEP_ADD = """\
@@ -8,5 +8,6 @@
   <ItemGroup>
     <PackageReference Include="Newtonsoft.Json" Version="13.0.1" />
+    <PackageReference Include="Serilog" Version="3.0.0" />
   </ItemGroup>
"""

PACKAGES_CONFIG_DEP_ADD = """\
@@ -2,4 +2,5 @@
 <packages>
   <package id="EntityFramework" version="6.4.4" />
+  <package id="Newtonsoft.Json" version="13.0.1" />
 </packages>
"""


@pytest.mark.parametrize(
    "path,patch",
    [
        ("package.json", NPM_VERSION_BUMP),
        ("package.json", NPM_SCRIPTS_EDIT),
        ("pyproject.toml", PYPROJECT_VERSION_BUMP),
        ("requirements.txt", REQUIREMENTS_COMMENT_ONLY),
        ("Cargo.toml", CARGO_VERSION_BUMP),
        ("go.mod", GO_MOD_MODULE_RENAME),
        ("pom.xml", POM_VERSION_BUMP),
        ("build.gradle", GRADLE_VERSION_BUMP),
        ("src/app/App.csproj", CSPROJ_VERSION_BUMP),
    ],
)
def test_non_dependency_changes_are_not_flagged(path, patch):
    assert manifest_change_adds_dependency(path, patch) is False


@pytest.mark.parametrize(
    "path,patch",
    [
        ("package.json", NPM_DEP_ADD),
        ("package.json", NPM_NEW_DEP_BLOCK),
        ("pyproject.toml", PYPROJECT_DEP_ADD),
        ("pyproject.toml", PYPROJECT_POETRY_DEP_ADD),
        ("requirements.txt", REQUIREMENTS_DEP_ADD),
        ("Cargo.toml", CARGO_DEP_ADD),
        ("go.mod", GO_MOD_DEP_ADD),
        ("pom.xml", POM_DEP_ADD),
        ("build.gradle", GRADLE_DEP_ADD),
        ("src/app/App.csproj", CSPROJ_DEP_ADD),
        ("packages.config", PACKAGES_CONFIG_DEP_ADD),
    ],
)
def test_real_dependency_adds_are_flagged(path, patch):
    assert manifest_change_adds_dependency(path, patch) is True


@pytest.mark.parametrize(
    "patch",
    ["", "   ", "\n"],
)
def test_missing_patch_fails_safe_to_escalation(patch):
    # No content to inspect → keep escalating (never silently under-escalate).
    assert manifest_change_adds_dependency("package.json", patch) is True


def test_unrecognized_manifest_fails_safe():
    # A file classified DEPENDENCY but with no parser we know → escalate.
    assert manifest_change_adds_dependency("weird.lockish", "@@ -1 +1 @@\n+stuff") is True


def test_release_please_version_bump_does_not_set_adds_dependencies():
    """End-to-end: the release-please #320 case no longer force-escalates."""
    diff = Diff(files=[DiffFile(path="package.json", status="modified", patch=NPM_VERSION_BUMP)])
    profile = build_change_profile(
        ["package.json"],
        diff,
        SecuritySurface(),
        BlastRadius(),
        FileRolesConfig(),
    )
    assert profile.adds_dependencies is False


def test_real_dependency_add_sets_adds_dependencies():
    diff = Diff(files=[DiffFile(path="package.json", status="modified", patch=NPM_DEP_ADD)])
    profile = build_change_profile(
        ["package.json"],
        diff,
        SecuritySurface(),
        BlastRadius(),
        FileRolesConfig(),
    )
    assert profile.adds_dependencies is True


def test_manifest_without_patch_still_escalates():
    """If the platform omitted the patch, we must not silently clear the flag."""
    diff = Diff(files=[DiffFile(path="package.json", status="modified", patch="")])
    profile = build_change_profile(
        ["package.json"],
        diff,
        SecuritySurface(),
        BlastRadius(),
        FileRolesConfig(),
    )
    assert profile.adds_dependencies is True
