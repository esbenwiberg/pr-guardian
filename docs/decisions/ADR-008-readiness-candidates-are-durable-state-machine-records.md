# ADR-008: Readiness candidates are durable state machine records

## Status

Accepted - 2026-05-31. Planned by
`specs/readiness-gated-profiles/`; not implemented yet.

## Context

PR webhooks arrive before CI checks, security tools, and optional Archmap
artifacts complete. Starting a full review directly from a `pull_request`
webhook races those systems and can waste review effort on PRs that are not
ready.

An in-memory delayed waiter would improve timing but would lose state across
deploys and would be hard to audit. Webhooks can also be missed or delivered
out of order, so readiness needs both event triggers and periodic recovery.

## Decision

Automatic reviews start only through durable readiness candidates.

A candidate represents one linked repo PR head SHA. It stores state, reason,
readiness snapshot, deadlines, source, linked Profile/Connection context, and
transition history. Webhooks create or update candidates; a reconciler
re-evaluates candidates to recover missed events and delayed checks. A review
can start only after a durable transition to `reviewing`.

Candidate states are:

- `waiting`
- `blocked`
- `reviewing`
- `reviewed`
- `superseded`
- `error`

Readiness details belong in reason and snapshot fields, not in additional state
names.

## Consequences

**Easier:** Guardian can recover after missed webhooks and deployments, explain
why a PR is waiting or blocked, and prevent duplicate automatic reviews for the
same head SHA.

**Harder:** The system needs more database schema, transition logging, and a
reconciler. UI must distinguish waiting/blocked candidates from completed
review rows.

**Committed to:** Candidate retention is indefinite, and every transition stores
actor/source, time, reason, and a compact snapshot suitable for audit.

## Alternatives Rejected

- **Keep immediate webhook review.** Rejected because it races checks and
  Archmap.
- **Use only polling with no durable candidate.** Rejected because it is harder
  to audit and recover across deploys.
- **Require consumer repos to call Guardian when ready.** Rejected for v1
  because it creates setup work in every participating repo and conflicts with
  the goal that Guardian observes ordinary platform state.
- **Represent every readiness reason as a separate state.** Rejected because it
  makes the state machine brittle. Reasons and snapshots provide detail without
  multiplying state transitions.
