# ADR-009: Guardian clearance is separate from platform approval

## Status

Accepted - 2026-05-31. Planned by
`specs/readiness-gated-profiles/`; not implemented yet. Supersedes the approval
semantics proposed in ADR-005 for this feature.

## Context

The existing decision enum includes `Decision.AUTO_APPROVE`, and earlier plans
discussed final auto-approval gates. For the readiness/Profile rollout, formal
platform approval must be safer by default: a Profile may want Guardian to mark
a PR as cleared without actually approving it in GitHub or Azure DevOps.

This is especially important because auto-review opt-in and automated platform
approval are separate product decisions. A team may want Guardian comments,
statuses, and dashboard findings long before it is comfortable with formal
approval side effects.

## Decision

`Decision.AUTO_APPROVE` remains the stored/API decision value, but product copy
and platform side effects interpret it as Guardian clearance unless the resolved
Profile explicitly enables formal platform approval.

- UI copy says `Cleared` or `Guardian cleared` when no formal approval was
  posted.
- `approve_pr()` runs only when the Profile's platform approval switch is on.
- Formal request-changes is also controlled by Profile side-effect switches.
- Statuses remain always-on.
- Comments, labels, reviewers, formal approval, and formal request-changes are
  separate side-effect gates.
- Human finalization remains a separate signed-in action path and may approve
  or request changes as a human verdict.

## Consequences

**Easier:** The default behavior is safer for newly opted-in repos. Teams can
adopt Guardian review without accidentally granting formal platform approvals.

**Harder:** API, UI, comments, and audit logs must be precise about the
difference between Guardian clearance and platform approval. `_post_results()`
must be split so approval is not bundled with other side effects.

**Committed to:** Auto-review opt-in and automated platform approval are
separate switches. Future code must not infer formal platform approval from
`Decision.AUTO_APPROVE` alone.

## Alternatives Rejected

- **Rename the decision enum immediately.** Rejected because it would churn
  stored/API behavior and can be deferred while copy clarifies the meaning.
- **Always approve when the decision is `AUTO_APPROVE`.** Rejected because it
  makes new repo opt-in too risky.
- **Disable `AUTO_APPROVE` entirely.** Rejected because Guardian still needs a
  durable low-risk/cleared outcome even when formal approval is off.
