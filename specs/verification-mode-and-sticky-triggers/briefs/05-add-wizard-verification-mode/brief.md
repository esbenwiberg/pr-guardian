---
title: "Add wizard verification + trigger-focus modes"
touches:
  - src/pr_guardian/dashboard/human_wizard.html
  - src/pr_guardian/dashboard/static/snippet.js
  - src/pr_guardian/api/dashboard.py
  - src/pr_guardian/persistence/storage.py
  - tests/test_verify_endpoint.py
does_not_touch:
  - src/pr_guardian/dashboard/review_detail.html
  - src/pr_guardian/decision/
  - src/pr_guardian/triage/
---

## Task

Extend the wizard's chapter logic with two new modes.

**Verification mode** (auto-entered):
- Condition: `finding_reasons` empty AND `sticky_triggers` non-empty.
- Inserts a Verification chapter (one card per sticky trigger).
- Actions: [Acknowledge & Approve] / [Needs Fix].
- Acknowledging all triggers → wrap-up; each is marked `verified`.

**Trigger-focus mode** (deep-linked):
- URL: `/reviews/{id}?mode=wizard&focus=trigger:{kind}`.
- Wizard skips other chapters, surfaces only the focused trigger.

Reuse `snippet.js` (from Pod 4) when a trigger has a file location.

## Touches

- `src/pr_guardian/dashboard/human_wizard.html` — chapter logic + new
  Verification card markup.
- `src/pr_guardian/dashboard/static/snippet.js` — consumed, not modified.
- `src/pr_guardian/api/dashboard.py` — new endpoint:
  `POST /api/dashboard/reviews/{id}/verify` accepting a trigger
  acknowledgment payload.
- `src/pr_guardian/persistence/storage.py` — `verify_sticky_trigger(pr_id,
  trigger_kind, trigger_source, user)` helper. Stores via synthetic
  signature in `finding_dismissals` (see ADR-004).
- `tests/test_verify_endpoint.py` — verify endpoint contract test.

## Does not touch

- `src/pr_guardian/dashboard/review_detail.html` — Pod 4's surface.
- `src/pr_guardian/decision/` — bucketing is brief 02.
- `src/pr_guardian/triage/` — classification untouched.

## Constraints

Follow design.md → UX flows → Verification chapter wireframe. Verification
chapter only appears when condition is met — do not insert it on a PR
that still has open findings. Snippet reuse must `import` from
`snippet.js`, not duplicate the renderer.

## Test expectations

`tests/test_verify_endpoint.py`:
- `POST /verify` with valid payload → 200, record in storage.
- `POST /verify` with unknown trigger_kind → 400.
- Idempotent: posting the same verify twice → second is no-op (200).

UI behaviour is covered by the two web ACs.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
