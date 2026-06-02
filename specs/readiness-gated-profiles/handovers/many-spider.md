# Handover: many-spider

## Built

- Extended `/api/reviews/queue` to merge normal review rows with opted-in
  readiness candidates from `storage.list_active_readiness_candidates()`.
- Candidate queue rows use `subject_type="candidate"` and `row_key="candidate:<id>"`
  so the UI can distinguish readiness rows from completed review rows.
- `/reviews` now renders waiting and user-actionable blocked candidates in a
  readiness panel. Completed review rows still navigate to `/reviews/{review_id}`.
- Draft candidates and technical/config-style readiness failures are hidden from
  `/reviews` by default. Included blocked reasons are currently checks failed,
  checks timeout, fork manual-start, paused repo link, and disabled auto-review.
- Added candidate actions on the panel:
  - `Start Review Now` for ordinary signed-in users
  - `Override Readiness & Start Review` only when `/api/me` reports admin or
    `can_manage_profiles`
- Expanded `/pull-requests` into the broad synced PR browse surface with filters,
  tabs, Connection provenance, linked repo status, Guardian status, side-panel
  actions, manual start, open PR, sync, and hide-from-browse behavior.
- Kept broad synced PRs out of `/reviews`; they remain sourced from `/api/prs`.
- Changed repository-review trigger copy away from scan language. Repository URLs
  now show "repository review options" and "Will review ... repository".
- Restored `/scans` and `/scans/{scan_id}` to serve the actual scan page instead
  of redirecting to `/reviews?subject=repo`, so recent-changes/maintenance scans
  remain distinct from repository review.
- Added required browser fact scripts and a backend queue filtering test.

## Deviations

- The advisory expected list did not include `src/pr_guardian/api/dashboard_page.py`,
  but the brief explicitly required routing behavior and scan/repo-review copy
  separation. I changed `/scans` there because the existing redirect made scan
  history look like a repository-review mode under `/reviews`.
- Browser facts keep the existing deterministic Node mock-server pattern used by
  earlier pods. When Playwright is available they capture desktop/narrow
  screenshots; if Playwright is unavailable they still perform source/API wiring
  assertions and write fallback evidence.

## Interfaces downstream pods should know

- `/api/reviews/queue` items now may include:
  - `subject_type="candidate"`
  - `row_key="candidate:<candidate_id>"`
  - `state`
  - `reason`
  - `readiness: { state, reason, snapshot }`
  - candidate provenance IDs and Connection snapshot fields
- Review rows now also include `row_key="review:<review_id>"`.
- The `/reviews` client posts candidate actions to the existing endpoints:
  - `POST /api/reviews/candidates/{candidate_id}/start`
  - `POST /api/reviews/candidates/{candidate_id}/override`
- `/pull-requests` still depends on the existing browse API surface:
  - `GET /api/prs`
  - `POST /api/prs/{pr_uuid}/start-wizard`
  - `POST /api/prs/exclude-repo`
  - `POST /api/prs/sync`

## Files to avoid changing without a good reason

- `src/pr_guardian/api/reviews_queue.py` around candidate queue shaping and
  default visibility rules.
- `src/pr_guardian/dashboard/reviews_queue.html` readiness panel and repository
  review trigger copy.
- `src/pr_guardian/dashboard/pull_requests.html` browse panel and actions.
- `tests/browser/reviews_readiness_panel.spec.mjs`
- `tests/browser/pull_requests_page.spec.mjs`
- `tests/browser/repo_review_copy.spec.mjs`
- `tests/test_reviews_queue_candidates.py`

## Landmines

- Do not source broad synced PR rows from `/api/reviews/queue`. `/reviews` should
  only show normal review rows plus opted-in readiness candidates.
- `reason="draft"` waiting candidates are intentionally hidden from `/reviews`.
- Profile/connection/platform technical failures are hidden as queue rows by
  default; platform readiness status carries those failures.
- Repository review is still invoked through the existing trigger `mode="scan"`
  backend path for compatibility, but user-facing copy should call it repository
  review. Actual recent-changes/maintenance scans remain named scans.
- `/scans` is intentionally separate again. Do not redirect it into `/reviews`
  unless the information architecture changes explicitly.

## Verification

- `node tests/browser/reviews_readiness_panel.spec.mjs --grep reviews-shows-opted-readiness-candidates`
- `node tests/browser/reviews_readiness_panel.spec.mjs --grep readiness-panel-actions-respect-permissions`
- `node tests/browser/pull_requests_page.spec.mjs`
- `node tests/browser/repo_review_copy.spec.mjs`
- `python -m pytest tests/test_reviews_queue_candidates.py`
- `npm run check:js`
- `validate_locally` passed lint, build, and full pytest: 568 passed.
