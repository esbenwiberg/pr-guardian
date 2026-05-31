---
title: "Add readiness candidate engine and platform readiness adapters"
depends_on:
  - 01-add-profile-readiness-data
  - 03-replace-review-yml-with-profile-resolver
touches:
  - src/pr_guardian/core/readiness.py
  - src/pr_guardian/core/readiness_reconciler.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/main.py
  - tests/test_readiness_engine.py
  - tests/test_webhook_security_and_router.py
does_not_touch:
  - src/pr_guardian/dashboard/
  - src/pr_guardian/core/orchestrator.py
---

# Brief 06 - Add Readiness Candidate Engine and Platform Readiness Adapters

## Task

Implement durable readiness candidates for linked repos and platform readiness
evaluation for GitHub and Azure DevOps. Webhooks discover or update candidates;
the reconciler catches missed events and delayed check transitions.

No webhook should directly start `run_review()` before a durable candidate
transition to `reviewing` is possible. The actual review handoff and side-effect
gating are finalized in brief 07.

## Requirements

- Add platform protocol methods for PR metadata, draft/fork state,
  checks/statuses/policies, readiness status writes, review status writes, and
  Archmap artifact lookup by head SHA.
- GitHub readiness listens to and/or handles:
  - `pull_request`
  - `check_run`
  - `check_suite`
  - `status`
  - `workflow_run`
- ADO readiness uses automated policies/statuses only and ignores human-review
  policies.
- ADO Archmap artifact lookup is in scope using convention `archmap-<sha>`.
- Public GitHub/ADO webhook requests fail when shared secret/header token is
  missing or invalid. Keep explicit dev/test bypass only.
- Create candidates only for exact linked repos with auto-review enabled.
- Do not surprise-backfill open PRs when a repo is linked. Only new PRs/new
  commits create candidates unless a manager explicitly asks to create
  candidates for current open PRs.
- Candidate evaluation re-reads current repo link, Profile, and Connection on
  each evaluation until review start.
- Candidate state/reason behavior:
  - draft PRs wait, are hidden from `/reviews`, and do not accrue max wait time
  - no visible automated checks means ready if the query succeeds
  - permission/API errors become candidate errors
  - failed checks/statuses block automatic review for the same SHA
  - timeout blocks automatic review but remains recoverable
  - fork-origin PRs block automatic review with `fork_requires_manual_start`
  - close/merge/new commit supersedes previous candidates as appropriate
  - Archmap expected waits briefly and times out soft with warning
- Post `guardian/readiness` pending on candidate create/update, failure on
  blocked/error, and success only when ready/review starts or later override
  marks success.
- Add a reconciler that periodically re-evaluates waiting and recoverable
  blocked candidates.

## Defaults

```text
quiet_period_seconds = 10
max_wait_minutes = 30
archmap_max_wait_minutes = 10
```

## Required Facts

- `fact-opted-pr-waits-then-starts-review`
- `fact-failed-timeout-fork-blocks`
- `fact-strict-webhook-secrets`
- `fact-reconciler-recovers-missed-events`
- `fact-archmap-soft-wait-and-ado-lookup`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not show candidate rows in the UI in this brief.
- Do not make branch protection the readiness source of truth; use all
  non-ignored visible automated checks/statuses.
- Do not wait on Guardian's own readiness/review statuses.
- Do not treat failed checks as terminal forever; same-SHA recovery is allowed.
- Do not perform formal approval on fork PRs, even after manual start.

## Wrap-Up

- Register the reconciler with app startup/shutdown if this repo already has a
  background-task pattern; otherwise add the smallest local pattern and document
  it in code.
- Add focused tests around the state machine before platform integration tests
  become broad.
