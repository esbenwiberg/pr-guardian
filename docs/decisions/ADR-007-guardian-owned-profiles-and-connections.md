# ADR-007: Guardian-owned Profiles and Connections replace review.yml

## Status

Accepted - 2026-05-31. Planned by
`specs/readiness-gated-profiles/`; not implemented yet.

## Context

PR Guardian originally loaded review policy from `review.yml` in the target
repository. That was convenient for early development, but it makes opt-in
harder for an organization with hundreds of repositories where only a subset
should use Guardian automatic review.

Repo-local config also couples product rollout to source changes in every
consumer repo. Changing defaults, pruning old fields, or adding settings
requires either broad pull requests or compatibility code in Guardian. The
feature plan instead needs exact repo opt-in, centrally managed credentials,
web CRUD, audit history, and immediate changes for waiting candidates.

## Decision

Guardian-owned Profiles, Connections, and repo links become the source of
review and scan policy.

- A Profile is reusable policy for review, repo review, and scan behavior.
- A Connection is a reusable outbound platform credential.
- A repo link ties one exact repository to one Profile and one Connection.
- Auto-review opt-in lives on the repo link.
- The default/noop Profile is used for unlinked manual reviews, repo reviews,
  and scans.
- Runtime `review.yml` loading is removed for product paths.
- CLI/config documentation is updated so local dry-run uses the default/noop
  Profile instead of target-repo config.

Profiles include active product policy only. They do not contain LLM provider
settings, API keys, model/runtime knobs, or dormant old fields.

## Consequences

**Easier:** Operators can opt in a small subset of repos without committing a
file to each repo. Future policy changes happen in Guardian and can be audited.

**Harder:** Removing repo-file config is a breaking operational change. The
migration has to preserve enough historical review/scan snapshots that old
rows remain readable after live Profiles or Connections change.

**Committed to:** Profile/Connection/repo-link management is a first-class
Guardian feature, including web CRUD, audit history, health validation, and
permission checks.

## Alternatives Rejected

- **Keep `review.yml` as the opt-in marker.** Rejected because it still requires
  checked-in config files across many repos and keeps policy evolution tied to
  source changes.
- **Support both repo config and Guardian Profiles in v1.** Rejected because it
  doubles precedence rules and creates ambiguous behavior for scans, manual
  reviews, and waiting candidates.
- **Use org-wide opt-in with exclusions.** Rejected because the desired rollout
  is a small explicit set of repositories, not every repo in an org.
- **Store credentials on Profiles.** Rejected because Profiles should be
  reusable policy. Credentials belong to named Connections referenced by repo
  links and manual execution resolution.
