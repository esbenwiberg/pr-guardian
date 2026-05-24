# Auto-approve gates

## Problem

PR Guardian can auto-approve without durable repo consent because
`auto_approve.enabled` defaults to true and repo config is loaded from a local
temporary checkout path instead of the PR target branch. The hotspot gate is
also ineffective: `load_hotspots()` returns an empty set and the docs still
describe a nightly/precomputed model that does not fit webhook-first repos.
Finally, re-review paths can auto-approve after all findings are dismissed or
resolved without rechecking the same structural gates, which lets config and
hotspot blockers be bypassed.

## Outcome

Auto-approval happens only after a shared final gate confirms target-branch
explicit consent, no root config edit, no respected hotspot hit/failure, and no
other structural blocker; otherwise reviewers see structured trigger details in
the existing review detail and wizard surfaces.

## Users

- Repo owners who want auto-approve to be explicit opt-in rather than a service
  default.
- Reviewers who need to know why an otherwise low-risk PR still needs human
  eyes.
- PR authors whose all-resolved re-reviews should not exploit a different
  approval path from initial reviews.
- Operators maintaining the docs and support model for webhook-first repos.

## Success signal

A low-risk PR is platform-approved only when the target branch `review.yml`
explicitly enables auto-approve and sets repo risk; missing/invalid consent,
current-path `review.yml` edits, respected hotspot hits/failures, and re-review
shortcut cases all produce structural trigger details instead of platform
approval.

## Non-goals

- No warning banner, migration banner, or soft rollout period. This is a hard
  cut because the product is not live yet.
- No nightly hotspot job, DB hotspot cache, scheduled refresh, or
  `.pr-guardian/hotspots.json`.
- No label/issue based fix-commit heuristic in v1; commit-message matching is
  enough.
- No rename-away detection for `review.yml` config-policy edits in v1; only a
  current diff path exactly equal to `review.yml` blocks.
- No new dashboard surface. Existing review detail and wizard surfaces are the
  UI targets.
- No change to manual human verdict approval. `submit_verdict` remains a human
  action, not auto-approval.

## Glossary

- **Target-branch consent** - `review.yml` read from the PR base/target branch,
  not from the PR head, with explicit `auto_approve.enabled: true` and explicit
  `repo_risk_class`.
- **Candidate auto-approval** - a decision that would auto-approve before final
  structural gates are applied. Candidate status never calls `approve_pr()`
  directly.
- **Final auto-approval gate** - the single shared check that runs immediately
  before any auto-approval can survive into platform side effects.
- **Config policy trigger** - a sticky structural trigger with kind
  `config_policy` explaining missing/invalid/incomplete consent or a root
  `review.yml` current-path edit.
- **Hotspot** - a changed file whose last 90 days of path history meet the
  configured churn and fix-ratio thresholds.
- **Fix commit heuristic** - case-insensitive commit-message v1 matching
  `fix`, `bug`, `hotfix`, `patch`, `revert`, plus conventional `fix:` and
  `fix(...)`.
- **Hotspot exemption** - a reasoned per-path config entry under
  `auto_approve.hotspot_exemptions`, used to exclude especially generated or
  intentionally noisy files from hotspot blocking.
- **Sticky trigger details** - structured `StickyTrigger.details` data persisted
  through the decision, storage, API, and UI boundary.

## Reversibility

There is no DB migration or on-disk data format change. Rollback is a code
revert of schema defaults, config loading, hotspot evaluation, final-gate
wiring, and UI rendering. The one API-shape change is additive:
`StickyTrigger.details` and `config_policy` are added to the existing sticky
trigger contract. Removing them later would require a small compatibility pass
for stored review JSON that may contain those keys.
