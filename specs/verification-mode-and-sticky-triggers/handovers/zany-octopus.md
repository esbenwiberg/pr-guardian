# Handover: zany-octopus (Brief 01 — Finding Lifecycle Storage)

## What was built

Extended `finding_dismissals` with 7 nullable lifecycle columns and added a complete set of storage helpers for tracking the finding lifecycle through `open → dismissed → fixed → regressed → verified`.

### New columns on `finding_dismissals`
- `resolution_kind VARCHAR(24)` — lifecycle state driver (`fixed | regressed | verified`)
- `fixed_by_sha VARCHAR(64)` — SHA when finding was inferred as fixed
- `fixed_at TIMESTAMPTZ` — when it was marked fixed
- `verified_by VARCHAR(256)` — user who acknowledged/verified
- `verified_at TIMESTAMPTZ` — when verified
- `regressed_at TIMESTAMPTZ` — when regression was detected
- `regressed_from_sha VARCHAR(64)` — SHA at which it was previously fixed (before regression)

### New symbols in `src/pr_guardian/persistence/storage.py`
- `FindingState(StrEnum)` — `OPEN | DISMISSED | FIXED | REGRESSED | VERIFIED`
- `mark_fixed(pr_id, signature, fixed_by_sha)` — async, no-op if VERIFIED
- `mark_regressed(pr_id, signature, sha, prev_sha)` — async, no-op if VERIFIED; `sha` param is in the contract but not persisted (no column for it yet)
- `mark_verified(pr_id, signature, user)` — async, terminal; idempotent
- `get_finding_states(pr_id) -> dict[str, FindingState]` — returns only sigs with existing rows; absent = OPEN
- `infer_fixes(pr_id, prev_sigs, current_sigs, current_sha) -> (fixed, regressed)` — computes set diff and writes marks
- `verify_sticky_trigger(pr_id, trigger_kind, trigger_source, user)` — synthetic sig via `_hash16(pr_id::kind::source)`, delegates to `mark_verified`
- `_hash16(raw)` — private; extracted from the `finding_signature` formula to avoid duplication

### Migration
`alembic/versions/018_add_finding_lifecycle.py` — additive, idempotent (column-exists guard), reversible. `down_revision = "017"`.

## Contracts downstream pods must honour

### Seam 1 — `FindingState` enum shape
The exact string values (`"open"`, `"dismissed"`, `"fixed"`, `"regressed"`, `"verified"`) are written into `finding_dismissals.resolution_kind`. Brief 02 (engine) reads `FindingState` by name; briefs 03 (re-run) and 05 (wizard) call the helpers directly. Do not rename enum members.

### Seam 2 — Row-state derivation rule
`_row_to_finding_state` returns the state by reading `resolution_kind`. A row with `resolution_kind=None` is treated as `DISMISSED` (legacy path). A missing row means `OPEN`. Brief 03 and 05 must not assume any other mapping.

### Seam 3 — `mark_regressed` signature
`mark_regressed(pr_id, signature, sha, prev_sha)` — the `sha` parameter (current SHA at regression time) is accepted but not persisted, because no column was specified in brief 01. Brief 03 may add a `regressed_to_sha` column and start storing it — that would be additive and safe.

### Seam 4 — Synthetic signature formula (ADR-004)
`verify_sticky_trigger` uses `_hash16(f"{pr_id}::{trigger_kind}::{trigger_source}")`. Brief 05 must use the same formula when calling `POST /verify` → `verify_sticky_trigger`. The formula is implemented once in `_hash16` — do not duplicate.

### Seam 5 — `get_finding_states` returns only known sigs
The returned dict only contains signatures that have a row in `finding_dismissals` for that `pr_id`. A signature absent from the dict is `OPEN`. Brief 02 and 03 must handle the absent-key case.

## Files this pod owns — do not modify without good reason
- `src/pr_guardian/persistence/models.py` (FindingDismissalRow columns)
- `src/pr_guardian/persistence/storage.py` (FindingState, mark_*, get_finding_states, infer_fixes, verify_sticky_trigger, _hash16)
- `alembic/versions/018_add_finding_lifecycle.py`
- `tests/test_finding_lifecycle.py`

## Discovered constraints / landmines
- **`alembic upgrade head` from scratch fails at migration 003** — pre-existing bug (003 adds columns already created by 001/002 without an existence guard). The validator environment starts from a seeded DB (already at 017), so this doesn't affect the `fact-migration-applies` check. Do not attempt to fix migrations outside your brief scope.
- **`sha` parameter on `mark_regressed` is unused** — intentionally part of the contract per brief spec. If brief 03 needs to persist it, add a `regressed_to_sha` column in a new migration.
- **All helpers swallow DB errors** — they follow the existing no-DB graceful-degradation pattern in storage.py (`except Exception: log.warning(...)`). Callers must not assume writes succeeded; brief 03 should treat `infer_fixes` returning empty sets as a no-op, not an error.
