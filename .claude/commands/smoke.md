---
description: Run the local checks any change must pass before pushing.
---

Run the full local gate, in order, stopping at the first failure. Report the
failure verbatim — do not summarize:

1. `ruff check .`
2. `ruff format --check .`
3. `mypy src` — note: currently soft-failing, treat new errors as regressions
4. `python -m pytest -q`
5. `pip-audit --strict`

If every step passes, summarize: "smoke clean — N tests, M packages audited."

If any step fails:
- Show the failing tool's output (last 30 lines).
- Identify the offending file(s) by path.
- Propose a fix, do not apply it without confirming.

Do not bypass with `--no-verify`, `# noqa`, or `# type: ignore` unless the
user explicitly asks. Suppressions in this repo require a justification
comment.
