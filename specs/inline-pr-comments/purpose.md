# Inline PR Comments

## Problem
PR Guardian posts a single summary comment on each PR. Findings are only visible in full on the PR Guardian dashboard — PR authors and reviewers must follow a link to see what specifically is wrong and where. There is no way to see a finding in context alongside the diff line it refers to.

## Outcome
When a user selects "Inline comments" mode before triggering a review, PR Guardian posts each MEDIUM+ finding as a native inline comment anchored to its exact diff line in GitHub or Azure DevOps, plus a final summary comment carrying the overall verdict.

## Users
PR authors who want to see findings in context without leaving the GitHub or ADO PR UI. Code reviewers who triage findings alongside the diff.

## Success signal
A finding at MEDIUM severity or higher appears as an inline comment on the exact diff line in the GitHub or ADO PR UI. Observable by opening the PR in either platform after a review completes with `comment_mode=inline`.

## Non-goals
- Per-repo severity threshold override (system-level config only).
- In-place editing of previously posted inline comments — delete-and-repost on re-review only.
- New dashboard UI surface for inline comments — they are platform-native only.
- Platforms beyond GitHub and Azure DevOps.
- Inline anchoring for findings whose line is outside the PR diff — those are silently skipped and still appear in the final summary comment.

## Glossary
- **Inline comment** — a platform-native per-line comment anchored to a specific file and line within the PR diff (GitHub: pull review comment; ADO: thread with `threadContext`).
- **Comment mode** — tri-state on a review request: `none` (no platform comment), `summary` (existing single summary comment), `inline` (per-finding inline comments + final summary).
- **Final summary comment** — the overall verdict comment posted after all inline comments; present in both `summary` and `inline` modes.
- **MEDIUM+ finding** — a finding whose severity is MEDIUM, HIGH, or CRITICAL.
- **Severity threshold** — the minimum severity for a finding to be posted as an inline comment; configurable system-wide, defaults to MEDIUM.
