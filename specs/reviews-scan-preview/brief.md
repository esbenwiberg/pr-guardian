---
title: "Add repo scan preview to Reviews"
touches:
  - src/pr_guardian/dashboard/reviews_queue.html
  - tests/test_trigger_dispatch.py
  - tests/browser/reviews_scan_preview.spec.mjs
does_not_touch:
  - src/pr_guardian/dashboard/scans.html
  - src/pr_guardian/api/scans.py
  - src/pr_guardian/platform/
  - src/pr_guardian/persistence/
  - alembic/
---

## Task

Add a small repository-scan preview to the current `/reviews` paste bar. When
the user enters repo-scan input, the existing scan options row already appears;
extend that row so it also shows the canonical repo and platform Guardian will
scan.

Examples:

- `https://github.com/octocat/spoon` -> `Will scan GitHub repo: octocat/spoon`
- `https://github.com/octocat/spoon.git` -> `Will scan GitHub repo: octocat/spoon`
- `https://dev.azure.com/myorg/myproj/_git/myrepo` -> `Will scan Azure DevOps repo: myproj/myrepo`
- `myorg/myproj/myrepo` -> `Will scan Azure DevOps repo: myproj/myrepo`
- `owner/repo` -> `Will scan GitHub repo: owner/repo`
- `project/repo` with platform set to Azure DevOps -> `Will scan Azure DevOps repo: project/repo`

The preview is client-side only. Mirror the existing `_resolve_repo_scan_target`
behavior from `src/pr_guardian/api/reviews_queue.py`; do not add a normalization
endpoint.

## Context

`/scans` is legacy and redirects to `/reviews?subject=repo`, so the live surface
is `src/pr_guardian/dashboard/reviews_queue.html`, not `scans.html`.

The current `/reviews` page already has `looksLikeScan()`,
`detectPlatformDefault()`, `#scan-opts`, `#scan-platform`, and the
`POST /api/reviews/trigger` submission path. Keep those flows intact.

## Constraints

- Keep the preview compact and visually subordinate to the existing scan
  options row.
- Update the preview on input changes and when the platform select changes.
- Hide or clear the preview for PR URLs and unrecognized input.
- Do not change scan execution, platform adapters, persistence, auth, or legacy
  scan pages.

## Wrap-up

Run the focused tests in the contract and perform the browser validation on the
running app. The app starts with `bash scripts/agent-serve.sh` and listens on
`$PORT` (default `8000`).
