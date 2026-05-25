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

## Design

Data shape:

```python
ResolvedScanTarget = {
    "platform": "github" | "ado",
    "repo": "owner/repo" | "project/repo",
}
```

Client flow:

1. `looksLikeScan(value)` decides whether the paste-bar value is repo-scan
   input rather than a PR URL.
2. `detectPlatformDefault(value)` selects the platform when the input itself is
   unambiguous.
3. The preview renderer mirrors `_resolve_repo_scan_target()` for supported
   formats and writes one compact line into the existing scan options row.
4. The submit path remains unchanged: `/api/reviews/trigger` is still the only
   server call.

Ownership:

- `src/pr_guardian/dashboard/reviews_queue.html` owns the client preview.
- `src/pr_guardian/api/reviews_queue.py` remains the source of truth for server
  normalization.
- Platform adapters, persistence, and scan execution are intentionally outside
  this feature.

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

## Acceptance Criteria

- Given `https://github.com/octocat/spoon`, when the value is typed in the
  paste bar, then the scan options row says
  `Will scan GitHub repo: octocat/spoon`.
- Given `https://github.com/octocat/spoon.git`, when the value is typed in the
  paste bar, then the preview strips `.git`.
- Given `https://dev.azure.com/myorg/myproj/_git/myrepo`, when the value is
  typed in the paste bar, then the preview says
  `Will scan Azure DevOps repo: myproj/myrepo`.
- Given `owner/repo`, when platform is GitHub, then the preview treats the
  value as `owner/repo`.
- Given `project/repo`, when platform is Azure DevOps, then the preview treats
  the value as `project/repo`.
- Given a PR URL or unsupported input, when the value changes, then the preview
  is hidden and no normalization network call is made.

## Test Traceability

- `repo-target-resolver-contract`:
  `python -m pytest tests/test_trigger_dispatch.py::TestResolveRepoScanTarget -q`
- `trigger-route-uses-canonical-scan-target`:
  `python -m pytest tests/test_trigger_dispatch.py::TestTriggerRouteScanDispatch -q`
- `reviews-scan-preview-renders`:
  `node tests/browser/reviews_scan_preview.spec.mjs`

## Wrap-up

Run the focused tests in the contract and perform the browser validation on the
running app. The app starts with `bash scripts/agent-serve.sh` and listens on
`$PORT` (default `8000`).
