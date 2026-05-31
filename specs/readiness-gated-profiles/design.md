# Design - Readiness-Gated Profiles and Repo Opt-In

## Blast Radius

```text
alembic/versions/<new>.py                       - profiles, connections, repo links, candidates, audit, provenance
src/pr_guardian/persistence/models.py           - ORM rows and relationships
src/pr_guardian/persistence/storage.py          - CRUD, resolver, candidate, audit, sync provenance helpers
src/pr_guardian/persistence/crypto.py           - token encryption helpers if not already centralized
src/pr_guardian/auth/identity.py                - can_manage_profiles identity field
src/pr_guardian/auth/dependencies.py            - signed-in/admin/profile-manager dependencies
src/pr_guardian/api/profiles.py                 - Profiles, Connections, repo links, managers, audit API
src/pr_guardian/api/review.py                   - manual review/repo review resolver integration
src/pr_guardian/api/agent_api.py                - API-key linked-repo review restrictions
src/pr_guardian/api/scans.py                    - scan resolver and provenance
src/pr_guardian/api/pr_dashboard_api.py         - pull-request browse API over synced PRs
src/pr_guardian/api/reviews_queue.py            - merged reviews/candidate queue API
src/pr_guardian/api/dashboard_page.py           - /profiles, /pull-requests, redirects
src/pr_guardian/api/webhooks.py                 - strict webhook security and candidate routing
src/pr_guardian/core/orchestrator.py            - Profile snapshots, side-effect gating, stale-SHA guard
src/pr_guardian/core/pr_sync.py                 - sync over healthy sync-enabled Connections
src/pr_guardian/core/readiness.py               - new readiness evaluator and state machine
src/pr_guardian/core/readiness_reconciler.py    - new periodic recovery loop
src/pr_guardian/core/repo_review.py             - synthetic PR review path naming/provenance
src/pr_guardian/core/recent_changes.py          - scan Profile/Connection use
src/pr_guardian/core/maintenance.py             - scan Profile/Connection use
src/pr_guardian/config/schema.py                - active Profile schema, pruned dormant fields
src/pr_guardian/config/loader.py                - remove repo review.yml runtime loading
src/pr_guardian/config/defaults.yml             - default/noop Profile seed shape
src/pr_guardian/platform/protocol.py            - readiness/status/artifact/fork/draft methods
src/pr_guardian/platform/github.py              - checks/status readiness, fork/draft, strict status writes
src/pr_guardian/platform/ado.py                 - policy/status readiness, fork/draft, Archmap artifact lookup
src/pr_guardian/dashboard/profiles.html         - new management screen
src/pr_guardian/dashboard/pull_requests.html    - new broad PR browse screen
src/pr_guardian/dashboard/reviews_queue.html    - readiness rows and panel
src/pr_guardian/dashboard/static/sidebar.js     - Reviews, Pull Requests, Profiles visibility
src/pr_guardian/dashboard/static/command-palette.js - Profiles/Pull Requests entries if present
scripts/dev_seed.py                             - seed default Profile, Connections, candidates, PR browse data
docs/cli.md                                     - remove/replace validate --config guidance if present
```

## Brief Ordering

```text
Gate 1:
  01 Add Profile, Connection, repo-link, and readiness data model

Gate 2, parallel after 01:
  02 Add Profile and Connection management APIs
  03 Replace review.yml config loading with Profile resolution

Gate 3, parallel after 02/03:
  04 Resolve manual reviews, repo reviews, and scans through Profiles
  05 Move broad PR browse sync to Connections and /pull-requests

Gate 4, sequential backend after 01/03:
  06 Add readiness candidate engine and platform readiness adapters
  07 Gate platform side effects and candidate/review transitions

Gate 5, parallel user surfaces:
  08 Build /profiles management UI
  09 Build /reviews readiness panel and /pull-requests browse UI
```

## Seams

### Persistence -> Product APIs

Brief 01 owns durable tables and storage helpers. Briefs 02, 04, 05, 06, and 07
consume those helpers without redefining schema. Cross-boundary rows must store
IDs plus compact snapshots so later UI/API work does not need to chase mutable
Profiles or Connections for historical reviews.

### Profile Resolver -> Review and Scan Execution

Brief 03 owns the resolver contract. Brief 04 wires every manual PR review,
repo review, re-review, API-key trigger, and scan through it. Automatic
readiness evaluation also re-reads the current repo link/Profile/Connection on
each evaluation until a review starts.

### Platform Readiness -> Candidate Engine

Brief 06 extends `PlatformAdapter` with readiness primitives and owns the
candidate evaluator. The evaluator decides candidate state; platform adapters
only report facts such as draft/fork/check/status/Archmap state.

### Candidate Engine -> Orchestrator

Brief 07 owns the durable handoff from readiness candidate to review. Automatic
review can start only after a compare-and-set transition to `reviewing`, which
prevents duplicate starts from simultaneous webhook and reconciler events.

### Side Effects -> Platform Adapters

The orchestrator decides which side effects are allowed by the resolved Profile.
Adapters perform the writes. `_post_results()` should be split into readable
steps for statuses, comments, labels/reviewers, and platform verdicts.

### Broad Sync -> Pull Requests UI

Brief 05 moves browse-only PR discovery to sync-enabled Connections and persists
Connection provenance. Brief 09 renders those rows in `/pull-requests`, while
`/reviews` only shows review rows and opted-in readiness candidates.

### Identity -> Management Permissions

Profile management is not full admin. Brief 02 adds `can_manage_profiles`;
briefs 08 and 09 use it for nav and UI visibility. `/settings`, API keys, LLM
settings, and admin management remain admin-only.

## Contracts

### Profile

Profile data is Guardian-owned and stored in the database. The v1 Profile schema
contains active product policy only:

- `repo_risk_class`
- `human_review.reviewer_group` if active in the current product
- `thresholds.auto_approve_max_score`
- `thresholds.hard_block_score`
- `weights`
- `certainty_validation`
- `guardian_clearance` / `platform_approval_enabled`
- `path_risk`
- `file_roles`
- `security_surface`
- `trust_tiers`
- `severity_floor`
- `validator`
- `recent_changes`
- `maintenance`
- `inline_comments`
- `readiness.quiet_period_seconds`
- `readiness.max_wait_minutes`
- `readiness.archmap_max_wait_minutes`
- `readiness.ignored_statuses`
- `readiness.ignored_checks`
- `readiness.archmap_expected`
- side-effect switches for comments, labels, reviewer requests, formal approve,
  formal request-changes, and scan issue/work-item creation

Profiles exclude:

- `llm.*`
- API keys, provider/model/temperature/max tokens/timeouts
- executor/runtime knobs such as agent timeout and max context tokens
- dormant old config fields: `intent_verification`, `privacy`, `feedback`,
  `test_quality`, `triage.agent_context_thresholds`,
  `thresholds.human_review_min_score`

The default/noop Profile is system-owned and non-deletable but editable. It is
used for unlinked manual reviews, repo reviews, and scans.

### Connection

```text
Connection:
  id
  name
  platform: github | ado
  org_url: nullable for GitHub, required for ADO
  token_secret_ref or encrypted_token
  token_prefix
  health_status: unknown | healthy | unhealthy
  health_message
  health_checked_at
  sync_enabled
  archived_at
  created_by
  updated_by
  created_at
  updated_at
```

GitHub Connections are name plus token. ADO Connections are name plus org URL
plus PAT. Existing GitHub PAT rows migrate into Connections, and old PAT APIs
are removed.

Environment variables such as `GITHUB_TOKEN` and `ADO_PAT` may be shown as
explicit "import available" setup paths, but must not be silently persisted.

### Repo Link

```text
RepoLink:
  id
  platform: github | ado
  org_url
  project
  repo_owner
  repo_name
  repo_url
  canonical_repo_key
  profile_id
  connection_id
  auto_review_enabled
  paused
  created_by
  updated_by
  created_at
  updated_at
```

GitHub identity is the exact repository. ADO identity is
`platform + org_url + project + repo_name`; `project/repo` is only input
shorthand. One active Profile applies to each repo link.

### Candidate State

```text
Candidate state:
  waiting
  blocked
  reviewing
  reviewed
  superseded
  error
```

Readiness details live in reason and snapshot fields rather than expanding the
state enum. Normal user-actionable `waiting` and `blocked` candidates appear in
`/reviews`. `superseded` and technical `error` rows are hidden by default and
available only through debug/admin filters.

Candidate transitions store:

- from state
- to state
- timestamp
- source/actor
- reason
- compact readiness snapshot

Candidate retention is indefinite.

### Readiness Evaluation

Automatic reviews are opt-in by exact repo link. Manual dashboard/API/paste
reviews bypass readiness and remain immediate.

Readiness rules:

- Draft PRs wait and are hidden from `/reviews`; `guardian/readiness` stays
  pending. Draft time does not count toward max wait.
- Mergeability and conflicts are ignored.
- Quiet period defaults to 10 seconds.
- Check/status max wait defaults to 30 minutes.
- Archmap max wait defaults to 10 minutes.
- Checks means all non-ignored visible automated checks/statuses, not only
  branch-protection checks.
- If there are zero visible automated checks and the readiness query succeeds,
  review can start immediately.
- Permission/API errors are candidate errors, not "no checks".
- Failed checks/statuses block automatic review for that SHA. This is
  recoverable: if the same head SHA later passes, Guardian can start review.
- Timeout also blocks automatic review with reason `checks_timeout`; the
  reconciler keeps checking and can recover for the same head SHA.
- Archmap is soft. If expected, wait; on timeout, review with warning.
- Fork-origin PRs are automatic-blocked with `fork_requires_manual_start`;
  manual start is allowed, but Guardian formal approval never runs on forks.
- New commit supersedes the old candidate. If an automatic review is already
  running, it may finish internally, but stale-SHA guard prevents platform side
  effects.
- Close or merge moves active candidates to `superseded` with
  `pr_closed`/`pr_merged`.

Candidate evaluation always re-reads current repo link, Profile, and Connection
until a review starts. Profile/link changes trigger re-evaluation transitions.
Pausing a repo link or disabling auto-review immediately blocks waiting
candidates; in-flight reviews continue with their start snapshot.

### Platform Statuses

Guardian posts two distinct statuses:

- `guardian/readiness` - pending when a candidate is created or updated,
  success when automatic review starts or an authorized override marks
  readiness success, failure when blocked or error.
- `guardian/review` - actual review execution/result status.

Manual `Start Review Now` bypasses readiness and does not mark readiness
success. Admin/Profile Manager `Override Readiness & Start Review` requires a
reason and marks readiness success as manual override.

### Profile and Connection Resolver

Signed-in dashboard manual resolution is intentionally quiet:

1. Linked repo: use repo link Connection and Profile.
2. `/pull-requests` row: use the Connection that synced/saw the PR.
3. Same user/platform: use last successful active Connection if available.
4. Exactly one active Connection for platform: use it.
5. Otherwise show a picker.

If the inferred Connection cannot hydrate the PR, fail with an actionable
picker/message rather than trying every other Connection.

API keys can trigger reviews only for linked repos and use the linked repo
Connection/Profile. API keys cannot administer Profiles/Connections and cannot
override readiness or finalize human verdicts.

### Review, Repo Review, Re-Review, and Scan Provenance

Review and scan rows store:

- `profile_id`
- `profile_snapshot`
- `connection_id`
- `connection_snapshot`
- optional `repo_link_id`
- optional `candidate_id`
- source, such as automatic, manual, manual_bypass, override, api, scan

Repo review stays distinct from scans. It is a small-repo synthetic PR review,
shows under Reviews, uses the PR review pipeline, and suppresses platform side
effects.

Scans use Profiles but not a separate scan-baseline product concept. Linked
scans use the linked Profile and Connection. Unlinked scans use default/noop
Profile plus selected or inferred Connection. Scan issue/work-item creation is
controlled by a Profile side-effect switch and defaults off.

### Side Effects and Approval

`Decision.AUTO_APPROVE` remains stored/API decision value, but means Guardian
clearance unless the resolved Profile enables formal platform approval. UI copy
should say `Cleared` or `Guardian cleared` when no formal approval was posted.

Statuses are always on. Comments respect comment mode, including manual
override for a run. Labels, reviewer requests, formal approval, and formal
request-changes are off unless enabled by Profile side-effect switches.

Human finalization is signed-in-only and can still approve/request changes as a
human action. Finalization uses the review's stored Connection ID with the
current token and fails clearly if archived or inaccessible.

### Webhook Security

Public GitHub and ADO webhook requests fail when the configured shared secret is
missing or invalid. Explicit dev/test bypass is allowed for local tests only.

Webhooks plus reconciler are both required. Webhooks create/update candidates;
the reconciler recovers missed events, delayed check transitions, and deploy
gaps.

### Broad PR Browse and Exclusions

Healthy active Connections with `sync_enabled=true` participate in broad PR
sync. Repo links may use `sync_enabled=false` Connections. Manual reviews and
scans may use sync-disabled Connections.

`sync_sources` and `synced_prs` store `connection_id` provenance. Existing
exclusions remain browse-only: they can hide rows from `/pull-requests`, but
they must not block explicit repo links or automatic readiness.

## UX Flows

### `/reviews`

`/reviews` contains normal review rows plus opted-in readiness candidates.
Completed review rows navigate to `/reviews/{review_id}`. Candidate/open PR rows
open the right-side readiness panel. Draft PR candidates are hidden from this
page.

```text
/reviews

[ Reviews                                      17 items ] [Identity]

[ Paste PR URL or repo ] [Connection v] [Comment mode v] [Review]

[ All ] [ Waiting ] [ Blocked ] [ Needs review ] [ Mine ] [ Scans ]
                                            [repo v] [author v] [risk v]

queue/list                                     readiness panel
PR  GH  #124  feat/auth                       feat/auth
repo/api - alice - updated 3m ago             GH - repo/api - #124
WAITING CHECKS - 4/7 checks - archmap wait    Readiness
Checks: 4 passed, 2 pending                   Archmap: waiting, 6m left
PR  ADO #88  fix/billing                      Quiet period: satisfied
repo/billing - bob - updated 14m ago          Actions
BLOCKED - checks timeout                      [Start Review Now] [Pause]
PR  GH #118 reviewed row
HUMAN REVIEW - 3 high - 12 files
```

### `/pull-requests`

`/pull-requests` is the broad synced PR browse surface. It replaces the old
`/pr-dashboard` and `/browse-pr` concepts. Non-opted synced PRs belong here,
not in `/reviews`.

```text
/pull-requests

[ Pull Requests                              143 open PRs ] [Identity] [Sync]

[ Search PRs... ] [platform v] [repo v] [author v] [status v]

[ Mine ] [ Ready-ish ] [ Needs attention ] [ Stale ] [ All open ]

open PR list                                   PR panel
GH #124 feat/auth                              feat/auth
repo/api - alice - updated 3m ago             GH - repo/api - #124
CI pending - Guardian not run                 Status
ADO #88 fix/billing                           CI: pending
repo/billing - bob - updated 14m ago          Guardian: not run
CI passing - unlinked repo                    Linked repo: no
GH #118 chore/docs                            Actions
repo/docs - you - updated 2d ago              [Start Review Now]
stale - reviewed before                       [Open in GitHub/ADO]
                                              [Hide repo from browse]
```

### `/profiles`

`/profiles` is a top-level management page, not a `/settings` subpage. It is
visible to admins and Profile Managers.

```text
/profiles

[ Profiles                                      ]  [New Profile] [Link Repository]

[ Profiles ] [ Repositories ] [ Connections ] [ Audit ] [ Managers* ]

left list            main editor/list              inspector
Default / noop       Profile: Default / noop       Usage
Standard Service     Name, description             0 linked repos
High Risk Service    Review policy                 Side effects
                     Readiness                     Recent audit

Repositories tab:
platform | org/project | repo | profile | connection | auto-review | paused | last readiness

Connections tab:
name | platform | org/url | token prefix | sync_enabled | linked repos | updated | actions

Audit tab:
time | actor | action | target | before/after

Managers tab, admin-only:
email | added by | date | remove
```

## Reference Reading

- `docs/decisions/ADR-007-guardian-owned-profiles-and-connections.md` -
  Profiles, Connections, and repo links replace runtime `review.yml`.
- `docs/decisions/ADR-008-readiness-candidates-are-durable-state-machine-records.md` -
  automatic review starts only through durable readiness candidates.
- `docs/decisions/ADR-009-guardian-clearance-is-separate-from-platform-approval.md` -
  `Decision.AUTO_APPROVE` means Guardian clearance unless platform approval is
  explicitly enabled.
- `CLAUDE.md` - commands, repo layout, layer boundaries, migration rule.
- `docs/plan/12-readiness-gated-reviews.md` - original readiness design and
  trigger trade-offs.
- `docs/decisions/ADR-001-inline-comment-mode-tristate.md` - comment mode as a
  tri-state.
- `docs/decisions/ADR-002-sticky-trigger-split.md` - closed durable trigger
  semantics.
- `docs/decisions/ADR-003-finding-lifecycle-state-machine.md` - state-machine
  precedent.
- `docs/decisions/ADR-004-fix-by-inference.md` - evidence and inference
  boundary.
- `docs/decisions/ADR-005-final-auto-approval-gate.md` - proposed/not
  implemented; superseded by ADR-009 for approval semantics.
- `docs/decisions/ADR-006-split-verifier-agent-identity.md` - config migration
  precedent with legacy identity.
- `src/pr_guardian/api/webhooks.py` - current immediate webhook-to-review path.
- `src/pr_guardian/config/loader.py` - current `review.yml` loader.
- `src/pr_guardian/persistence/models.py` - current review, scan, PAT, sync,
  admin, API key, and global config tables.
- `src/pr_guardian/dashboard/reviews_queue.html` - current Reviews queue and
  repo-review copy.
- `src/pr_guardian/platform/github.py` - existing GitHub status/comment and
  Archmap artifact patterns.
- `src/pr_guardian/platform/ado.py` - existing ADO status/comment patterns and
  missing Archmap artifact lookup.

## Decisions

- ADR-007: Guardian-owned Profiles and Connections replace `review.yml`.
- ADR-008: Readiness candidates are durable state machine records.
- ADR-009: Guardian clearance is separate from platform approval.
