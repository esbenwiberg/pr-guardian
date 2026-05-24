# Changelog

All notable changes to PR Guardian are tracked here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project is
pre-1.0 — versions move freely until the API stabilizes.

## [Unreleased]

### Added
- CI workflow (`.github/workflows/ci.yml`) — ruff, mypy, pytest, pip-audit,
  and import-linter on push and PR.
- Pre-commit hooks — gitleaks, ruff, large-file and private-key guards.
- `ARCHITECTURE.md` — pipeline shape, layer boundaries, invariants.
- `CONTRIBUTING.md` — branch / commit / PR conventions.
- `.editorconfig` — uniform indentation, EOL, final-newline rules.
- `.gitattributes` — marks lockfiles, prototypes, screenshots, and generated
  CSS so size probes and diff tooling ignore them.
- `uv.lock` is now tracked; `pip-audit` configured for dep CVE checks.
- import-linter contracts enforce three architecture invariants in CI
  (mechanical ⊥ llm, decision IO-free, core ⊥ api/dashboard).
- `.claude/commands/` skills: `/smoke`, `/commit`, `/new-agent`.

### Changed
- `CLAUDE.md` expanded into a real agent guide (command table, layout,
  conventions, invariants). `AGENTS.md` forwards to it so the two stay aligned.
- One-shot ruff format pass across 130 files to align with the declared
  ruff config.
- Moved 34 screenshot PNGs from repo root into `docs/screenshots/`.
- Tightened `selection: str` → `SelectionMode` in `api/review.py`, removing
  the only `# type: ignore` in `src/`.

### Security
- Pin `starlette >= 1.0.1` to clear PYSEC-2026-161.

---

When cutting a release: move the relevant entries from `## [Unreleased]` into
a new `## [x.y.z] - YYYY-MM-DD` section.
