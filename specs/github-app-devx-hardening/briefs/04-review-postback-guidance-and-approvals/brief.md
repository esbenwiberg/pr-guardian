---
title: "Post inline findings, sticky guidance, statuses, and configured approval"
depends_on:
  - 01-github-app-connection-data-model
  - 02-github-installation-token-adapter
  - 03-github-app-setup-and-merge-gates
touches:
  - src/pr_guardian/core/readiness.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/decision/actions.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/dashboard/review_detail.html
  - tests/test_github_guidance_comment.py
does_not_touch:
  - src/pr_guardian/dashboard/profiles.html
---

# Brief 04 - Post Inline Findings, Sticky Guidance, Statuses, and Configured Approval

## Task

Harden the GitHub review postback lifecycle so every PR gets clear, low-noise
Guardian guidance and auto-review posts inline findings by default, while formal
approval remains Profile-controlled.

## Requirements

- Create/update the sticky top-level guidance comment:
  - create when `guardian/review` first becomes pending
  - update while waiting/reviewing
  - update with latest green/red/blocked result and Guardian review deeplink
  - update after re-review
  - recover by hidden marker if local comment ID is missing or stale
  - recreate if deleted
- Keep the guidance short:
  - latest Guardian state
  - deeplink to `/reviews/{review_id}` when available
  - `@guardian` re-review instruction
- Auto-review on GitHub uses inline finding comments by default when comments
  are enabled. Sticky guidance is independent and always attempted for linked
  GitHub PRs.
- `guardian/review` remains the merge-blocking status:
  - pending at candidate/review start
  - success when Guardian clears
  - failure when changes are requested or blocked
  - target URL points to Guardian review detail when available
- Formal GitHub approval happens only when:
  - resolved Profile has `platform_approval_enabled=true`
  - resolved Profile side effect `formal_approve=true`
  - PR is not fork-origin
  - Guardian result is clear/auto-approve
- Review detail shows a postback panel with status, inline comments, sticky
  guidance, formal approval posted/skipped, and merge gate state.

## Required Facts

- `fact-sticky-guidance-upserts-through-lifecycle`
- `fact-auto-review-posts-inline-comments`
- `fact-formal-approval-stays-profile-gated`
- `fact-review-detail-postback-browser-panel`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not make the sticky guidance an inline review thread.
- Do not spam a fresh guidance comment on every run.
- Do not couple sticky guidance to `comment_mode`; it is a product guidance
  comment, not the review summary.
- Do not approve fork-origin PRs.
