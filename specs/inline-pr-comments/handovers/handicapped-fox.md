# Handover: handicapped-fox (Brief 02 — Platform Adapter Inline Comments)

## What was built

- **`PlatformAdapter.post_inline_comments()`** — abstract method added to `protocol.py`. Groups findings by `(file, line)`, posts one comment per group. Returns list of platform-native comment IDs. Findings with `line=None` are silently skipped. Lines outside the diff (422 response) are silently skipped.
- **`PlatformAdapter.delete_inline_comments()`** — abstract method added to `protocol.py`. Deletes comments by their platform-native IDs.
- **`GitHubAdapter.post_inline_comments()`** — posts one review (event=COMMENT) per (file, line) group via `POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews`. Collects `comments[].id` from each review response.
- **`GitHubAdapter.delete_inline_comments()`** — deletes via `DELETE /repos/{owner}/{repo}/pulls/comments/{comment_id}`.
- **`ADOAdapter.post_inline_comments()`** — posts one thread with `threadContext` per (file, line) group via `POST /{org}/{project}/_apis/git/repositories/{repo}/pullRequests/{pr_id}/threads`. Collects thread `id` from each response.
- **`ADOAdapter.delete_inline_comments()`** — no ADO thread deletion; PATCHes thread `status` to 4 (byDesign) then posts a reply "This comment was superseded by a re-review."
- **`src/pr_guardian/platform/_utils.py`** — shared `inline_comment_body(findings)` helper used by both adapters.
- **`tests/test_inline_comments.py`** — 10 unit tests covering ID collection, 422 skipping, grouping, and correct endpoint selection.

## Deviations from brief

- GitHub implementation posts **one review per (file, line) group** (not one batch review for all groups). This cleanly implements per-group 422 handling without a retry loop. The brief's "comments array" example shows the API shape; posting one-per-group satisfies the "silently skip that finding group" requirement.
- Extracted `inline_comment_body()` to `platform/_utils.py` (not in the brief) to avoid identical duplication across adapters.

## Interfaces downstream pods must know about

- `post_inline_comments(pr, findings, *, threshold="MEDIUM") -> list[str]` — caller must pre-filter findings to threshold before passing. The `threshold` param is accepted but not used inside the adapter (by design per the contract).
- `delete_inline_comments(pr, comment_ids)` — for ADO, `comment_ids` are thread IDs (integers as strings, e.g. `"42"`). For GitHub, they are review comment IDs.
- `inline_comment_body` helper is in `pr_guardian.platform._utils` — underscore prefix signals it's internal to the platform package.

## Files owned — do not modify without good reason

- `src/pr_guardian/platform/protocol.py` — new method signatures are the contract; changing them breaks all implementors.
- `src/pr_guardian/platform/_utils.py` — shared formatting; changes affect both adapters.
- `tests/test_inline_comments.py` — new test file; extend, don't replace.

## Discovered constraints / landmines

- **GitHub posts one review per group, not a batch**: If you want to change to batch mode, you must handle 422 failures differently (retry each comment individually). The current per-group approach is simpler and more predictable.
- **ADO 422 behavior**: ADO returns 422 when the `threadContext.filePath` or line range is outside the PR diff. The catch handles this identically to GitHub.
- **ADO thread IDs are integers in the API response**: `resp.json().get("id")` returns an int; we stringify it with `str(thread_id)`. The downstream storage layer stores these as strings (see `PostedInlineCommentRow.platform_comment_id: String(256)`).
- **ADO file paths require leading `/`**: Already handled — the adapter prepends `/` if not present.
- **`threshold` parameter is intentionally unused in adapters**: The contract says "caller pre-filters". Don't add filtering logic inside the adapter without coordinating with Brief 03 (orchestrator).
