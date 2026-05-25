# ADR-004: Fix-by-inference (strict, no rename) + synthetic signature for trigger verification

## Status
Accepted — 2026-05-25. Implemented in `infer_fixes()` and
`verify_sticky_trigger()` using strict signature comparison.

## Context
With the lifecycle state machine in place (ADR-003), we need a rule for *who
decides* when a finding has transitioned to `fixed` or `regressed`. Two axes
to choose:

1. **Signature equality vs. fuzzy match.** Findings carry a signature
   `file::category::agent` (existing `finding_signature()` helper). A literal
   set difference on signatures is trivial to implement and trivial to
   explain. A fuzzy match (rename detection, similarity scoring) is more
   forgiving but introduces ambiguity ("did this fix `auth.py:42` or just
   move the same bug to `auth/jwt.py:38`?") and a tuning surface no one
   asked for.

2. **Diff baseline.** The "previous" set can be the immediately-prior run,
   the original review, or a walk back through history. Walking history makes
   the algorithm sensitive to how many re-runs happened; comparing against
   the original review penalizes legitimate intermediate fixes.

Separately, the wizard's Verification chapter needs to persist
"alice acknowledged the `new_dep:requests==2.32.3` trigger on this PR."
That record needs the same shape as a finding verification (`pr_id`,
something-like-a-signature, `verified_by`, `verified_at`) but a sticky
trigger has no real `file::category::agent` signature.

## Decision

**Fix inference:**
- Strict set difference on `file::category::agent` signatures. No rename
  heuristic, no fuzzy match.
- Diff baseline = immediately-previous review run for the same `pr_id`.
- `fixed = prev_sigs − current_sigs`.
- `regressed = previously_fixed_sigs ∩ current_sigs`, where
  `previously_fixed_sigs` is `get_finding_states(pr_id)` filtered to
  `FindingState.FIXED`.
- `fixed_by_sha` / `regressed_from_sha` always reference the current PR
  HEAD sha at the time of the inference call.

**Sticky-trigger verification storage:**
- Reuse the `finding_dismissals` row shape — same lifecycle columns, same
  storage path.
- Synthesize a signature for the trigger:
  `sha256(pr_id::trigger_kind::trigger_source)[:16]`.
- This keeps verification records in one table, makes
  `get_finding_states(pr_id)` naturally include trigger acknowledgements,
  and avoids a parallel `sticky_trigger_verifications` table that would
  duplicate the same columns.

## Consequences

**Easier:** trivial implementation, no NLP / similarity heuristics to tune
or test. One storage table for both finding and trigger lifecycle. The
verification chapter and the dismiss flow share the same persistence path.

**Harder:** renaming a file (or moving a finding from category `security` to
`security_privacy`) makes the old signature look "fixed" and the new
signature look "new". Acceptable trade-off — the audit log still tells the
true story and a human reviewing the re-run can see both events.

**Committed to:** the signature scheme (`file::category::agent` for
findings, `sha256(pr_id::trigger_kind::trigger_source)[:16]` for triggers)
is the API for fix inference and trigger verification. Changing either
invalidates all historical lifecycle records — treat as a breaking change.
