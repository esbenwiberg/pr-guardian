---
title: "Add @guardian ChatOps, eyes reactions, first review, and re-review"
depends_on:
  - 02-github-installation-token-adapter
  - 04-review-postback-guidance-and-approvals
touches:
  - src/pr_guardian/core/github_chatops.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/persistence/storage.py
  - tests/test_github_chatops.py
  - tests/test_webhook_security_and_router.py
does_not_touch:
  - src/pr_guardian/dashboard/profiles.html
---

# Brief 05 - Add @guardian ChatOps, Eyes Reactions, First Review, and Re-review

## Task

Make GitHub PR comments feel like modern review bots: a user can tag
`@guardian`, Guardian reacts with `eyes` immediately when it claims the command,
and Guardian queues either a first review or a focused re-review.

## Requirements

- Accept command aliases:
  - `@guardian`
  - `@guardian re-review`
  - `@pr-guardian`
  - `@pr-guardian re-review`
- Keep existing idempotent command claiming by platform comment ID.
- Add `eyes` reaction to the triggering issue comment as soon as the command is
  claimed.
  - Treat already-present reaction as success.
  - Log but do not fail the command if reaction creation is forbidden or
    rate-limited.
- Authorization:
  - repo owner/member/collaborator can command
  - PR author can command
  - unauthorized comments are ignored and audited
- Command behavior:
  - latest completed Guardian review exists -> queue focused re-review
  - no completed Guardian review and repo is linked -> queue first review
  - repo unlinked -> update sticky guidance or ack with setup-needed message if
    possible, but do not run review
- Update short guidance copy and summary comments to say `@guardian`, while
  preserving `@pr-guardian` as an accepted alias.
- Poll fallback still recognizes commands in synced PR comments.

## Required Facts

- `fact-chatops-guardian-aliases`
- `fact-chatops-eyes-reaction`
- `fact-chatops-first-review-or-rereview`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not trigger on unrelated mentions without `@guardian` or `@pr-guardian`.
- Do not queue duplicate work for webhook redelivery or poll replay.
- Do not let unauthorized external commenters trigger review work.
