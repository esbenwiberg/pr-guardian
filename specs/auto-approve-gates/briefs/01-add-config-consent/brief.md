---
title: "Add explicit target-branch auto-approve consent"
touches:
  - src/pr_guardian/config/schema.py
  - src/pr_guardian/config/defaults.yml
  - src/pr_guardian/config/loader.py
  - src/pr_guardian/core/orchestrator.py
  - tests/test_auto_approve_config.py
  - tests/test_repo_config_loading.py
  - tests/test_decision.py
does_not_touch:
  - src/pr_guardian/platform/
  - src/pr_guardian/triage/hotspots.py
  - src/pr_guardian/dashboard/
---

## Task

Make auto-approve a hard-cut explicit opt-in. Defaults must deny automated
approval unless the PR target branch `review.yml` explicitly sets
`auto_approve.enabled: true` and explicitly sets `repo_risk_class`.

## Touches

- `src/pr_guardian/config/schema.py` - add hotspot config fields and set
  `AutoApproveConfig.enabled` default to false.
- `src/pr_guardian/config/defaults.yml` - set `auto_approve.enabled: false`
  and add the default hotspot config values.
- `src/pr_guardian/config/loader.py` - add a provenance-preserving config load
  result for raw `review.yml` bytes and explicitness checks.
- `src/pr_guardian/core/orchestrator.py` - load target-branch `review.yml`
  through `PlatformAdapter.fetch_file_content(..., ref=pr.target_branch)`.
- `tests/test_auto_approve_config.py` - schema/default tests.
- `tests/test_repo_config_loading.py` - loader/orchestrator provenance tests.
- `tests/test_decision.py` - consent blocker decision coverage.

## Does not touch

- `src/pr_guardian/platform/` - existing `fetch_file_content` is enough for this
  brief.
- `src/pr_guardian/triage/hotspots.py` - Brief 02 owns hotspot evaluation.
- `src/pr_guardian/dashboard/` - UI representation is Brief 04.

## Constraints

Follow `design.md` -> Contracts -> Auto-approve config shape and Repo config
load result. PR-head `review.yml` must not grant consent. Missing, invalid, or
incomplete consent is represented as policy state first; Brief 03 turns it into
a `config_policy` trigger only when the PR is otherwise an auto-approve
candidate.

## Test expectations

`tests/test_auto_approve_config.py` should prove schema and defaults now disable
auto-approve and expose the hotspot config defaults:
`respect_hotspots: true`, `min_commits_90d: 8`, `min_fix_ratio: 0.3`, and empty
`hotspot_exemptions`.

`tests/test_repo_config_loading.py` should cover found, missing, invalid, and
incomplete target-branch config load results, including explicitness of
`auto_approve.enabled` and `repo_risk_class`.

`tests/test_decision.py` should cover the interim behavior that incomplete
consent is available to the decision layer without silently defaulting to
approval.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
