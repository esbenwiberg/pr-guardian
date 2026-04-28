# Handover: lovely-muskox (Brief 04 — User-Facing Surface)

## What was built

- **`ReviewRequest.comment_mode`** — replaced `post_comment: bool = False` with `comment_mode: Literal["none", "summary", "inline"] = "none"` in `api/review.py`. Added `model_config = ConfigDict(extra="forbid")` so the old `post_comment` field is rejected with a 422.
- **`FullReviewRequest.comment_mode`** — same replacement in `api/agent_api.py` (the agent-facing endpoint), default `"summary"` to preserve prior behaviour for automated agents.
- **`reviews.html`** — checkbox replaced with a `<select>` tri-state (`none`/`summary`/`inline`), default `none`. JS updated to read `.value` and pass `comment_mode` in the fetch body.
- **`review_detail.html`** — re-review JS now sends `comment_mode: reviewData.comment_mode ?? "summary"` instead of the hardcoded `post_comment: true`.
- **`dev_seed.py`** — seventh seeded review with `comment_mode="inline"`, two MEDIUM+ findings with real `file`/`line`, and two `PostedInlineCommentRow` rows (`gh-comment-001`, `gh-comment-002`). `_wipe()` now clears `PostedInlineCommentRow` before `ReviewRow` to avoid FK violations on repeated runs.

## Deviations from brief

- Also updated `api/agent_api.py` (`FullReviewRequest`) — not listed in the brief's "Touches" but had a remaining `post_comment: bool = True` that would have created an inconsistent API surface. The simplify pass surfaced it.
- `run_re_review` in `agent_api.py` is called without explicit `comment_mode` or `post_comment` kwargs — `run_re_review` reads `comment_mode` directly from `original_review`, so no change needed there.

## Interfaces downstream pods must know about

- `POST /api/review` accepts `comment_mode: "none"|"summary"|"inline"` (default `"none"`). The old `post_comment` field is hard-rejected with 422 (`extra="forbid"`).
- `POST /api/agent/review` accepts `comment_mode: "none"|"summary"|"inline"` (default `"summary"`). Same rejection behaviour.
- `reviewData.comment_mode` is now populated on all reviews from seeded data forward. Pre-migration reviews may return `null` — the `?? "summary"` fallback in `review_detail.html` handles this.

## Files owned — do not modify without good reason

- `src/pr_guardian/api/review.py` — `ReviewRequest` with `extra="forbid"` is load-bearing for the AC.
- `src/pr_guardian/api/agent_api.py` — `FullReviewRequest` aligned to the same pattern.
- `src/pr_guardian/dashboard/reviews.html` — tri-state select, default `none`.
- `src/pr_guardian/dashboard/review_detail.html` — `?? "summary"` fallback is intentional.
- `scripts/dev_seed.py` — `PostedInlineCommentRow` must be wiped before `ReviewRow` in `_wipe()`.

## Discovered constraints / landmines

- **`ConfigDict(extra="forbid")` must be on `ReviewRequest`** — Pydantic v2 silently ignores extra fields by default. Without `extra="forbid"`, `post_comment: true` would pass validation and silently be ignored, causing the AC to fail.
- **`_wipe()` order matters** — `PostedInlineCommentRow` has a FK to `ReviewRow` with no cascade. Wipe `PostedInlineCommentRow` before `ReviewRow` or the second seed run will fail with a FK violation.
- **`run_re_review` reads `comment_mode` from `original_review` dict** — it does NOT accept `comment_mode` as a kwarg. Don't try to pass it directly.
