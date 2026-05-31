---
title: "Gate platform side effects and candidate/review transitions"
depends_on:
  - 04-resolve-manual-and-scan-runs-through-profiles
  - 06-add-readiness-candidate-engine
touches:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/decision/actions.py
  - src/pr_guardian/api/reviews_queue.py
  - src/pr_guardian/api/dashboard.py
  - src/pr_guardian/platform/protocol.py
  - tests/test_profile_side_effects.py
  - tests/test_candidate_review_lifecycle.py
  - tests/test_finalize_review.py
  - tests/test_submit_verdict.py
does_not_touch:
  - src/pr_guardian/config/loader.py
  - src/pr_guardian/dashboard/profiles.html
---

# Brief 07 - Gate Platform Side Effects and Candidate/Review Transitions

## Task

Wire readiness candidates into the review pipeline safely, split readiness
status from review status, and gate every platform side effect through the
resolved Profile.

`Decision.AUTO_APPROVE` means Guardian clearance unless the resolved Profile
explicitly enables formal platform approval.

## Requirements

- Add a durable review start transition:
  - automatic review starts only after `try_mark_candidate_reviewing`
  - simultaneous webhook/reconciler attempts enqueue at most one review
  - the review row stores candidate, Profile, Connection, repo link, source, and
    snapshots
- Update candidate state after review completion:
  - `reviewing` -> `reviewed` when the linked review completes for the same SHA
  - active candidate -> `superseded` on close/merge/new commit
  - stale automatic review completion must not post platform side effects
- Split platform statuses:
  - `guardian/readiness` for candidate readiness
  - `guardian/review` for review execution/result
- Implement manual actions:
  - signed-in `Start Review Now` bypasses readiness and links candidate to a
    manual review when a candidate exists
  - Admin/Profile Manager `Override Readiness & Start Review` requires reason
    and confirmation, marks readiness success as manual override, and starts
    review
  - readiness override audit stores actor, timestamp, previous candidate
    snapshot, and reason
- Redesign `_post_results()` into separate readable side-effect steps:
  - statuses
  - comments
  - labels
  - reviewer requests
  - formal approval
  - formal request-changes
- Apply Profile side-effect switches to all review runs:
  - automatic
  - manual
  - manual bypass
  - override
  - re-review
- Keep statuses always on.
- Let manual `comment_mode` override comments for that run.
- Do not call `approve_pr()` unless `platform_approval_enabled` is true on the
  resolved Profile and the PR is not a fork-origin PR.
- UI/API copy should distinguish `Guardian cleared` from formal platform
  approval.
- Human finalization remains signed-in-only:
  - API keys cannot finalize, approve, request changes, or post human verdicts
  - finalization uses the review's stored Connection ID with current token
  - finalization fails clearly if the Connection is archived or inaccessible
  - review log stores actor email

## Required Facts

- `fact-ready-candidate-enqueues-once`
- `fact-auto-approve-is-clearance-unless-enabled`
- `fact-stale-sha-skips-side-effects`
- `fact-manual-bypass-and-override-audit`
- `fact-finalization-signed-in-only`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not infer formal platform approval from `Decision.AUTO_APPROVE` alone.
- Do not mark readiness success for normal manual bypass.
- Do not block manual start just because readiness is failed, timed out, or
  waiting.
- Do not post platform side effects for stale automatic reviews.
- Do not let API keys perform human finalization.

## Wrap-Up

- Update comments/status bodies enough that platform output explains manual
  override versus automatic readiness.
- Leave visual queue rendering to brief 09, but expose the API shape it needs.
