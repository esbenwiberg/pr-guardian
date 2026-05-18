# Handover: outrageous-lizard (Brief 02 — Engine Sticky-Trigger Split)

## What was built

Rewrote `check_overrides()` in `decision/engine.py` to return a split
`tuple[list[StickyTrigger], list[str]]` instead of a flat `list[str]`. Added the
`StickyTrigger` dataclass in `decision/types.py`. Migrated `ReviewResult` in
`models/output.py` to expose `sticky_triggers` and `finding_reasons` in place of
`override_reasons` and `trust_tier_reasons` (break-cleanly per ADR-002).

### New/changed symbols

**`src/pr_guardian/decision/types.py`** (NEW)
- `StickyTriggerKind` — `Literal["new_dep","path_risk","hotspot","trust_tier","repo_risk","high_diff"]`
- `StickyTrigger` — frozen dataclass: `kind, label, source, reason`

**`src/pr_guardian/decision/engine.py`**
- `check_overrides()` now returns `tuple[list[StickyTrigger], list[str]]`
  — sticky bucket: `new_dep` (adds_dependencies), `repo_risk` (elevated/critical),
    `hotspot` (check_hotspot_hits), `path_risk` (security_surface.has_hits),
    `trust_tier` (MANDATORY_HUMAN / HUMAN_PRIMARY — added in `decide()`)
  — finding bucket: FLAG_HUMAN, detected-medium+, suspected≥3, branch-blocked,
    auto-approve-disabled, reject reasons
- `decide()` — calls new return shape; `trust_tier` trigger appended inline when
  tier escalates; `ReviewResult` built with new fields

**`src/pr_guardian/models/output.py`**
- `ReviewResult.override_reasons` REMOVED
- `ReviewResult.trust_tier_reasons` REMOVED
- `ReviewResult.sticky_triggers: list[StickyTrigger]` ADDED
- `ReviewResult.finding_reasons: list[str]` ADDED

**`src/pr_guardian/persistence/storage.py`** (minimal)
- `save_review_result()` now stores `{sticky_triggers:[...], finding_reasons:[...]}`
  dict in the existing `override_reasons` JSONB column (no schema change)
- `_unpack_override_reasons()` helper added — handles both new dict format and
  legacy list format for backward compat on old rows
- `_review_to_dict()` spreads `sticky_triggers` + `finding_reasons` into the
  response instead of the old flat `override_reasons` key
- `trust_tier_details` no longer includes `"reasons"` key (removed `trust_tier_reasons`)

**`src/pr_guardian/dashboard/review_detail.html`**
- Old `#override-reasons-section` replaced by two panels:
  `#sticky-triggers-section` (orange, structural) and
  `#finding-reasons-section` (amber, finding-derived)

**`tests/test_engine_sticky_split.py`** (NEW) — 14 tests

## Contracts downstream pods must honour

### Seam 3 — `DecisionResult` (`ReviewResult`) shape
`ReviewResult` no longer has `override_reasons` or `trust_tier_reasons`.
It has `sticky_triggers: list[StickyTrigger]` and `finding_reasons: list[str]`.
Pod 03 (re-run / infer_fixes) and Pod 05 (wizard) MUST use these fields.

### Seam 3 — API payload shape
`GET /api/dashboard/reviews/{id}` now returns `sticky_triggers` and
`finding_reasons` at the top level. It does NOT return `override_reasons`.
Pod 05 (wizard) reads these fields from the payload — confirmed in the spec.

### Seam 3 — `StickyTrigger` serialization
In the API payload, each sticky trigger is a plain dict:
`{"kind": str, "label": str, "source": str, "reason": str}`.
The `kind` values are the closed set from `StickyTriggerKind`.

### Seam 3 — Storage backward compat
Old rows in the DB may have a `list` in the `override_reasons` JSONB column.
The `_unpack_override_reasons()` helper converts those to `{sticky_triggers:[],
finding_reasons: <old list>}`. Pod 03 should not break on these rows.

### Seam 3 — trust_tier_details no longer has "reasons" key
The `trust_tier_details` JSONB column on `ReviewRow` now stores only
`{files, reviewer_group_override, escalated_from}` — the `reasons` field was
removed. Code reading `trust_tier_details.get("reasons")` will get `None`.

## Known deviation: `high_diff` not implemented

The `high_diff` sticky trigger kind is defined in `StickyTriggerKind` but is not
emitted by the engine. No threshold for diff line count is defined in the config
or the existing codebase, and adding one would be scope creep. Pod 03 or a later
pass should implement it if needed; the kind is reserved in the closed set.

## Files this pod owns — do not modify without good reason
- `src/pr_guardian/decision/types.py`
- `src/pr_guardian/decision/engine.py` (check_overrides, decide)
- `src/pr_guardian/models/output.py` (ReviewResult field set)
- `tests/test_engine_sticky_split.py`

## Discovered constraints / landmines
- `trust_tier` sticky trigger is appended INSIDE `decide()` not `check_overrides()`,
  because `check_overrides()` doesn't receive the trust tier result. Pod 05 must
  read all triggers from `sticky_triggers` regardless of kind.
- `check_hotspot_hits()` from `pr_guardian.triage.hotspots` is now imported in
  `engine.py`. No circular import issues (triage does not import from decision).
- `dataclasses.asdict()` is used for StickyTrigger serialisation in storage.py;
  it produces a plain dict with the four string fields.
