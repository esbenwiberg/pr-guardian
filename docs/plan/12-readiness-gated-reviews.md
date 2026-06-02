# 12 - Readiness-Gated Reviews

## Purpose

PR Guardian should run its full review only when the pull request is ready to
be reviewed. "Ready" means the PR head SHA is current, platform-visible checks
have finished successfully, and optional pre-review artifacts such as Archmap
are available before triage and agent selection happen.

This document is a handover for the Guardian team. It compares trigger models,
names the recommended architecture, and lists the concrete code seams in the
current repo.

## Executive Summary

The current GitHub integration starts a review from the `pull_request` webhook.
That is too early for two reasons:

1. CI checks, CodeQL, lint, tests, Copilot, CodeRabbit, and the Archmap export
   workflow often start from the same PR event. They are not done when the PR
   webhook arrives.
2. Archmap is most valuable before PR Guardian chooses risk tier and agent set.
   If Archmap arrives after `run_review()` starts, it can still flag human
   review later, but it cannot shape review effort.

The recommended design is a Guardian-owned readiness gate:

1. `pull_request` webhooks discover or update review candidates.
2. `check_run`, `check_suite`, `status`, and `workflow_run` webhooks notify
   Guardian that readiness may have changed.
3. Guardian re-reads the PR's current head SHA and check state from GitHub.
4. Guardian starts `run_review()` only when readiness is satisfied.
5. A periodic reconciler catches missed events and handles webhook delivery
   gaps.

Consumer repositories should not need to call Guardian. They publish ordinary
GitHub checks and optional artifacts. Guardian observes GitHub state and decides
when to run.

## Current State

The current GitHub webhook handler accepts only `pull_request` events and
immediately enqueues a review:

```text
src/pr_guardian/api/webhooks.py
```

Relevant current flow:

```text
pull_request opened/synchronize/reopened
  -> normalize_webhook()
  -> ReviewQueue.is_duplicate(repo, pr, head_sha)
  -> ReviewQueue.enqueue(run_review(...))
```

In `run_review()`, Archmap is fetched during discovery:

```text
src/pr_guardian/core/orchestrator.py
```

Current Archmap behavior:

```text
discovery
  -> fetch diff
  -> build local context
  -> adapter.fetch_archmap_artifact(pr)
  -> parse_archmap_artifact(...)
  -> triage
  -> agents
  -> decision
```

This is best-effort. If `archmap-<head_sha>` does not exist yet, Guardian logs
"No Archmap artifact found for this PR head SHA" and continues without it.

The GitHub adapter already has useful primitives:

```text
src/pr_guardian/platform/github.py

- fetch_archmap_artifact(pr)
- list_repo_open_prs(repo), including check-run-derived _ci_status
```

The existing queue is in-process and dedupes by `repo:pr:head_sha` before
enqueuing. That is correct for immediate review, but it is too early for a
readiness-gated design. The dedupe boundary should move from "candidate seen"
to "review started for this ready SHA".

## Design Goals

- Guardian starts full review after checks are green.
- Archmap is available before triage if the repo publishes an Archmap artifact.
- Consumer repos do not have to know Guardian's URL or secrets.
- Guardian can still mark a pending status early if desired.
- Stale events never review an old commit.
- The system recovers from missed webhooks.
- Fork PR behavior is explicit and safe.
- The design works with GitHub first and leaves an ADO equivalent path.

## Non-Goals

- Do not require every repository to add a "call Guardian" CI job.
- Do not commit Archmap artifacts to source control.
- Do not make Guardian wait for its own status check.
- Do not block forever when a repo has no checks, unless configured to do so.
- Do not treat "GitHub says checks are green" as "human review is unnecessary".
  This gate controls when Guardian starts, not what Guardian decides.

## Key GitHub Mechanics

PR webhooks fire on PR activity, not after checks. They are useful for
discovering a PR and its latest head SHA, but not as a readiness signal.

GitHub check and status state is split across:

- `check_run` webhooks: individual check runs, including completed check runs.
- `check_suite` webhooks: suites of check runs, including completed suites.
- `status` webhooks: classic commit statuses.
- REST check-runs API: list check runs for a git ref.
- REST combined-status API: combined classic statuses for a ref.
- GraphQL `statusCheckRollup`: unified rollup available from commit objects.
- `workflow_run` webhooks: workflow lifecycle events, useful for named workflows
  such as `archmap`.

Important caveats:

- GitHub App check-run/check-suite payloads can have incomplete PR linkage for
  fork branches. Guardian must map by `(repo, head_sha)` against known PR
  candidates, not rely only on event `pull_requests`.
- Multiple check systems may publish checks at different times.
- `workflow_run` for Archmap is a good signal that an artifact should exist,
  but Guardian should still verify the artifact commit equals the PR head SHA.
- Guardian must ignore its own check/status context when deciding whether "all
  checks are green"; otherwise it can wait on itself.

## Trigger Options

### Option A - PR Webhook Runs Review Immediately

This is the current design.

Flow:

```text
pull_request webhook
  -> enqueue run_review()
```

Pros:

- Simple.
- Low latency.
- Already implemented.

Cons:

- Races all CI checks and artifacts.
- Archmap often cannot influence triage or agent selection.
- Wastes review cost on PRs that are about to fail build/lint/security checks.
- Produces noisy comments before other systems have settled.

Conclusion:

Keep this only as an emergency fallback or for repos that explicitly opt into
"review immediately".

### Option B - PR Webhook Enqueues a Delayed Waiter

Flow:

```text
pull_request webhook
  -> create candidate
  -> background waiter polls until checks/artifacts are ready
  -> enqueue run_review()
```

Pros:

- Consumer repos do not need Guardian-specific setup.
- Fixes the Archmap race if the waiter is correct.
- Can be implemented mostly inside PR Guardian.

Cons:

- Webhook request must not block, so the "wait" is really an internal job.
- In-memory waiters are fragile across deploys unless persisted.
- Polling every candidate can consume API rate limit.
- Pure polling adds latency or cost depending on interval.

Conclusion:

This is a reasonable stepping stone, but should be persisted and event-driven
quickly. Do not build it as long-lived in-memory sleeps.

### Option C - Consumer Repos Call Guardian When Ready

Flow:

```text
repo CI final job
  needs: [build, lint, security, archmap, ...]
  -> POST /guardian/review-ready
```

Pros:

- Very deterministic.
- Easy readiness semantics: CI decides when all dependencies are done.
- Guardian implementation stays small.
- Archmap can be passed by artifact name or URL.

Cons:

- Every consumer repo must know Guardian.
- Every repo needs endpoint config and auth/secrets.
- Pipelines drift; teams forget to update final job dependencies.
- Harder to onboard broad org coverage.
- Couples Guardian's availability to each repo's CI definition.

Conclusion:

Good for a small controlled fleet or a temporary pilot. It is not the preferred
default if Guardian is meant to be an observing service.

### Option D - Guardian Observes Checks and Artifacts

Flow:

```text
pull_request webhook
  -> upsert review candidate
  -> set optional "PR Guardian waiting for checks" status

check_run/check_suite/status/workflow_run webhook
  -> map event to candidate(s)
  -> evaluate readiness
  -> enqueue run_review() when ready

periodic reconciler
  -> evaluate stale waiting candidates
```

Pros:

- Consumer repos do not call Guardian.
- Review starts after checks are actually green.
- Archmap can be required before triage.
- Event-driven, with polling only as backup.
- Central policy: ignored checks, required checks, and timeouts live in Guardian.
- Scales better organizationally than per-repo callbacks.

Cons:

- More code in Guardian.
- Needs durable candidate state.
- Needs careful stale SHA handling.
- Needs permission to read checks/statuses/actions artifacts.
- GitHub edge cases around forks and missing PR linkage must be handled.

Conclusion:

Recommended target architecture.

### Option E - Polling-Only PR Discovery

Flow:

```text
periodic sync
  -> list open PRs
  -> inspect checks/artifacts
  -> enqueue ready PRs
```

Pros:

- No webhook delivery dependency.
- Guardian already has a PR sync loop for dashboard data.
- Consumer repos do not need to know Guardian.

Cons:

- Latency is tied to polling interval.
- More API calls.
- Hard to make "just finished" feel responsive.
- Still needs durable review-start dedupe.

Conclusion:

Use as a fallback reconciler, not the primary trigger.

## Recommendation

Adopt Option D: Guardian-owned readiness gate, with Option E as the safety net.

The PR webhook should discover candidates. It should not start full review by
default. Check/status/workflow events and a reconciler should transition a
candidate from waiting to ready. Only ready candidates enqueue `run_review()`.

This model gives the desired product behavior:

- Guardian sees the PR early enough to set a pending status.
- Guardian waits until the repo's normal pipeline has spoken.
- Archmap is present before Guardian selects risk tier and agents.
- Consumer repositories do not need Guardian-specific push calls.

## Proposed State Machine

Each candidate is keyed by:

```text
platform + repo + pr_id + head_sha
```

States:

| State | Meaning |
|---|---|
| `candidate` | PR/SHA discovered but not evaluated yet |
| `waiting_checks` | Checks/statuses are pending, missing, or not yet stable |
| `waiting_archmap` | Required checks are green, but Archmap artifact is missing |
| `ready` | All readiness gates passed; review can start |
| `reviewing` | `run_review()` is active for this PR/SHA |
| `reviewed` | Review completed for this PR/SHA |
| `blocked` | Checks failed, PR is draft, merge conflict, or readiness timed out |
| `superseded` | A newer PR head SHA exists |

State transitions:

```text
pull_request opened/synchronize/reopened
  -> upsert candidate for current head_sha
  -> mark older candidates for same PR as superseded
  -> evaluate_readiness()

check_run/check_suite/status/workflow_run event
  -> find candidates by repo + sha, or repo + PR number if available
  -> evaluate_readiness()

reconciler tick
  -> evaluate waiting candidates older than N seconds

evaluate_readiness()
  -> if PR closed: blocked/superseded
  -> if head SHA changed: superseded
  -> if draft and config says wait: blocked/waiting_checks
  -> if checks pending: waiting_checks
  -> if checks failed: blocked
  -> if Archmap required and missing: waiting_archmap
  -> else: ready -> enqueue review
```

## Readiness Definition

Readiness should be a deterministic function over current platform state, not
only webhook payloads.

Inputs:

- PR state: open/closed, draft, mergeable/conflicts, current head SHA.
- Check runs for head SHA.
- Classic commit statuses for head SHA.
- Optional branch protection required contexts, if Guardian has permission and
  the team wants exact required-check semantics.
- Archmap artifact lookup by `archmap-<head_sha>`.
- Repo-level Guardian config.

Default policy:

```yaml
review_readiness:
  enabled: true
  require_checks_success: true
  require_archmap_if_workflow_present: true
  wait_for_draft: true
  wait_for_mergeable: false
  max_wait_minutes: 60
  quiet_period_seconds: 20
  ignored_contexts:
    - "pr-guardian"
    - "PR Guardian"
  success_conclusions:
    - success
    - neutral
    - skipped
  failure_conclusions:
    - failure
    - timed_out
    - action_required
    - cancelled
```

Policy choices to settle:

- Should `cancelled` block or wait? Recommendation: block for the current SHA;
  a new SHA will create a new candidate.
- Should `neutral` count as success? Recommendation: yes by default because many
  optional checks use neutral.
- Should `skipped` count as success? Recommendation: yes unless required checks
  are being read from branch protection.
- Should unknown/no checks review immediately? Recommendation: configurable.
  For a service repo, default to wait for at least one check or a timeout. For a
  docs-only repo, immediate review may be acceptable.

## Archmap Readiness

Archmap should be treated as a pre-review artifact, not as a late decision-only
artifact.

Recommended behavior:

1. If a repo has an `archmap` workflow or publishes `archmap-<head_sha>`, wait
   for the artifact before starting review.
2. Fetch artifact with the existing `fetch_archmap_artifact(pr)` method.
3. Parse and verify:
   - `version == 1`
   - `commit == pr.head_commit_sha`
   - changed files are scoped to the PR
4. If `scope.missing` is non-empty, keep the artifact but surface a warning in
   pipeline log and review detail.
5. If artifact is stale or invalid, ignore it and either wait until timeout or
   start without Archmap depending on config.

Important: Archmap should affect triage, not only final decision.

Today `triage/classifier.py` does not use `context.archmap` when selecting risk
tier and agents. It should. Suggested rules:

```text
Any changed hub file:
  risk_tier = HIGH
  agents = ALL_AGENTS
  reason = "Archmap: changed hub file(s)"

Any changed branch file with risk >= 90:
  risk_tier = at least MEDIUM
  add architecture_intent and test_quality

Any artifact error:
  do not fail review by default, but add a discovery warning
```

The decision engine can still require human review for hub files, but readiness
gating lets Archmap also control how deeply Guardian looks.

## Data Model Proposal

Add a durable table for review candidates. Exact SQLAlchemy naming can follow
existing persistence conventions.

Fields:

```text
id uuid primary key
platform string
repo string
pr_id string
head_sha string
base_sha string nullable
source_branch string
target_branch string
author string
title string
state string
readiness_reason string
checks_status string
checks_summary json
archmap_status string
archmap_artifact_name string nullable
archmap_generated_at string nullable
last_event_type string nullable
last_event_at timestamp
ready_at timestamp nullable
review_started_at timestamp nullable
review_completed_at timestamp nullable
review_db_id uuid nullable
created_at timestamp
updated_at timestamp
```

Uniqueness:

```text
unique(platform, repo, pr_id, head_sha)
```

Useful indexes:

```text
(state, updated_at)
(platform, repo, head_sha)
(platform, repo, pr_id)
```

This table is separate from completed review records. A candidate can exist
without a review. A review is created only when the candidate becomes ready.

## Platform Adapter Changes

Extend the platform protocol with readiness-oriented methods.

GitHub first:

```python
class PlatformAdapter(Protocol):
    async def fetch_pr(self, repo: str, pr_id: str) -> PlatformPR:
        ...

    async def fetch_pr_readiness(self, pr: PlatformPR) -> PRReadinessSnapshot:
        ...

    async def find_open_prs_by_head_sha(self, repo: str, head_sha: str) -> list[PlatformPR]:
        ...
```

Suggested snapshot model:

```python
@dataclass(frozen=True)
class CheckItem:
    name: str
    kind: Literal["check_run", "status"]
    status: Literal["queued", "in_progress", "completed", "unknown"]
    conclusion: str
    app: str = ""
    url: str = ""


@dataclass(frozen=True)
class PRReadinessSnapshot:
    pr: PlatformPR
    head_sha: str
    is_open: bool
    is_draft: bool
    has_conflicts: bool | None
    checks: tuple[CheckItem, ...]
    combined_state: Literal["success", "pending", "failure", "unknown"]
```

Implementation detail:

- Use REST check-runs endpoint for check runs.
- Use REST combined-status endpoint for classic statuses.
- Optionally use GraphQL `statusCheckRollup` later to unify checks and statuses
  in one request.
- Read artifacts with the existing actions artifact endpoint code.

## Webhook Handling Proposal

Replace "only pull_request matters" with a router that classifies GitHub event
types.

Events to subscribe to:

```text
pull_request
check_run
check_suite
status
workflow_run
```

`pull_request` handler:

```text
if action in opened/synchronize/reopened/ready_for_review/converted_to_draft:
  normalize PR
  upsert candidate
  evaluate_readiness(candidate)
```

`check_run` handler:

```text
if action in created/rerequested/completed:
  sha = check_run.head_sha
  candidates = find waiting candidates for repo + sha
  evaluate each
```

`check_suite` handler:

```text
if action in requested/rerequested/completed:
  sha = check_suite.head_sha
  candidates = find waiting candidates for repo + sha
  evaluate each
```

`status` handler:

```text
sha = payload.sha
candidates = find waiting candidates for repo + sha
evaluate each
```

`workflow_run` handler:

```text
if workflow_run.name == "archmap" and conclusion == "success":
  sha = workflow_run.head_sha
  candidates = find waiting candidates for repo + sha
  evaluate each
```

The evaluation step must fetch current state from GitHub. Do not trust webhook
payloads as the final source of truth.

## Review Queue Changes

Current queue dedupe uses `repo:pr:head_sha` as soon as a webhook arrives.

For readiness-gated reviews, split dedupe into:

```text
candidate_seen: durable table, can be updated many times
review_started: durable marker, set only when ready -> reviewing
active_review: in-memory task cancellation by repo:pr
```

Behavior:

- Multiple readiness events for the same SHA should be harmless.
- Only one event should win the transition from `ready` to `reviewing`.
- If a new SHA arrives, cancel active review for the old SHA if possible and mark
  old candidate as superseded.
- If Guardian restarts, `reviewing` candidates older than a threshold should be
  reconciled.

## UI and Status Behavior

Guardian should make waiting visible.

Commit status/check contexts:

```text
PR Guardian / readiness: pending - Waiting for repository checks
PR Guardian / readiness: pending - Waiting for Archmap artifact
PR Guardian / review: pending - Review in progress
PR Guardian / review: success/failure - Review complete
```

Avoid one ambiguous `pr-guardian` context if possible. Splitting readiness and
review makes the timeline understandable and avoids deadlock in readiness
evaluation by ignoring both Guardian contexts.

Dashboard:

- Add candidate rows before a review exists.
- Show state: waiting checks, waiting Archmap, ready, reviewing, reviewed,
  blocked, superseded.
- Show blocking checks/artifacts.
- Add "review now" admin escape hatch for time-sensitive PRs.

PR comments:

- Do not post a full review comment while waiting.
- Optional: post nothing until review completes.
- Optional: if waiting exceeds threshold, post a concise "Guardian is waiting
  for checks" comment, but avoid noise by default.

## Timeout and Failure Policy

Recommended defaults:

- `max_wait_minutes: 60`
- `quiet_period_seconds: 20`
- `archmap_max_wait_minutes: 20` after checks are green
- failed checks -> blocked, no full review
- readiness timeout -> blocked or review-with-warning depending on repo config

Timeout modes:

```yaml
review_readiness:
  on_checks_timeout: "block"      # block | review_with_warning
  on_archmap_timeout: "review_with_warning"
```

Rationale:

- If build/lint/security checks never finish, Guardian should not spend review
  budget by default.
- If Archmap is optional or broken, teams may prefer Guardian to proceed with a
  visible warning rather than stop all reviews.

## Security and Permissions

GitHub App permissions likely needed:

- Pull requests: read/write, for PR metadata and comments/reviews.
- Checks: read, for check-run and check-suite events/API.
- Commit statuses: read, for classic status contexts.
- Actions: read, for Archmap artifacts.
- Contents: read, if Guardian later needs repo config from target branch.

Webhook signature verification remains mandatory for public endpoints.

Do not accept an unauthenticated "ready" event from consumer repos as a primary
path. If a callback endpoint exists for pilots, it must use app auth or a signed
shared secret and still re-read GitHub state before starting review.

## Fork PRs

Fork PRs are the main edge case.

Known constraints:

- Some GitHub events do not map cleanly back to fork PRs through
  `pull_requests` arrays.
- Actions permissions and artifact availability differ for fork-origin PRs.
- A `workflow_run` payload may not include enough PR metadata to resolve the PR
  reliably.

Recommended handling:

- Always create the candidate from the `pull_request` event, because it contains
  the PR number and head SHA.
- Map later check/status/workflow events by `repo + head_sha` to existing
  candidates.
- If there are multiple open candidates with the same SHA, evaluate all.
- If Archmap artifact is unavailable for fork PRs, follow
  `on_archmap_timeout`.
- Never trust artifacts without checking embedded `commit`.

## Azure DevOps Notes

The same concept applies to ADO, but the event names and APIs differ.

ADO target design:

```text
pull request event
  -> candidate

build completion / policy evaluation event
  -> evaluate readiness

polling fallback
  -> list active PRs and policy status
```

ADO should use the same internal candidate state machine. Only the platform
adapter and webhook mapping differ.

## Implementation Plan

### Phase 1 - Model and evaluator

Add:

- `ReviewCandidate` persistence model and migration.
- Candidate storage helpers:
  - `upsert_review_candidate`
  - `mark_candidate_state`
  - `get_waiting_candidates_by_sha`
  - `try_mark_candidate_reviewing`
  - `mark_candidate_reviewed`
- `PRReadinessSnapshot` and `evaluate_readiness(snapshot, config, archmap_state)`.

Tests:

- pending check -> `waiting_checks`
- failed check -> `blocked`
- successful checks with missing required Archmap -> `waiting_archmap`
- successful checks with valid Archmap -> `ready`
- Guardian check contexts ignored
- stale SHA -> `superseded`

### Phase 2 - GitHub readiness adapter

Add GitHub methods:

- Fetch current PR by repo/pr number.
- Fetch check runs for head SHA.
- Fetch combined commit status for head SHA.
- Fetch Archmap artifact by head SHA, reusing current code.
- Map events by SHA to waiting candidates.

Tests:

- REST payload normalization for check runs and statuses.
- Combined state calculation.
- Artifact commit mismatch stays not-ready or warning based on config.

### Phase 3 - Webhook router

Update GitHub webhook handler:

- Keep signature validation.
- Accept `pull_request`, `check_run`, `check_suite`, `status`, `workflow_run`.
- PR events upsert candidates.
- Check/status/workflow events re-evaluate candidates.
- Remove immediate `run_review()` from PR webhook default path.

Tests:

- PR opened creates candidate, no review yet.
- Check completed green transitions candidate to review.
- Check completed for stale SHA does not start review.
- Workflow_run `archmap` success triggers re-evaluation.

### Phase 4 - Queue and orchestrator integration

Change enqueue boundary:

- Candidate events do not call `ReviewQueue.is_duplicate`.
- `try_mark_candidate_reviewing` is the durable dedupe.
- Only then call `review_queue.enqueue(run_review(...))`.
- On completion, mark candidate reviewed/failed.

Pass Archmap:

- Option A: leave current orchestrator fetch in place, because readiness
  guarantees artifact is there.
- Option B: cache raw artifact or parsed context on the candidate and pass it to
  `run_review()` to avoid a second download.

Recommendation: start with Option A for smaller changes. Add caching later if
artifact fetch cost matters.

### Phase 5 - Triage uses Archmap

Update `triage/classifier.py`:

- Hub file -> high risk, all agents.
- Very high Archmap risk branch file -> at least medium risk.
- Add reasons with file examples.

Tests:

- Hub file forces high risk and all agents.
- Branch file with high risk bumps to medium.
- Missing Archmap preserves existing behavior.

### Phase 6 - Reconciler

Add periodic readiness reconciliation:

- Every 1 to 5 minutes, load waiting candidates updated before quiet period.
- Re-fetch PR/check/artifact state.
- Transition as needed.
- Mark timed-out candidates according to config.

Tests:

- Missed webhook recovered by reconciler.
- Timeout policy works.
- Restart-safe reviewing candidate handling.

### Phase 7 - UI and operations

Add dashboard visibility:

- Candidate state.
- Blocking checks.
- Missing Archmap status.
- Manual "review now" action with audit trail.

Ops:

- Metrics:
  - candidates waiting by state
  - readiness wait duration
  - reviews skipped because checks failed
  - Archmap missing/stale count
  - webhook events processed by type
  - reconciler recoveries
- Logs:
  - candidate transition
  - readiness decision reason
  - stale event ignored
  - artifact validation failure

## Open Questions

1. Should Guardian wait for all checks, or only branch-protection-required
   checks?

   Recommendation: default to all non-ignored checks until branch protection
   integration is implemented. Add `ignored_contexts` for noisy optional tools.

2. Should Guardian review if checks fail?

   Recommendation: no by default. Failed checks often make review noisy and
   stale. Allow admin override or repo config.

3. Should Archmap be required for all repos?

   Recommendation: require only when an Archmap workflow/artifact convention is
   detected or repo config opts in. Otherwise proceed without it.

4. Should PR Guardian create a pending status immediately?

   Recommendation: yes, but split readiness and review contexts if possible.

5. How should CodeRabbit/Copilot readiness be treated?

   Recommendation: only wait for them if they publish GitHub-visible check runs
   or statuses. If they only comment/review, treat them as external reviewers,
   not readiness gates.

6. What happens when checks are added after Guardian starts review?

   Recommendation: use a quiet period before starting review. If new checks
   appear after start, do not cancel unless they fail before final decision and
   Guardian has not posted side effects yet.

## Suggested Default Configuration

```yaml
review_readiness:
  enabled: true
  require_checks_success: true
  require_archmap_if_workflow_present: true
  wait_for_draft: true
  wait_for_mergeable: false
  quiet_period_seconds: 20
  max_wait_minutes: 60
  archmap_max_wait_minutes: 20
  on_checks_timeout: block
  on_archmap_timeout: review_with_warning
  ignored_contexts:
    - pr-guardian
    - PR Guardian
    - PR Guardian / readiness
    - PR Guardian / review
```

## Acceptance Criteria

- PR webhook for a new PR creates a candidate but does not run agents while
  checks are pending.
- Successful check/status completion starts review exactly once for the current
  head SHA.
- Failed check/status blocks review and records a visible reason.
- New PR commit supersedes the previous candidate and prevents stale review.
- Archmap artifact is loaded before triage when present.
- Archmap hub files can increase risk tier and agent effort.
- Missing Archmap after timeout follows repo config.
- Guardian never waits on its own status/check context.
- A reconciler can start a review even if the original check webhook was missed.
- Fork PRs are safe: artifact commit is verified and stale/missing data does not
  silently affect decisions.

## Source Links

- GitHub webhook events and payloads: https://docs.github.com/en/webhooks/webhook-events-and-payloads
- GitHub `workflow_run` event: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run
- GitHub check-runs REST API: https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference
- GitHub commit statuses REST API: https://docs.github.com/en/rest/commits/statuses#get-the-combined-status-for-a-specific-reference
- GitHub GraphQL `statusCheckRollup`: https://docs.github.com/en/graphql/reference/objects#statuscheckrollup
