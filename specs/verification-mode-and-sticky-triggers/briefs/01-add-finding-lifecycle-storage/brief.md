---
title: "Add finding lifecycle storage"
touches:
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/persistence/storage.py
  - alembic/versions/
  - tests/test_finding_lifecycle.py
does_not_touch:
  - src/pr_guardian/decision/
  - src/pr_guardian/dashboard/
  - src/pr_guardian/api/
---

## Task

Extend the existing `finding_dismissals` table to track a finding's full
lifecycle — not just "dismissed", but also `fixed`, `regressed`, and
`verified`. Add a `FindingState` enum and a small set of storage helpers
that later briefs (03 fix-inference, 05 wizard verification) call.

This is pure storage. No engine, no UI.

## Touches

- `src/pr_guardian/persistence/models.py` — extend `FindingDismissalRow`
  with `resolution_kind`, `fixed_by_sha`, `fixed_at`, `verified_by`,
  `verified_at`, `regressed_at`, `regressed_from_sha`. All new columns
  are nullable.
- `src/pr_guardian/persistence/storage.py` — `FindingState` enum + helpers:
  `mark_fixed(pr_id, signature, sha)`, `mark_regressed(pr_id, signature,
  sha, prev_sha)`, `mark_verified(pr_id, signature, user)`,
  `get_finding_states(pr_id) -> dict[str, FindingState]`,
  `infer_fixes(pr_id, prev_sigs, current_sigs, current_sha) ->
  (fixed, regressed)`,
  `verify_sticky_trigger(pr_id, trigger_kind, trigger_source, user)`.
- `alembic/versions/018_add_finding_lifecycle.py` — additive migration.
  New columns are nullable; no backfill required. Down-migration drops
  the columns.
- `tests/test_finding_lifecycle.py` — round-trip tests for each helper.

## Does not touch

- `src/pr_guardian/decision/` — engine bucketing is brief 02.
- `src/pr_guardian/dashboard/` — UI is briefs 04 and 05.
- `src/pr_guardian/api/` — re-run wiring is brief 03.

## Constraints

Follow design.md → Contracts → `FindingState` for the transition rules.
Reuse the existing `finding_signature()` helper in
`persistence/storage.py:623` for finding signatures — do NOT introduce a
new hashing scheme. For sticky-trigger verification storage, follow
ADR-004 → use a synthetic signature
`sha256(pr_id::trigger_kind::trigger_source)[:16]` written into the same
`finding_dismissals` row shape.

The lifecycle is monotonic per `(pr_id, signature)` except for
`regressed` (which follows `fixed`). `verified` is terminal — further
`mark_*` calls on a `verified` row are no-ops (silently). The migration
MUST be additive and reversible — verify with `alembic downgrade -1`
followed by `alembic upgrade head` round-trip.

Use `alembic/versions/017_add_exclusion_rules.py` as the template for
migration `018`.

## Test expectations

`tests/test_finding_lifecycle.py`:
- **Happy fix:** `mark_fixed` then `get_finding_states` returns `fixed`
  with `fixed_by_sha` populated.
- **Regression:** `mark_fixed` at sha_a, then `mark_regressed` at sha_b
  with `prev_sha=sha_a` → state is `regressed`, `regressed_from_sha` is
  `sha_a`.
- **Verification terminal:** `mark_verified` then subsequent
  `mark_fixed` is a no-op; state stays `verified`.
- **`get_finding_states` aggregation:** mix of states across multiple
  signatures returns the right dict.
- **`infer_fixes` set math:** given `prev={a,b,c}`, `current={a}`,
  `previously_fixed={d}` → `fixed={b,c}`, `regressed={}`. If `current`
  contains `d`, then `regressed={d}`.
- **`verify_sticky_trigger`:** writes a synthetic-signature row;
  posting the same trigger twice is a no-op success.
- **No-DB mode:** helpers return safe defaults
  (empty dict / sets, no raise) when DB is unavailable. Follow the
  existing pattern in `storage.py`.

## Wrap-up

Before finishing:
1. Run `alembic upgrade head` against the dev DB; verify the new columns
   exist on `finding_dismissals`. Then run `alembic downgrade -1` and
   `alembic upgrade head` again to confirm reversibility.
2. Run `/simplify` and address its findings.
3. Re-run build and tests; both must still pass.
4. Commit and push.
