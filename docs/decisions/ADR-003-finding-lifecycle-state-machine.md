# ADR-003: Finding lifecycle state machine

## Status
Proposed

## Context
The `finding_dismissals` table currently tracks a single bit of state per
`(pr_id, signature)` — whether a human explicitly dismissed the finding. The
verification-mode feature needs more:

- The wizard must distinguish "developer fixed this between runs" (`fixed`)
  from "human said it's fine" (`dismissed`) — the audit story for the two is
  different.
- A finding that was previously fixed but reappears on a later run is a
  regression and should be surfaced differently from a never-seen finding.
- A human's explicit acknowledgement of a sticky trigger (or a residual
  finding) needs a terminal state that subsequent automated inference cannot
  silently overwrite.

A boolean `dismissed` column can't carry these semantics, and bolting on a
second boolean (`fixed_by_inference`) creates undefined combined states.

## Decision
Add a `FindingState` enum and treat `(pr_id, signature)` as having a lifecycle:

```
open  → dismissed | fixed | verified
fixed → regressed | verified
dismissed → verified
regressed → fixed | verified
verified → (terminal — no further transitions)
```

- `fixed` and `regressed` are inferred automatically by signature diffing on
  re-run (see ADR-004). Never user-set.
- `dismissed` is set by the wizard's existing dismiss action.
- `verified` is set by the wizard's new Acknowledge & Approve action and is
  terminal: subsequent `mark_*` calls on a `verified` row are silent no-ops.

The state machine is hard-coded in the storage helpers
(`mark_fixed` / `mark_regressed` / `mark_verified`) — no generic
state-machine framework, no per-PR configuration.

## Consequences

**Easier:** re-run can correctly distinguish "dev fixed all of these" from
"human dismissed all of these" without losing audit history. Verification mode
has a terminal state to write into.

**Harder:** the `finding_dismissals` table grows wider — `resolution_kind`,
`fixed_by_sha`, `fixed_at`, `verified_by`, `verified_at`, `regressed_at`,
`regressed_from_sha`. The table name is now a slight misnomer (it stores more
than dismissals). Rename deferred to a later cleanup pass.

**Committed to:** `verified` is terminal — no transitions out. If a verified
finding's underlying issue reappears later, it surfaces as a *new* signature
or via `regressed` on the same signature, but the verified row stays
verified. This matches the audit story: "alice acknowledged this on
2026-05-18" is a permanent record, not a revocable one.
