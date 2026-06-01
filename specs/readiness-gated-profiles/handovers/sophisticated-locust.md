# Handover: sophisticated-locust

## Built

- Added the Brief 07 durable handoff from readiness candidate to review:
  `storage.try_start_candidate_review()` compare-and-sets an active candidate to
  `reviewing`, records the transition, and creates one Review row with candidate,
  Profile, Connection, repo link, and snapshot provenance.
- Wired `readiness.evaluate_candidate()` so ready automatic candidates start one
  background review through that handoff. Transition source remains the caller
  (`webhook`, `reconciler`, etc.); Review row `review_source` is `automatic`.
- Split review status from readiness status in the orchestrator by writing
  `guardian/review` through `set_review_status`. Stale automatic reviews skip
  pending and final review statuses plus comments, labels, reviewers, formal
  approval, and request-changes.
- Refactored `_post_results()` into profile-gated side-effect steps:
  statuses are always posted for eligible non-stale runs; comments, labels,
  reviewer requests, formal approve, and formal request-changes respect the
  resolved `GuardianConfig.side_effects`. `approve_pr()` additionally requires
  `platform_approval_enabled` and a non-fork PR.
- Changed AUTO_APPROVE platform copy to `Guardian cleared`, keeping formal
  platform approval separate from Guardian clearance.
- Added manual candidate actions in `/api/reviews/candidates/{candidate_id}`:
  `/start` atomically claims the candidate with `reason=manual_bypass` and queues
  a `manual_bypass` review without marking readiness success; `/override`
  requires Profile Manager access, confirmation, and reason, atomically claims
  the candidate, then writes readiness success, records audit, and starts one
  review.
- Hardened human finalization paths. `/api/reviews/{id}/finalize` and
  `/api/dashboard/reviews/{id}/submit-verdict` reject API keys, use the stored
  current Connection token when `connection_id` exists, fail on archived or
  inaccessible Connections, and append `actor_email` to the review log.
- Added required fact coverage in `tests/test_candidate_review_lifecycle.py`,
  `tests/test_profile_side_effects.py`, and `tests/test_finalize_review.py`.

## Deviations

- The requested parent handover
  `specs/readiness-gated-profiles/handovers/bloody-raccoon.md` was not present.
  I read the available dependency handover `rapid-sailfish.md`; the missing file
  was also absent from the handover directory listing.
- I touched `src/pr_guardian/api/review.py` and `src/pr_guardian/auth/dependencies.py`
  outside the advisory expected list. `api/review.py` needed to mark dashboard
  manual PR reviews as allowing per-run comment-mode override; `auth/dependencies.py`
  needed a human-only dependency so finalization can reject API keys.

## Interfaces downstream pods should know

- New storage helpers:
  `try_start_candidate_review`, `mark_candidate_reviewed_for_review`, and
  `record_profile_audit_event`.
- `run_review()` now accepts `manual_comment_override: bool = False`. Manual UI
  entry points pass `True`; automatic/API/profile-governed paths leave it false.
- New Reviews API endpoints:
  `POST /api/reviews/candidates/{candidate_id}/start` and
  `POST /api/reviews/candidates/{candidate_id}/override`.
- Review rows now use `review_source` values `automatic`, `manual_bypass`,
  `override`, `manual`, `api_key`, and existing `re_review` paths.
- Stale automatic review detection relies on the Review row's stored
  `head_commit_sha` and `review_source == "automatic"`, then calls
  `adapter.fetch_pr_metadata()` before any review status or result side effects.

## Files to avoid changing without a good reason

- `src/pr_guardian/persistence/storage.py` candidate/review lifecycle helpers.
- `src/pr_guardian/core/orchestrator.py` side-effect gating and stale-SHA guard.
- `src/pr_guardian/core/readiness.py` automatic review handoff.
- `src/pr_guardian/api/reviews_queue.py` candidate manual action and finalization
  Connection logic.
- `tests/test_candidate_review_lifecycle.py`,
  `tests/test_profile_side_effects.py`, and the new fact in
  `tests/test_finalize_review.py`.

## Landmines

- `try_start_candidate_review()` intentionally creates the Review row in the same
  transaction as the candidate `reviewing` transition. Do not split those writes
  or duplicate starts can reappear under webhook/reconciler races.
- `Decision.AUTO_APPROVE` is only Guardian clearance. Formal platform approval
  requires both `config.platform_approval_enabled` and
  `config.side_effects.formal_approve`, and must not run on forks.
- Manual bypass must not mark readiness success. Override is the path that writes
  readiness success and audit.
- The human-only dependency is strict: anonymous callers and API keys are both
  rejected. Tests that finalize or submit human verdicts now send a masked
  signed-in user header.

## Verification

- `validate_locally` passed lint, build, and full pytest: 565 passed.
- Required facts passed:
  - `python -m pytest tests/test_candidate_review_lifecycle.py::test_ready_candidate_transitions_to_one_review_under_concurrency`
  - `python -m pytest tests/test_profile_side_effects.py::test_auto_approve_is_guardian_clearance_unless_profile_enables_platform_approval`
  - `python -m pytest tests/test_candidate_review_lifecycle.py::test_stale_automatic_review_skips_platform_side_effects`
  - `python -m pytest tests/test_candidate_review_lifecycle.py::test_manual_bypass_and_manager_override_have_distinct_readiness_audit`
  - `python -m pytest tests/test_finalize_review.py::test_api_keys_cannot_finalize_and_signed_in_user_uses_stored_connection`
