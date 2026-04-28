---
title: "Wire orchestrator and actions for inline comment mode"
depends_on: ["01-add-db-and-config", "02-add-platform-adapters"]
acceptance_criteria:
  - { type: none, test: "python -m pytest", pass: "all tests pass", fail: "any failure" }
  - { type: none, test: "python -m pytest tests/ -k 'inline'", pass: "all inline-related tests pass", fail: "any failure" }
touches:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/decision/actions.py
  - src/pr_guardian/persistence/storage.py
does_not_touch:
  - src/pr_guardian/platform/
  - src/pr_guardian/config/
  - src/pr_guardian/dashboard/
  - src/pr_guardian/api/review.py
---

## Task

Wire `_post_results()` in the orchestrator to handle `comment_mode="inline"`, and add the comment-body builder for inline findings:

1. Add `build_inline_comment_body(findings: list[Finding]) -> str` to `actions.py` — formats one or more co-located findings into a single inline comment body.
2. Update `_post_results()` in `orchestrator.py`:
   - Accept (or read from context) `comment_mode: CommentMode`.
   - `"none"` → no platform comment (unchanged).
   - `"summary"` → existing path, unchanged.
   - `"inline"` →
     a. Load severity threshold from config (`config.inline_comments.severity_threshold`).
     b. Collect all findings (agent + mechanical) that have a non-None `line`, filtered to threshold.
     c. Group by `(file, line)`.
     d. Call `adapter.post_inline_comments(pr, findings_flat)` — pass findings flat; grouping by file+line happens in the adapter's caller (here), but the adapter receives them pre-grouped per call *or* as a flat list with the adapter handling grouping internally. Use the flat-list approach: pass all qualifying findings and let the adapter group by `(path, line)` internally for simplicity.
     e. Store returned IDs via `storage.save_inline_comment_ids()`.
     f. After inline comments, still post the final summary comment via `adapter.post_comment()` (inline mode = inline comments + summary, not inline-only).
3. Update re-review path: before posting inline comments, call `storage.load_inline_comment_ids(review_id)` and pass to `adapter.delete_inline_comments()` to clear old comments first.
4. Persist `comment_mode` on `ReviewRow` when the review is created/updated.

Add unit tests (mock adapter) that assert:
- With `comment_mode="inline"` and findings of mixed severity, only MEDIUM+ findings are passed to `post_inline_comments`.
- With `comment_mode="inline"`, the final summary comment is still posted via `post_comment`.
- With `comment_mode="summary"`, `post_inline_comments` is never called.
- Re-review with `comment_mode="inline"` calls `delete_inline_comments` before `post_inline_comments`.

## Touches

- `src/pr_guardian/core/orchestrator.py` — `_post_results()` inline mode branch; re-review delete-before-repost; persist `comment_mode` on `ReviewRow`.
- `src/pr_guardian/decision/actions.py` — `build_inline_comment_body(findings)` function.
- `src/pr_guardian/persistence/storage.py` — `save_inline_comment_ids()` query implementation (skeleton added in brief 01; implement the body here if brief 01 left stubs).

## Does not touch

- `src/pr_guardian/platform/` — adapter implementations are brief 02's work; call the protocol only.
- `src/pr_guardian/config/` — schema in place from brief 01; read via existing config loader.
- `src/pr_guardian/dashboard/` — UI is brief 04's responsibility.
- `src/pr_guardian/api/review.py` — API shape is brief 04's responsibility.

## Constraints

From `design.md` → Contracts:

- Severity comparison: map threshold string to ordinal. Canonical order: `LOW < MEDIUM < HIGH < CRITICAL`. A finding qualifies if `SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER[threshold]`.
- The summary comment is always posted last in inline mode so it appears at the bottom of the PR comments, not buried under inline threads.
- Mechanical findings (`MechanicalResultRow`) also carry `file` and `line` (as dicts in the `findings` JSONB column). Deserialise them into `Finding`-like objects with at least `file`, `line`, and `severity` before filtering. If a mechanical finding lacks a `line`, skip it silently.

### `build_inline_comment_body` format

```
**[SEVERITY] Category**
Description text.

> Suggestion text (omit if empty)
```

Multiple findings at the same location are separated by `---`.

## Risks / pitfalls

- `_post_results()` is called from both the main review path and the re-review path. Ensure the `comment_mode` is read from the same place in both paths (from `ReviewRow.comment_mode`, not from the request, by the time re-review runs).
- Mechanical findings are stored as raw dicts, not `Finding` objects. Don't assume attribute access — use `.get()`.

## Wrap-up
Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run `python -m pytest`; must pass including the new inline tests.
3. Commit and push.
