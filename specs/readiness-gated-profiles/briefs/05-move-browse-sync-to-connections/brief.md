---
title: "Move broad PR browse sync to Connections and /pull-requests"
depends_on:
  - 02-add-profile-management-api
touches:
  - src/pr_guardian/core/pr_sync.py
  - src/pr_guardian/api/pr_dashboard_api.py
  - src/pr_guardian/api/dashboard_page.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/dashboard/pull_requests.html
  - src/pr_guardian/dashboard/static/sidebar.js
  - tests/test_connection_sync.py
  - tests/browser/pull_requests_page.spec.mjs
does_not_touch:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/dashboard/reviews_queue.html
---

# Brief 05 - Move Broad PR Browse Sync to Connections and /pull-requests

## Task

Make healthy sync-enabled Connections the only broad PR sync source and move
browse-only open PR visibility to `/pull-requests`. Non-opted repositories must
not flood `/reviews`.

This brief may add a functional `/pull-requests` route and minimal page shell.
The final readiness/pull-request UI composition is brief 09.

## Requirements

- Replace sync over old GitHub PAT rows and env fallback with sync over active,
  healthy Connections where `sync_enabled=true`.
- Support both GitHub and ADO Connections.
- Persist `connection_id` provenance on `sync_sources` and `synced_prs`.
- Keep repo links independent from sync:
  - repo links may use `sync_enabled=false` Connections
  - manual reviews/scans may use sync-disabled Connections
- Keep existing exclusions as browse-only:
  - broad sync/listing can hide excluded repos from `/pull-requests`
  - exclusions must not block explicit repo links or readiness candidates
- Add `/pull-requests` as the broad browse page route.
- Redirect `/pr-dashboard` to `/pull-requests`.
- Update `/browse-pr` routing to the new browse page or explicit review flow as
  appropriate for the existing route's semantics.
- Keep `/reviews` free of non-opted broad synced PR rows.

## Required Facts

- `fact-sync-uses-healthy-connections`
- `fact-exclusions-browse-only`
- `fact-non-opted-prs-stay-in-pull-requests`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not start reviews from broad sync.
- Do not silently import env PATs into Connections.
- Do not remove exclusions; narrow their effect to broad browse.
- Keep final `/reviews` layout work for brief 09.

## Wrap-Up

- Seed/dev data should include enough synced PRs to see `/pull-requests`.
- Keep old browse endpoints redirected or compatible enough that existing links
  do not 404.
