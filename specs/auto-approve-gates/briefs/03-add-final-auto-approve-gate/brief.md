---
title: "Add shared final auto-approval gate"
touches:
  - src/pr_guardian/decision/types.py
  - src/pr_guardian/decision/engine.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/api/dashboard.py
  - tests/test_auto_approve_final_gate.py
  - tests/test_engine_sticky_split.py
  - tests/test_verify_endpoint.py
does_not_touch:
  - src/pr_guardian/dashboard/
  - src/pr_guardian/platform/
  - src/pr_guardian/persistence/models.py
---

## Task

Add `config_policy`, add structured `StickyTrigger.details`, and enforce a
single final auto-approval gate before any automated path can call platform
approval side effects.

## Touches

- `src/pr_guardian/decision/types.py` - extend the closed kind set and
  `StickyTrigger` dataclass.
- `src/pr_guardian/decision/engine.py` - add/apply the final gate for normal
  review decisions and convert missing consent/root config edits/hotspot
  failures into sticky triggers.
- `src/pr_guardian/core/orchestrator.py` - turn re-review direct auto-approvals
  into candidate auto-approvals that run the shared final gate before posting or
  saving.
- `src/pr_guardian/api/dashboard.py` - allow `config_policy` through sticky
  trigger verification validation.
- `tests/test_auto_approve_final_gate.py` - final gate behavior and no-bypass
  tests.
- `tests/test_engine_sticky_split.py` - trigger details and kind contract tests.
- `tests/test_verify_endpoint.py` - `config_policy` verification validation.

## Does not touch

- `src/pr_guardian/dashboard/` - rendering is Brief 04.
- `src/pr_guardian/platform/` - platform adapter behavior is Brief 02.
- `src/pr_guardian/persistence/models.py` - no DB schema change is needed for
  JSON `override_reasons` storage.

## Constraints

Follow `design.md` -> Contracts -> StickyTrigger contract and Final gate
invariant. Any path that would call `approve_pr()` for automated approval must
pass through the final gate first. Existing re-review branches at
`core/orchestrator.py:736` and `core/orchestrator.py:892` become candidate
auto-approval paths, not direct approval paths.

Root config edit blocking is deliberately v1-narrow: check only current diff
path exactly `review.yml`. Do not inspect `old_path`.

Manual `submit_verdict` approval remains outside this gate because it is a human
approval action.

## Test expectations

`tests/test_auto_approve_final_gate.py` should simulate both re-review shortcut
paths with a blocker present and assert `approve_pr()` is not called while
`config_policy` or hotspot triggers are persisted on the result.

`tests/test_engine_sticky_split.py` should prove `details` round-trips in
stored/API-shaped trigger dictionaries and does not break existing trigger
bucket tests.

`tests/test_verify_endpoint.py` should prove `config_policy` is accepted by the
verify endpoint's allowed kind set and unknown kinds still fail.

## Risks / pitfalls

The existing decision engine currently appends "Auto-approve is disabled" to
finding reasons. This feature needs config-policy structural triggers only when
the result is otherwise an auto-approve candidate; do not add a global banner or
finding reason for every human-review result.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
