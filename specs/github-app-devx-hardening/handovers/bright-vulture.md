# Handover: Brief 05 — @guardian ChatOps, Eyes Reactions, First Review, and Re-review

**Pod:** bright-vulture  
**Branch:** autopod/yelling-monkey (stacked)  
**Date:** 2026-06-07

## What was built

### `src/pr_guardian/core/github_chatops.py` (rewritten)

- **`_COMMAND_RE`** — new regex matching `@guardian` and `@pr-guardian` (with optional ` re-review` suffix). Negative lookaheads prevent false positives on `@guardian-app` or `@guardianstuff`.
- **`is_github_command(body)`** — public function for the new broader command detection. Replaces `is_github_re_review_command` as the primary detection used by `handle_github_comment`.
- **`is_github_re_review_command`** — kept unchanged for backward compat (existing tests rely on it; it still uses the old `_MENTION_REVIEW_RE` regex requiring explicit `re-review`).
- **`_GUARDIAN_COMMAND = "guardian"`** — unified claim command name (replaces `"re-review"`). Affects `claim_chatops_command` calls going forward.
- **`_add_eyes_reaction(adapter, repo, comment_id)`** — duck-typed helper. Calls `adapter.create_issue_comment_reaction(repo, comment_id, "eyes")` if the method exists; swallows all errors with a warning log.
- **`_adapter_for_repo_link(repo_link)`** — builds a `GitHubAdapter` from a repo link's `connection_id` using `build_github_adapter_from_connection`.
- **`_run_first_review_background(command_id, pr, repo_link, base_url)`** — background task that runs `run_review()` for a PR with no previous Guardian review.
- **`handle_github_comment`** updated to:
  1. Use `is_github_command` for detection.
  2. Claim under `_GUARDIAN_COMMAND = "guardian"` (existing `"re-review"` DB records are unaffected — comment_id deduplication still holds).
  3. Immediately react with `eyes` after claiming (best-effort, via `_add_eyes_reaction`).
  4. Look up `get_active_repo_link_for_repo` (with `require_auto_review=False`) when no prior review exists.
  5. Handle unlinked-repo case: mark ignored, post setup-needed ack if possible, return `"repo_not_linked"`.
  6. Queue first review (`_run_first_review_background`) when repo is linked but no prior review.
  7. Queue re-review (`_run_re_review_background`) when a prior review exists.
  8. Changed ack comment from `"PR Guardian: re-review queued."` → `"Guardian: re-review queued."` (and `"Guardian: first review queued."`).

### `src/pr_guardian/platform/github.py`

- **`GitHubAdapter.create_issue_comment_reaction(repo, comment_id, content)`** — POSTs to `/repos/{repo}/issues/comments/{comment_id}/reactions`. Returns on 200 (already exists) or 201 (created); raises for any other status.

### `src/pr_guardian/api/webhooks.py`

- `github_webhook` now passes `pr_author=(issue.get("user") or {}).get("login") or ""` to `handle_github_comment` so the PR author can be used for authorization without an extra API call.

### `tests/test_github_chatops.py`

Three required fact tests added:
- `test_is_github_command_accepts_guardian_and_legacy_aliases`
- `test_handle_github_comment_reacts_with_eyes_when_claimed`
- `test_guardian_mention_queues_first_review_or_rereview`

Plus an error-resilience test:
- `test_handle_github_comment_eyes_reaction_error_does_not_fail_command`

`_FakeGitHubAdapter` gains `reactions: list[tuple[str, str, str]]` and `create_issue_comment_reaction`.

Existing `test_handle_github_comment_queues_re_review` updated for new ack copy (`"Guardian: re-review queued."`).

## Interfaces and contracts Brief 06+ must know about

### `_GUARDIAN_COMMAND = "guardian"` is the new claim key

Chatops commands claimed under `"guardian"` replace the old `"re-review"` key. Any E2E harness or test fixture that checks the `command` field in `chatops_commands` DB rows should expect `"guardian"`, not `"re-review"`.

### `is_github_command(body)` vs `is_github_re_review_command(body)`

Use `is_github_command` for all new code. `is_github_re_review_command` is kept as a legacy compatibility function and accepts only the explicit `@pr-guardian re-review` form. Do not use it for new detection logic.

### `create_issue_comment_reaction` is duck-typed

`_add_eyes_reaction` uses `getattr(adapter, "create_issue_comment_reaction", None)`. Only `GitHubAdapter` has this method; the ADO adapter does not. Do not add it to the protocol unless ADO should also react.

### First-review path uses `require_auto_review=False`

`handle_github_comment` calls `get_active_repo_link_for_repo(..., require_auto_review=False)` so it detects linked-but-paused repos. If the repo link is paused, Guardian still queues a first review when a user explicitly commands it. Brief 06 E2E should be aware of this behavior.

### `_run_first_review_background` calls `run_review()` directly

Unlike `_run_re_review_background` which calls `run_re_review()`, first review calls `run_review()` with `existing_review_db_id=None`. The orchestrator creates a new review row. No readiness candidate is created or evaluated — the chatops path bypasses readiness gating by design (user explicitly requested).

### Webhook now passes `pr_author` from issue payload

The `issue_comment` webhook handler extracts the PR author from `issue["user"]["login"]` and passes it as `pr_author` to `handle_github_comment`. The PR author field enables authorization checks without an extra `fetch_pr` API call in the common case.

## Files I own — downstream should not modify without good reason

- `src/pr_guardian/core/github_chatops.py` — complete chatops module
- `tests/test_github_chatops.py` — all chatops tests

## Discovered constraints / landmines

1. **`_GUARDIAN_COMMAND = "guardian"` breaks idempotency for in-flight `"re-review"` records** — if a comment was claimed under the old `"re-review"` command name and the webhook is redelivered after deploy, the claim will succeed a second time under `"guardian"`. This is a one-time transition edge case with no practical impact (the review job itself is idempotent).

2. **`_adapter_for_repo_link` creates a new adapter** — the command adapter (used for eyes reaction + PR fetch) is a separate instance from the background adapter. Both are closed in their respective `finally` blocks. Do not try to share adapters across the `asyncio.create_task` boundary.

3. **Unlinked-repo ack is not possible** — when `review is None and repo_link is None`, no adapter exists (we don't know which GitHub App installation to authenticate with). The command is marked ignored with `"repo not linked"`; no ack comment is posted. The brief's "if possible" qualifier covers this case.

4. **First review bypasses readiness gating** — explicitly by design (user ChatOps command overrides auto-review paused state). If Brief 06 needs the E2E to verify first-review-via-chatops, it should ensure the PR comment is posted before Guardian processes the webhook and that the repo link exists with `require_auto_review=False` accessible.

5. **`@guardian-app` does NOT trigger** — the `(?!\w)(?!-)` lookaheads ensure `@guardian` followed by `-` is rejected. Brief 06 E2E should use plain `@guardian` in test comments.

6. **Bare `@pr-guardian review` (without re-review) now triggers** — `is_github_command` accepts any `@guardian`/`@pr-guardian` mention. A user saying "great job @pr-guardian" will now trigger the chatops flow. This is consistent with modern review bot behavior (see Copilot, CodeRabbit) but may surprise admins. The duplicate check prevents runaway work on repeated mentions.
