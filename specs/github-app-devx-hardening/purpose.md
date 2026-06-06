# GitHub App DevX Hardening

## Problem

PR Guardian has the core review, readiness, inline comment, Profile, and
Connection pieces, but the GitHub integration is still shaped around PATs and
environment tokens. That makes the product harder to install, harder to test as
a real app, and less like the hosted review assistants developers already know.

The current developer experience also leaves important platform outcomes spread
across several places: merge blocking depends on external branch protection,
re-review commands require the older `@pr-guardian re-review` phrase, platform
approval is easy to confuse with Guardian clearance, and there is no deterministic
end-to-end harness that lets an agent validate the full GitHub flow by itself.

## Outcome

GitHub support is GitHub App-first and self-validating: Guardian installs as an
App, stores App credentials in Connections, enforces `guardian/review` as a
required merge check, posts inline findings plus one sticky guidance comment,
handles `@guardian` ChatOps commands, and ships an opt-in deterministic sandbox
E2E harness for `esbenwiberg/pr-guardian-e2e`.

## Success Signal

On `esbenwiberg/pr-guardian-e2e`, a local Guardian instance authenticated as a
GitHub App can create or update a linked repo, enforce the `guardian/review`
required check, process a signed webhook replay for a real PR, post inline
comments, maintain the sticky guidance comment with a Guardian review deeplink,
react with `eyes` to `@guardian`, queue a re-review, and formally approve only
when the resolved Profile enables platform approval.

## Users

- Guardian operators who install and manage GitHub App Connections.
- PR authors who need clear feedback inside GitHub and a simple re-review path.
- Reviewers who rely on inline findings and a required Guardian status before
  merge.
- Coding agents that need to run a reliable GitHub App E2E check while
  developing Guardian.

## Non-goals

- No ADO auth redesign. ADO support stays intact and PAT-shaped for this
  feature.
- No GitHub PAT or `GITHUB_TOKEN` fallback for Guardian runtime paths.
- No full dashboard redesign beyond GitHub App setup, repo-link, merge-gate,
  review-detail, and guidance copy polish.
- No automatic merge. Guardian may approve when configured, but authors still
  merge.
- No live webhook requirement for E2E. Signed replay is required; live webhook
  delivery is optional when `GUARDIAN_PUBLIC_URL` is configured.
- No model-quality E2E dependency. The default E2E uses deterministic fake LLM
  output. Real LLM smoke can be added as an explicit optional mode later.
- No destructive repo-setting changes for normal linked repos. Guardian edits
  branch protection/rulesets additively. The sandbox repo is the only place the
  E2E harness may reset protection freely.

## Glossary

- **GitHub App Connection** - A Guardian Connection for GitHub containing a
  GitHub App ID, encrypted private key, discovered installation metadata, health
  state, and sync/merge-gate state.
- **Installation token** - A short-lived token minted from a GitHub App JWT for
  one installation. Guardian uses it for GitHub API calls.
- **Guardian review check** - The `guardian/review` commit status/check context
  that blocks merge until Guardian finishes green.
- **Merge gate** - The repository-side branch protection or ruleset requirement
  that makes `guardian/review` required before merge.
- **Sticky guidance comment** - One top-level PR conversation comment owned by
  Guardian, identified by a hidden marker, created early and updated through the
  PR lifetime with the latest state, review deeplink, and re-review instruction.
- **Inline finding comment** - A GitHub pull request review comment anchored to
  a changed file and line.
- **ChatOps command** - A PR conversation comment that mentions Guardian and
  requests first review or re-review.
- **Sandbox actor** - The `gh` authenticated user or `GH_TOKEN` used by the E2E
  script to create branches, PRs, comments, and sandbox branch protection.
- **Guardian App actor** - The GitHub App installation identity used by the
  Guardian server under test.

## Reversibility

This feature introduces append-only migrations and removes GitHub PAT fallback
from runtime code, so rollback requires operational preparation:

- Existing historical review and scan snapshots remain readable because they
  already store redacted Connection snapshots.
- Live GitHub repo links must be converted to GitHub App Connections before the
  PAT fallback is removed.
- If a deployment rolls back, operators must restore code that understands the
  previous GitHub Connection token shape or keep a database backup from before
  the migration.
- Branch protection edits are additive for normal repos, so rollback removes
  Guardian behavior by disabling repo links or manually removing only the
  `guardian/review` requirement.
