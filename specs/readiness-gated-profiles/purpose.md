# Readiness-Gated Profiles and Repo Opt-In

## Problem

PR Guardian currently starts reviews too early and relies on repo-local
`review.yml` policy. In practice, PR webhooks arrive before CI checks, security
checks, and Archmap artifacts settle, so Guardian can spend review effort on
PRs that are not ready and can miss pre-review context that should influence
triage.

The repo-local config model also does not fit an organization with hundreds of
repositories where only a small subset should opt into automatic Guardian
review. Teams need to opt in one exact repository at a time, manage credentials
centrally, and change review/scan behavior without requiring a config file to
be checked into every repo.

## Outcome

Profile Managers can create a Connection, Profile, and exact repo link in
Guardian; new PRs on linked repos become readiness candidates, wait for
automated readiness, and start automatic review only when ready, while manual
reviews, repo reviews, and scans use consistent Profile and Connection
provenance.

## Success Signal

The feature is working when all of these are true:

1. A Profile Manager can create a healthy Connection, create/edit a Profile,
   link an exact repo, and enable auto-review for that repo.
2. A new PR on an opted-in repo appears as a waiting or blocked readiness row
   in `/reviews`, and passing automated checks for the same head SHA starts one
   Guardian review.
3. Broad synced PRs from non-opted repositories appear in `/pull-requests` and
   do not flood `/reviews`.
4. Manual PR reviews, repo reviews, re-reviews, and scans resolve and persist
   Profile and Connection provenance.

The executable proof is split across browser and integration facts in briefs
04, 05, 08, and 09.

## Users

- PR authors and reviewers who see Guardian status at the right time instead of
  before CI has settled.
- Profile Managers who manage Profiles, Connections, exact repo links, and
  readiness behavior.
- Admins who retain global control over settings, API keys, LLM config, and
  Profile Manager membership.
- Signed-in users who manually start reviews, repo reviews, and scans.
- API clients that trigger reviews for already-linked repos.

## Non-Goals

- No organization-wide opt-in or wildcard repo matching in v1.
- No repo-local `review.yml` runtime support after this feature lands.
- No YAML or JSON editor for Profile management in the web UI.
- No LLM provider/model/API-key/temperature/max-token/timeouts inside Profiles.
- No executor runtime knobs in the Profile UI.
- No automatic merge. Guardian may approve or request changes only when
  explicitly configured, but authors still merge.
- No consumer-repo CI job that calls Guardian when ready.
- No new platform beyond GitHub and Azure DevOps.
- No treatment of readiness as a quality verdict. Readiness controls when
  Guardian starts; Guardian's review decision still comes from the review
  pipeline.

## Glossary

- **Profile** - Guardian-owned reusable review and scan policy. It contains
  active product settings such as thresholds, weights, readiness, side-effect
  switches, path risk, file roles, security surface, trust tiers, validator,
  recent changes, maintenance, and inline comments.
- **Default/noop Profile** - system-owned, non-deletable Profile used for
  unlinked manual reviews, repo reviews, and scans. It defaults to status-only
  behavior, no auto-review, no platform approval, and scan issue creation off.
- **Connection** - reusable named outbound platform credential. GitHub
  Connections store a token; ADO Connections store org URL plus PAT.
- **Repo link** - exact repository opt-in record tying platform repo identity to
  one Profile and one Connection. Auto-review and paused state live here.
- **Profile Manager** - signed-in user who can manage Profiles, Connections,
  repo links, and the `/profiles` page without being a full admin.
- **Readiness candidate** - durable record for one PR head SHA on a linked repo.
  It stores state, readiness snapshot, reason, and transition history.
- **Readiness** - automated pre-review gate: quiet period, draft status, visible
  automated checks/statuses, optional Archmap wait, fork policy, repo link state,
  and permission/config health.
- **Archmap** - optional architecture artifact named `archmap-<sha>`. Guardian
  waits briefly when expected, then proceeds with a warning on timeout.
- **Manual bypass** - any signed-in user starts a review immediately without
  marking readiness success.
- **Readiness override** - Admin/Profile Manager action that records a reason,
  marks readiness success as manual override, and starts review.
- **Guardian clearance** - internal `Decision.AUTO_APPROVE` result meaning
  Guardian found no blocking concern.
- **Platform approval** - formal GitHub/ADO approval. It only happens when the
  resolved Profile explicitly enables it.
- **Repo review** - small/bounded repository snapshot reviewed through the PR
  review pipeline as a synthetic PR. It is not a scan and never posts platform
  side effects.
- **Scan** - recent-changes or maintenance analysis with scan-specific focus,
  issue/work-item side effects, and persisted scan provenance.
- **Broad PR sync** - background discovery of open PRs from sync-enabled
  Connections for browse purposes.
- **Exclusion** - browse-only filter that hides repos from `/pull-requests`.
  Exclusions do not block explicit repo links or readiness candidates.

## Reversibility

This feature introduces durable schema, migration, API, and UI changes. The
rollback strategy is data-preserving:

- Connections, Profiles, repo links, candidates, transitions, and audit rows
  are soft-archived or disabled rather than physically deleted in normal
  product flows.
- Reviews and scans store Profile and Connection snapshots so old rows remain
  readable if the live Profile or Connection changes later.
- Repo links can be paused or auto-review-disabled to stop new automatic
  candidates without interrupting in-flight reviews.
- Removing `review.yml` runtime support is intentionally hard to reverse. The
  fallback for unlinked work is the editable default/noop Profile, not a hidden
  file-based compatibility path.
