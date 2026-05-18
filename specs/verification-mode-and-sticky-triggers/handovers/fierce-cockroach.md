# Handover: fierce-cockroach (Brief 03 — Fix Inference Wire-up)

## What was built

Wired `infer_fixes()` (already implemented by zany-octopus in `storage.py`) into
both re-run entrypoints so fix and regression state is tracked automatically after
every agent run.

### Changes

**`src/pr_guardian/api/dashboard.py`** — `_run_bg()` inside `re_review` handler:
- Captures return value of `run_re_review`
- Computes `prev_sigs` from `review["agent_results"]` (original review findings, raw dicts)
- Computes `current_sigs` from `result.agent_results` (typed `Finding` objects)
- Calls `await storage.infer_fixes(pr.pr_id, prev_sigs, current_sigs, pr.head_commit_sha)`

**`src/pr_guardian/api/review.py`** — `_run_review_background()`:
- Captures return value of `run_review`
- Guards on `if dismissals is not None:` (proxy for DB availability — `None` means
  import/query failed, empty list means DB was reachable with no prior dismissals)
- Uses existing `dismissals` list sigs as `prev_sigs`
- Computes `current_sigs` from `result.agent_results`
- Calls `await infer_fixes(pr.pr_id, prev_sigs, current_sigs, pr.head_commit_sha)`

**`tests/test_fix_inference.py`** (NEW) — 4 tests:
- `test_run1_to_run2_detects_fixed` — 3→1 findings, 2 land in fixed
- `test_run3_detects_regression_with_regressed_from_sha` — previously-fixed sigs
  reappear, regressed with regressed_from_sha set to fixed sha (sha-run2)
- `test_no_findings_disappear_returns_empty` — stable sig set → both empty
- `test_no_db_returns_empty_sets_and_does_not_raise` — graceful no-DB path

## Interfaces / contracts downstream pods must know

### `infer_fixes` signature (unchanged from zany-octopus)
```python
async def infer_fixes(
    pr_id: str,
    prev_sigs: set[str],
    current_sigs: set[str],
    current_sha: str,
) -> tuple[set[str], set[str]]:  # (fixed, regressed)
```
`previously_fixed` sigs are loaded from DB internally; callers pass only prev+current.

### No deviations from the design contract

The design spec says `infer_fixes(pr_id, prev_sigs, current_sigs, current_sha)` — 
that is exactly what was implemented by zany-octopus and called here.

## Files this pod owns — do not modify without good reason
- `tests/test_fix_inference.py`

## Files modified that downstream pods should be aware of
- `src/pr_guardian/api/dashboard.py` — `_run_bg()` now returns result and calls
  `infer_fixes`. The exception handler is unchanged; `infer_fixes` errors are
  silently swallowed inside the helper (existing no-DB pattern).
- `src/pr_guardian/api/review.py` — `_run_review_background()` now captures
  `run_review` return value. Same swallowing pattern.

## Discovered constraints / landmines

- **`prev_sigs` source differs between entrypoints**: In the re-review path,
  `prev_sigs` comes from the original review's `agent_results` (exact run-N sigs).
  In the full-review path, `prev_sigs` comes from `dismissals` (all historically
  known sigs for the PR). The full-review path is slightly broader — a sig that
  appeared in any previous run and was never dismissed will be in `prev_sigs` even
  if it wasn't in the immediately preceding run. This is acceptable for the brief's
  use case (fix/regression detection) and matches the existing dismissal-tracking
  semantics.

- **`infer_fixes` already exists** — zany-octopus (Brief 01) fully implemented the
  helper, including the state machine transitions (mark_fixed, mark_regressed) and
  no-DB graceful degradation. Brief 03's work was purely the wire-up.

- **`infer_fixes` result is not used** — both callers discard the returned
  `(fixed, regressed)` sets. The side-effects (DB rows updated) are what matters.
  Pod 04/05 reads state via `get_finding_states()` when needed.
