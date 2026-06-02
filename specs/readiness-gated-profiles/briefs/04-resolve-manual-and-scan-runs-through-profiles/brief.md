---
title: "Resolve manual reviews, repo reviews, and scans through Profiles"
depends_on:
  - 02-add-profile-management-api
  - 03-replace-review-yml-with-profile-resolver
touches:
  - src/pr_guardian/api/review.py
  - src/pr_guardian/api/agent_api.py
  - src/pr_guardian/api/scans.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/core/repo_review.py
  - src/pr_guardian/core/recent_changes.py
  - src/pr_guardian/core/maintenance.py
  - tests/test_manual_profile_resolution.py
  - tests/test_scan_profile_provenance.py
does_not_touch:
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/dashboard/reviews_queue.html
---

# Brief 04 - Resolve Manual Reviews, Repo Reviews, and Scans Through Profiles

## Task

Route every non-automatic execution path through the Profile/Connection
resolver and persist Profile/Connection provenance on reviews, repo reviews,
re-reviews, and scans.

Automatic readiness candidates are handled by later briefs. This brief keeps
manual execution immediate.

## Requirements

- Manual signed-in PR reviews:
  - linked repo uses repo link Profile and Connection automatically
  - `/pull-requests` rows use the Connection that synced/saw the PR
  - same user/platform may reuse the last successful active Connection
  - exactly one active Connection for the platform may be inferred
  - otherwise return/show a picker requirement
- If inferred Connection hydration fails, do not try every other Connection.
  Fail with an actionable picker/error.
- API-key review triggers:
  - allowed only for linked repos
  - use the linked repo Profile and Connection
  - cannot administer Profiles or override readiness
- Manual comment mode can override comments for the run.
- Manual reviews use linked repo Profile when present, otherwise default/noop.
- Re-review uses the original review's stored Connection/Profile snapshot and
  side-effect settings. It should not silently drift because the live Profile
  changed after the original review started.
- Repo review remains distinct from scans:
  - small/bounded repository snapshot
  - synthetic PR review through the review pipeline
  - appears under Reviews
  - uses the same Profile/Connection resolver
  - platform side effects always suppressed
- Scans:
  - linked scans use repo link Profile and Connection
  - unlinked scans use default/noop Profile plus selected/inferred Connection
  - rows store `profile_id`, `profile_snapshot`, `connection_id`,
    `connection_snapshot`, and optional `repo_link_id`
  - issue/work-item creation is controlled by Profile side-effect switch and
    defaults off
- Rename internal/user-facing repo-review mode away from "scan" in API copy
  where this brief touches execution responses. Full UI copy is brief 09.

## Required Facts

- `fact-scan-stores-profile-connection-provenance`
- `fact-manual-review-resolution`
- `fact-scan-issue-side-effect-switch`
- `fact-repo-review-suppresses-platform-side-effects`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not route automatic webhook reviews here.
- Do not add new scan-baseline product concepts.
- Do not let unlinked API-key reviews choose arbitrary Connections.
- Do not post platform comments, labels, approvals, or request-changes from repo
  review.

## Wrap-Up

- Keep behavior compatible with the existing review pipeline shape.
- Ensure the orchestrator can receive a resolved config/snapshot without
  re-reading repo-local config.
