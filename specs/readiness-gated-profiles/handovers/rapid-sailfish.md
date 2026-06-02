# Handover: rapid-sailfish

## Built

- Added platform readiness protocol DTOs and methods for PR metadata, automated
  readiness signals, distinct readiness/review status writes, and Archmap lookup.
- Implemented GitHub readiness metadata, check-run/status collection, Guardian
  readiness/review statuses, and `archmap-<sha>` artifact lookup.
- Implemented ADO readiness metadata, automated PR status collection with
  human-review signals filtered out, Guardian readiness/review statuses, and
  build artifact lookup by `archmap-<sha>`.
- Added `src/pr_guardian/core/readiness.py`, a platform-neutral candidate
  engine that creates candidates only for exact active auto-review repo links,
  supersedes older same-PR head SHAs, re-reads live link/Profile/Connection
  state on each evaluation, and transitions ready candidates to `reviewing`
  without calling `run_review()`.
- Added `src/pr_guardian/core/readiness_reconciler.py` and registered its
  background loop in app startup/shutdown next to the existing PR sync loop.
- Reworked public GitHub and ADO webhook handlers to require configured shared
  secrets unless `GUARDIAN_WEBHOOK_DEV_BYPASS=1`, route PR/check/status events
  into durable candidates, and stop directly enqueueing `run_review()`.
- Added required fact coverage in `tests/test_readiness_engine.py` and
  `tests/test_webhook_security_and_router.py`.

## Deviations

- The parent handover path `specs/readiness-gated-profiles/handovers/flat-takin.md`
  was not present. I read the available dependency handovers
  `ashamed-rhinoceros.md`, `payable-skink.md`, and `cruel-reptile.md`.
- Brief 06 intentionally stops at the durable handoff: a ready automatic
  candidate transitions to `reviewing`, but no review execution is started.
  Brief 07 should attach compare-and-set review startup and provenance.
- Status writes are treated as part of platform permission health. If writing
  `guardian/readiness` fails during evaluation, the candidate becomes
  `error/status_write_failed` rather than silently starting review.

## Interfaces downstream pods should know

- New protocol types:
  `PlatformPRMetadata(head_sha, draft, fork, closed, merged)` and
  `PlatformReadinessSignal(name, state, source, url, description)`.
- New adapter methods:
  `fetch_pr_metadata`, `fetch_readiness_signals`, `set_readiness_status`,
  `set_review_status`, and `find_archmap_artifact`.
- `readiness.create_or_update_candidate_from_pr(pr, source, adapter=None)` is
  the webhook entry point for new PRs/new commits. It returns `None` for
  unlinked or disabled repos.
- `readiness.evaluate_candidate(candidate_id, source="reconciler", adapter=None)`
  re-reads live repo link, Profile, and Connection and records the state
  transition.
- `readiness.evaluate_candidates_for_sha(platform, repo, head_sha, source)` is
  the GitHub check/status webhook path.
- `readiness.supersede_candidates_for_pr(pr, reason=...)` handles PR close,
  merge, and new-commit supersession cases.
- Storage helpers added for downstream use:
  `get_active_repo_link_for_repo`, `get_readiness_candidate_by_id`,
  `list_active_readiness_candidates`, and
  `list_recoverable_readiness_candidates`.
- Webhook secret behavior is now strict by default:
  GitHub requires `GITHUB_WEBHOOK_SECRET` plus a valid
  `X-Hub-Signature-256`; ADO requires `ADO_WEBHOOK_SECRET` plus
  `X-ADO-Webhook-Token` or `Authorization: Bearer ...`.

## Files to avoid changing without a good reason

- `src/pr_guardian/core/readiness.py`
- `src/pr_guardian/core/readiness_reconciler.py`
- `src/pr_guardian/api/webhooks.py`
- `src/pr_guardian/platform/protocol.py`
- `src/pr_guardian/platform/github.py` readiness methods
- `src/pr_guardian/platform/ado.py` readiness methods
- `tests/test_readiness_engine.py`
- `tests/test_webhook_security_and_router.py`

## Landmines

- `reviewing` currently means "durable readiness handoff reached", not
  "orchestrator is running." Brief 07 must add the actual review startup and
  duplicate-start compare-and-set semantics.
- Draft PR candidates do not carry `readiness_started_at`; the max-wait clock
  starts only after a non-draft evaluation.
- Guardian's own statuses (`guardian/readiness`, `guardian/review`, and legacy
  `pr-guardian`) are filtered out of readiness signals.
- ADO policy/status filtering is conservative and excludes signals containing
  reviewer/approval/vote/manual markers so human-review policies do not block
  automated readiness.
- Linking a repo still does not backfill open PRs; only webhooks or explicit
  future manager actions should create candidates.

## Verification

- `python -m pytest tests/test_readiness_engine.py::test_opted_pr_waits_for_checks_then_becomes_reviewable`
- `python -m pytest tests/test_readiness_engine.py::test_failed_timeout_fork_and_permission_readiness_outcomes`
- `python -m pytest tests/test_webhook_security_and_router.py::test_github_and_ado_webhooks_require_valid_secrets`
- `python -m pytest tests/test_readiness_engine.py::test_reconciler_starts_candidate_after_missed_check_event`
- `python -m pytest tests/test_readiness_engine.py::test_archmap_wait_times_out_soft_and_ado_uses_sha_artifact_name`
- `validate_locally` passed lint, build, and full pytest: 560 passed.
