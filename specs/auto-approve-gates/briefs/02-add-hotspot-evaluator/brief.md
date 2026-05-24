---
title: "Add on-demand hotspot evaluator"
touches:
  - src/pr_guardian/triage/hotspots.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
  - src/pr_guardian/core/orchestrator.py
  - tests/test_hotspots.py
  - tests/test_github_adapter.py
  - tests/test_ado_adapter.py
does_not_touch:
  - src/pr_guardian/persistence/
  - src/pr_guardian/dashboard/
  - docs/plan/
---

## Task

Replace the empty nightly/precomputed hotspot stub with on-demand per-path
evaluation over changed files, using platform commit history, reasoned
exemptions, process-local caching, and fail-closed lookup failures.

## Touches

- `src/pr_guardian/triage/hotspots.py` - implement evaluator, cache, fix-message
  heuristic, exemptions, and compatibility helpers.
- `src/pr_guardian/platform/protocol.py` - extend path-history signature with
  `since`, `ref`, and message expectations.
- `src/pr_guardian/platform/github.py` - pass path, since, ref/sha, and per-page
  values to the commits endpoint.
- `src/pr_guardian/platform/ado.py` - pass equivalent ADO search criteria and
  normalize commit message/date fields.
- `src/pr_guardian/core/orchestrator.py` - call the evaluator for changed files
  and attach the result for the final gate.
- `tests/test_hotspots.py` - evaluator, threshold, exemption, cache, and failure
  tests.
- `tests/test_github_adapter.py` - GitHub path-history request/shape tests.
- `tests/test_ado_adapter.py` - Azure DevOps path-history request/shape tests.

## Does not touch

- `src/pr_guardian/persistence/` - hotspot cache is in-memory only.
- `src/pr_guardian/dashboard/` - UI rendering is Brief 04.
- `docs/plan/` - docs cleanup is Brief 05.

## Constraints

Follow `design.md` -> Contracts -> Platform path-history contract and Hotspot
result. The v1 fix heuristic is commit-message only and case-insensitive:
match `fix`, `bug`, `hotfix`, `patch`, `revert`, plus conventional `fix:` and
`fix(...)`. No PR labels, issue labels, or linked issue metadata in v1.

Cache is process-local memory with a 24 hour TTL. Do not introduce Postgres,
Redis, files, scheduled jobs, or background refresh.

## Test expectations

`tests/test_hotspots.py` should cover threshold hits, below-threshold misses,
fix-message regex behavior, reasoned path exemptions, cache hit/miss behavior,
and lookup failure while `respect_hotspots` is true.

Adapter tests should prove path-history calls include the time/ref inputs and
return commit messages in the normalized shape consumed by the evaluator.

## Risks / pitfalls

The existing `core/maintenance.py` call site uses
`fetch_commits_for_path(repo, path, per_page=1)`. Updating the protocol to a
keyword-only shape must keep existing maintenance behavior compiling.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
