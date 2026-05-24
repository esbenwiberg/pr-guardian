---
title: "Render structural trigger details in existing UI"
touches:
  - src/pr_guardian/dashboard/review_detail.html
  - src/pr_guardian/dashboard/human_wizard.html
  - tests/browser/auto_approve_trigger_details.spec.mjs
  - tests/test_dashboard_triage_enrichment.py
does_not_touch:
  - src/pr_guardian/decision/
  - src/pr_guardian/core/
  - src/pr_guardian/platform/
---

## Task

Render `StickyTrigger.details` for hotspot and config-policy triggers in the
existing review detail and wizard surfaces, with no new banner or new screen.

## Touches

- `src/pr_guardian/dashboard/review_detail.html` - enrich the existing
  Structural Triggers section with compact detail rows.
- `src/pr_guardian/dashboard/human_wizard.html` - enrich trigger cards with
  detail rows and add a `config_policy` icon/label treatment.
- `tests/browser/auto_approve_trigger_details.spec.mjs` - durable browser proof
  for review detail and wizard rendering.
- `tests/test_dashboard_triage_enrichment.py` - API/template context proof for
  config-policy detail payloads if needed.

## Does not touch

- `src/pr_guardian/decision/` - Brief 03 owns trigger production.
- `src/pr_guardian/core/` - Brief 03 owns final gate orchestration.
- `src/pr_guardian/platform/` - Brief 02 owns platform history.

## Constraints

Follow `design.md` -> UX flows. Use existing surfaces only:
`review_detail.html` Structural Triggers and `human_wizard.html` trigger cards.
No global warning banner, no migration notice, no landing page, and no separate
hotspot detail route.

Render older triggers without `details` gracefully. Hotspot details should show
file path, 90-day count, fix ratio, thresholds, cache state, computed timestamp,
and reason when present. Config-policy details should show the policy message
and relevant path/ref/explicitness fields.

## Test expectations

The browser test should start or target the existing dashboard test harness in a
deterministic way, seed/mount mocked review payloads with hotspot and
config-policy details, and assert the visible text/action buttons on both
surfaces. It may write screenshots/traces under
`.autopod/evidence/<fact-id>/`.

`tests/test_dashboard_triage_enrichment.py` should cover any Python/API payload
helper introduced for detail shaping so the browser test is not the only guard
against field regressions.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
