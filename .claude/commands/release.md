---
description: Cut a release — bump version, finalize changelog, tag, build artifacts.
---

PR Guardian is pre-1.0 (`pyproject.toml` declares `version = "0.1.0"`) and uses
`Keep a Changelog`. Releases are mechanical — no marketing copy, no narrative.

Steps:

1. Verify the tree is clean and on `main`:
   - `git status` must show no modifications.
   - `git rev-parse --abbrev-ref HEAD` must print `main`.
   - If either fails, stop and tell the user.

2. Decide the next version. The user picks; suggest one based on the
   `## [Unreleased]` entries in `CHANGELOG.md`:
   - Any `### Removed` / `BREAKING CHANGE:` footer → bump **minor** (pre-1.0)
     or **major** (post-1.0).
   - `### Added` only → bump **minor**.
   - `### Fixed` / `### Security` only → bump **patch**.

3. Update `CHANGELOG.md`:
   - Rename `## [Unreleased]` → `## [x.y.z] - YYYY-MM-DD` (today's date).
   - Insert a fresh empty `## [Unreleased]` block above it with the four
     standard subsections (`### Added`, `### Changed`, `### Fixed`,
     `### Security`).

4. Update `pyproject.toml`: bump `version = "x.y.z"`.

5. Run the smoke gate (see `/smoke`). Do not proceed if anything fails.

6. Commit with a `chore(release): x.y.z` subject. Tag the commit:
   `git tag -a vx.y.z -m "Release x.y.z"`.

7. Build artifacts: `python -m build --sdist --wheel`. Verify `dist/` contains
   `pr_guardian-x.y.z-py3-none-any.whl` and `pr_guardian-x.y.z.tar.gz`.

8. Stop. Do not push tags or upload artifacts without explicit user
   confirmation — both are destructive in different ways (tags rewrite remote
   history; uploads are irreversible).

Never bump major to 1.x without an explicit ask — the project's surface is
still in flux and a major signals API stability the codebase doesn't claim.
