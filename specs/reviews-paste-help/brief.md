---
title: "Add paste-bar help to Reviews"
touches:
  - src/pr_guardian/dashboard/reviews_queue.html
  - tests/browser/reviews_paste_help.spec.mjs
does_not_touch:
  - src/pr_guardian/api/reviews_queue.py
  - src/pr_guardian/api/dashboard_page.py
  - src/pr_guardian/dashboard/static/sidebar.js
  - src/pr_guardian/dashboard/scans.html
  - tests/browser/reviews_scan_preview.spec.mjs
  - src/pr_guardian/platform/
  - src/pr_guardian/persistence/
  - alembic/
---

## Task

Add a compact paste-bar help popover to the existing `/reviews` queue page. The
popover opens when the user clicks a help control beside the PR/repo input and
shows accepted input examples for a GitHub PR URL, a GitHub repo scan, and an
Azure DevOps repo scan.

The change must preserve the current trigger form and scan-preview behavior. Add
a durable browser smoke script that clicks the help control and captures at least
two screenshots for the harness evidence path.

## Why

The `/reviews` paste bar accepts several input shapes, but that knowledge is
buried in placeholder text and client-side parsing. A small in-place help
control gives users a real affordance without adding another page or changing
review dispatch.

This brief is also intentionally shaped to exercise the harness path for
`scenarios`, executable `required_facts`, and non-empty `human_review` entries.
The UI change is small, real, and browser-visible.

## Touches

- `src/pr_guardian/dashboard/reviews_queue.html` - add the help control,
  popover markup, and small client-side toggle behavior near the existing
  paste-bar form.
- `tests/browser/reviews_paste_help.spec.mjs` - add the browser proof that
  clicks the control, validates the popover, checks the existing scan preview,
  and writes screenshots under `.autopod/evidence/fact-reviews-paste-help/`.

## Does not touch

- `src/pr_guardian/api/reviews_queue.py`
- `src/pr_guardian/api/dashboard_page.py`
- `src/pr_guardian/dashboard/static/sidebar.js`
- `src/pr_guardian/dashboard/scans.html`
- `tests/browser/reviews_scan_preview.spec.mjs`
- `src/pr_guardian/platform/`
- `src/pr_guardian/persistence/`
- `alembic/`

## Constraints

- `/reviews` is the live queue surface. `src/pr_guardian/api/dashboard_page.py`
  documents `/reviews` as the queue root and redirects legacy `/scans` to
  `/reviews?subject=repo`; do not add this help to `scans.html`.
- Keep the existing `looksLikeScan()`, `detectPlatformDefault()`,
  `resolveRepoScanPreview()`, and `POST /api/reviews/trigger` paths intact.
  This is a help affordance, not a normalization or dispatch change.
- The help control must be `type="button"` and should expose basic accessible
  state such as `aria-expanded` / `aria-controls` so clicking it cannot submit
  the trigger form.
- The browser fact command stays plain:
  `node tests/browser/reviews_paste_help.spec.mjs`. The harness starts the app
  via the normal `scripts/agent-serve.sh` runtime; do not make the contract
  depend on an extra URL argument.
- The browser proof must create at least:
  `.autopod/evidence/fact-reviews-paste-help/closed.png` and
  `.autopod/evidence/fact-reviews-paste-help/open.png`.
- Do not refactor the sidebar Help popover in
  `src/pr_guardian/dashboard/static/sidebar.js`; that is a separate navigation
  feature.

## Skills to reference

None. The harness consumes `contract.yaml` directly, and the user explicitly
declined adding a Browser/browser-test skill reference.

## Test expectations

Create `tests/browser/reviews_paste_help.spec.mjs`.

The browser test should:

1. Open the running `/reviews` page from the standard harness runtime.
2. Save a closed-state screenshot to
   `.autopod/evidence/fact-reviews-paste-help/closed.png`.
3. Click the paste-bar help control.
4. Assert the popover is visible and includes examples for GitHub PR, GitHub
   repo scan, and Azure DevOps repo scan inputs.
5. Save an open-state screenshot to
   `.autopod/evidence/fact-reviews-paste-help/open.png`.
6. Fill `https://github.com/octocat/spoon` into `#trigger-url` and assert the
   existing scan preview still says `Will scan GitHub repo: octocat/spoon`.

## Risks / pitfalls

- Do not collapse `human_review` back to `[]`; this spec deliberately includes a
  judgement-only validation item to exercise the harness.
- Avoid a file-URL-only proof for the final screenshots. The harness-served
  `/reviews` page is the surface that loads `/static/styles.css` and matches the
  UI a human reviewer sees.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
