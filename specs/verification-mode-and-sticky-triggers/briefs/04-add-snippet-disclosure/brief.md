---
title: "Add snippet disclosure to finding cards"
touches:
  - src/pr_guardian/dashboard/review_detail.html
  - src/pr_guardian/dashboard/static/snippet.js
  - src/pr_guardian/api/dashboard.py
  - tests/test_snippet_endpoint.py
does_not_touch:
  - src/pr_guardian/dashboard/human_wizard.html
  - src/pr_guardian/persistence/
  - src/pr_guardian/decision/
---

## Task

Add a "Show code" disclosure to each finding card on `/reviews/{id}`.
Clicking it fetches the relevant diff hunk and renders it inline using
the existing `.hunk` CSS primitive from `human_wizard.html` (lines 122-132).
Extract the hunk renderer into `static/snippet.js` so Pod 5 can reuse it
inside the wizard without duplication.

## Touches

- `src/pr_guardian/dashboard/review_detail.html` — add disclosure markup +
  load `snippet.js`.
- `src/pr_guardian/dashboard/static/snippet.js` — exported renderer:
  `renderSnippet(container, hunkData)` and `fetchSnippet(reviewId, path,
  line, context=3)`.
- `src/pr_guardian/api/dashboard.py` — confirm the existing
  `/api/dashboard/reviews/{id}/diff` endpoint accepts `path`, `line`,
  `context` query params; extend if missing.
- `tests/test_snippet_endpoint.py` — endpoint param test.

## Does not touch

- `src/pr_guardian/dashboard/human_wizard.html` — Pod 5 wires snippet
  reuse there.
- `src/pr_guardian/persistence/` — out of scope.
- `src/pr_guardian/decision/` — out of scope.

## Constraints

Reuse `.hunk` styling — do not redefine in `snippet.js` or `review_detail.html`.
The renderer must be DOM-only: no framework, plain `document.createElement`.
Failure modes (404 file, line out of diff): renderer shows a muted
"snippet unavailable" line rather than throwing.

## Test expectations

`tests/test_snippet_endpoint.py`:
- `/api/dashboard/reviews/{id}/diff?path=X&line=10&context=3` returns
  expected lines (or 404 cleanly).
- `context=0` returns only the target line.

UI behavior is covered by the `web` AC, not unit tests.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
