---
title: "Infer finding fixes in re-run path"
touches:
  - src/pr_guardian/api/dashboard.py
  - src/pr_guardian/api/review.py
  - src/pr_guardian/persistence/storage.py
  - tests/test_fix_inference.py
does_not_touch:
  - src/pr_guardian/decision/
  - src/pr_guardian/dashboard/
  - src/pr_guardian/triage/
---

## Task

After agents complete in any re-run, infer which findings the developer
fixed and which previously-fixed findings regressed. Strict signature
match — no rename detection. Always against the current PR HEAD; never
against the original review.

Single `infer_fixes(prev_sigs, current_sigs, previously_fixed_sigs) ->
(fixed, regressed)` helper in `persistence/storage.py`, called from both
re-run entrypoints (`POST /re-review` in dashboard.py and `POST /api/review`
in review.py).

## Touches

- `src/pr_guardian/api/dashboard.py` — wire `infer_fixes` into the
  `/re-review` handler after agents complete.
- `src/pr_guardian/api/review.py` — same wire in the full-rerun path.
- `src/pr_guardian/persistence/storage.py` — `infer_fixes()` helper +
  call into `mark_fixed` / `mark_regressed` (added in brief 01).
- `tests/test_fix_inference.py` — fix, regress, no-DB graceful path.

## Does not touch

- `src/pr_guardian/decision/` — bucketing is brief 02.
- `src/pr_guardian/dashboard/` — UI is briefs 04 and 05.
- `src/pr_guardian/triage/` — classification untouched.

## Constraints

Follow design.md → Contracts → `infer_fixes()`. Signature equality only —
no fuzzy match, no rename heuristic. The "previously_fixed" set is loaded
via `get_finding_states(pr_id)` and filtering for `FindingState.FIXED`.
No-DB mode: skip the inference call entirely (existing pattern).

## Test expectations

`tests/test_fix_inference.py`:
- 3 findings on run 1, 1 finding on run 2 → 2 in `fixed`.
- Run 1: 3 findings, run 2: 1 finding (2 fixed), run 3: 3 findings → the
  2 reappearing sigs land in `regressed` with `regressed_from_sha` set
  to the run-2 HEAD.
- No findings disappear → `fixed` and `regressed` both empty.
- No-DB mode: helper returns empty sets, does not raise.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
