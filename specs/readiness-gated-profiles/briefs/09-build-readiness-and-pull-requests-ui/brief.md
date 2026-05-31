---
title: "Build /reviews readiness panel and /pull-requests browse UI"
depends_on:
  - 05-move-browse-sync-to-connections
  - 06-add-readiness-candidate-engine
  - 07-gate-platform-side-effects
touches:
  - src/pr_guardian/api/reviews_queue.py
  - src/pr_guardian/api/dashboard_page.py
  - src/pr_guardian/dashboard/reviews_queue.html
  - src/pr_guardian/dashboard/pull_requests.html
  - src/pr_guardian/dashboard/static/sidebar.js
  - tests/browser/reviews_readiness_panel.spec.mjs
  - tests/browser/pull_requests_page.spec.mjs
  - tests/browser/repo_review_copy.spec.mjs
does_not_touch:
  - src/pr_guardian/api/profiles.py
  - src/pr_guardian/config/loader.py
---

# Brief 09 - Build /reviews Readiness Panel and /pull-requests Browse UI

## Task

Implement the approved `/reviews` and `/pull-requests` wireframes. `/reviews`
shows actionable review work and opted-in readiness candidates. `/pull-requests`
shows broad synced PRs, including non-opted repos.

Also rename repo-review UI copy away from "scan" so users can distinguish repo
review from recent-changes/maintenance scans.

## Approved `/reviews` Wireframe

```text
/reviews

[ Reviews                                      17 items ] [Identity]

[ Paste PR URL or repo ] [Connection v] [Comment mode v] [Review]

[ All ] [ Waiting ] [ Blocked ] [ Needs review ] [ Mine ] [ Scans ]
                                            [repo v] [author v] [risk v]

queue/list                                     readiness panel
PR  GH  #124  feat/auth                       feat/auth
repo/api - alice - updated 3m ago             GH - repo/api - #124
WAITING CHECKS - 4/7 checks - archmap wait    Readiness
Checks: 4 passed, 2 pending                   Archmap: waiting, 6m left
PR  ADO #88  fix/billing                      Quiet period: satisfied
repo/billing - bob - updated 14m ago          Actions
BLOCKED - checks timeout                      [Start Review Now] [Pause]
PR  GH #118 reviewed row
HUMAN REVIEW - 3 high - 12 files
```

## Approved `/pull-requests` Wireframe

```text
/pull-requests

[ Pull Requests                              143 open PRs ] [Identity] [Sync]

[ Search PRs... ] [platform v] [repo v] [author v] [status v]

[ Mine ] [ Ready-ish ] [ Needs attention ] [ Stale ] [ All open ]

open PR list                                   PR panel
GH #124 feat/auth                              feat/auth
repo/api - alice - updated 3m ago             GH - repo/api - #124
CI pending - Guardian not run                 Status
ADO #88 fix/billing                           CI: pending
repo/billing - bob - updated 14m ago          Guardian: not run
CI passing - unlinked repo                    Linked repo: no
GH #118 chore/docs                            Actions
repo/docs - you - updated 2d ago              [Start Review Now]
stale - reviewed before                       [Open in GitHub/ADO]
                                              [Hide repo from browse]
```

## Requirements

- `/reviews`:
  - merge normal review rows and opted-in readiness candidates in the queue API
  - completed review rows navigate to `/reviews/{review_id}`
  - candidate rows open the readiness panel
  - show waiting and normal user-actionable blocked candidates by default
  - hide draft candidates from `/reviews`
  - hide superseded and technical error candidates by default, with debug/admin
    filters if existing patterns support them
  - expose manual `Start Review Now`
  - expose `Override Readiness & Start Review` only to admins/Profile Managers
  - do not show config/profile errors as review rows; platform readiness status
    carries the failure
- `/pull-requests`:
  - render broad synced PRs with Connection provenance
  - show linked repo status and Guardian status
  - allow manual Start Review Now through the manual resolver
  - allow opening the PR in GitHub/ADO
  - keep browse exclusions available as hide-from-browse behavior
- Routing:
  - `/pr-dashboard` redirects to `/pull-requests`
  - `/browse-pr` redirects or maps to `/pull-requests` according to the route's
    current meaning
  - sidebar includes Reviews, Pull Requests, Profiles when permitted, Insights,
    Settings when admin, and existing Help behavior
- Repo review copy:
  - rename current "scan repo" copy on the review trigger surface to "Review
    repository" or equivalent
  - keep actual scans named scans
  - `/scans` remains the scan history/focus area if still present in the app IA

## Required Facts

- `fact-reviews-shows-opted-readiness`
- `fact-browse-prs-separated`
- `fact-readiness-panel-actions`
- `fact-repo-review-copy-distinct-from-scan`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not show broad non-opted synced PRs in `/reviews`.
- Do not show draft candidates in `/reviews`.
- Do not bury `/pull-requests` as a mode inside `/reviews`.
- Do not present repo review as a scan.
- Keep manual review available for other repos, subject to Connection resolver
  rules.

## Wrap-Up

- Browser tests should capture desktop and narrow viewport screenshots for the
  rearranged `/reviews` and new `/pull-requests` surfaces.
- Keep all text within fixed controls at mobile and desktop sizes.
