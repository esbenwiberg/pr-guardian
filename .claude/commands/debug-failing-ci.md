---
description: Diagnose a red CI run on the current branch and propose a minimal fix.
---

CI lives in `.github/workflows/ci.yml`. The job is a single sequence: install
→ ruff check → ruff format check → mypy → vulture → pytest → import-linter
→ build → pip-audit. The first failing step short-circuits the rest.

Steps:

1. Find the failing run:
   - `gh run list --branch $(git rev-parse --abbrev-ref HEAD) --limit 5`.
   - Pick the most recent non-success.
   - `gh run view <id> --log-failed` → the failing step's log.
2. Identify the step from the log header (`Run mypy src`, `Run python -m
   pytest -q`, etc.). Don't guess from output shape — the log header is
   authoritative.
3. Map the failure to a local command and reproduce:
   - `ruff check .` / `ruff format --check .`
   - `mypy src`
   - `vulture`
   - `python -m pytest -q -x` (`-x` stops at first failure for faster iter)
   - `lint-imports`
   - `python -m build --sdist --wheel`
   - `pip-audit --strict`
   If the local repro doesn't match CI, the divergence is the bug — check
   environment (`uv.lock` drift, missing dev dep, secrets in env, OS-specific
   path).
4. Diagnose. Be specific:
   - `mypy` failure → name the error code (`[arg-type]`, `[union-attr]`, …)
     and explain which constraint is being violated.
   - `pytest` failure → quote the assertion and the actual value.
   - `pip-audit` → which CVE, which package, which pinned version would
     clear it.
5. Propose the minimal fix. Do not refactor surrounding code. Do not silence
   with `# type: ignore`, `# noqa`, or `--no-verify` unless the user
   explicitly accepts the suppression and a justification comment is added.
6. Apply the fix, re-run the local equivalent of the failing step, then run
   `/smoke` to confirm nothing else regressed.

Never re-run CI to "see if it goes green this time" without a code change —
the run is deterministic; flaky tests are bugs and `tests.deterministic`
guards against them.
