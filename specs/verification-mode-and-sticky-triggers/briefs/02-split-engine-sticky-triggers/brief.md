---
title: "Split engine sticky-trigger bucket"
touches:
  - src/pr_guardian/decision/engine.py
  - src/pr_guardian/decision/types.py
  - src/pr_guardian/api/dashboard.py
  - src/pr_guardian/dashboard/review_detail.html
  - tests/test_engine_sticky_split.py
does_not_touch:
  - src/pr_guardian/persistence/
  - src/pr_guardian/dashboard/human_wizard.html
  - src/pr_guardian/triage/
---

## Task

Split `check_overrides()` in `decision/engine.py` so escalation reasons are
tagged by source. Add a `StickyTrigger` dataclass. Bucket reasons into:

- `sticky_triggers: list[StickyTrigger]` — structural (new dep, path_risk,
  hotspot, trust_tier, repo_risk, high_diff)
- `finding_reasons: list[str]` — finding-derived (N high-sev, N suspected,
  FLAG_HUMAN)

**Break-cleanly migration (per ADR-002):** drop `override_reasons` and
`trust_tier_reasons` from `DecisionResult` and the `/api/dashboard/reviews/{id}`
payload entirely. No flat union, no back-compat shim. Migrate
`review_detail.html` (line ~265) to read `sticky_triggers` and `finding_reasons`
directly. Pod 5 (wizard) reads the same new fields.

## Touches

- `src/pr_guardian/decision/engine.py` — rewrite `check_overrides()` return shape;
  drop `override_reasons` / `trust_tier_reasons` fields from `DecisionResult`.
- `src/pr_guardian/decision/types.py` — `StickyTrigger` dataclass.
- `src/pr_guardian/api/dashboard.py` — replace old field names with new ones in
  the review GET payload.
- `src/pr_guardian/dashboard/review_detail.html` — rewrite the "override reasons"
  panel to render two sections (sticky_triggers + finding_reasons) instead of one
  flat list. Existing `#override-reasons-section` becomes two sections.
- `tests/test_engine_sticky_split.py` — bucketing unit tests.

## Does not touch

- `src/pr_guardian/persistence/` — storage is brief 01.
- `src/pr_guardian/dashboard/human_wizard.html` — Pod 5's surface.
- `src/pr_guardian/triage/` — classification is upstream and unchanged.

## Constraints

Follow design.md → Contracts → `StickyTrigger`. Kind values are the closed
set: `new_dep | path_risk | hotspot | trust_tier | repo_risk | high_diff`.
Adding a new kind requires a contract update in design.md, not a silent
addition.

## Test expectations

`tests/test_engine_sticky_split.py`:
- A PR with only a new dep → `sticky_triggers` has one entry with kind=`new_dep`,
  `finding_reasons` is empty.
- A PR with only a high-sev finding → `finding_reasons` non-empty,
  `sticky_triggers` is empty.
- A PR with both → both non-empty, no overlap between them.
- A clean PR → both empty.
- `DecisionResult` does NOT expose `override_reasons` or `trust_tier_reasons`
  (the break-cleanly invariant from ADR-002).

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
