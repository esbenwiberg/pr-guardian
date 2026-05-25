# ADR-001: Replace `post_comment` bool with `comment_mode` tri-state

## Status
Accepted — 2026-05-25. Implemented in `ReviewRequest.comment_mode`,
`ReviewRow.comment_mode`, and the inline-comment posting flow.

## Context
`POST /api/review` previously accepted `post_comment: bool` to control whether a summary comment was posted to the PR after review. Adding inline comments as a third posting mode (per-finding inline comments + final summary) cannot be expressed as a boolean — it is a distinct, mutually exclusive mode alongside "no comment" and "summary only".

Keeping `post_comment: bool` and adding a separate `inline_comments: bool` would allow callers to pass both as `true`, creating an undefined combined state. The two modes are mutually exclusive by design (user-confirmed).

## Decision
Replace `post_comment: bool = False` with `comment_mode: Literal["none", "summary", "inline"] = "none"` on `ReviewRequest` and persist the value as a string column on `ReviewRow`. The old field is not supported via a compatibility shim — callers passing `post_comment` receive a 422.

## Consequences

**Easier:** comment mode intent is explicit and unambiguous; no undefined combined states; `ReviewRow.comment_mode` lets re-review inherit the original posting intent without extra logic.

**Harder:** any existing API callers that pass `post_comment: true` break. This is an internal API with no known external consumers, so the breakage is acceptable.

**Committed to:** `"none"` is the default — no comment is posted unless the caller opts in. This matches the previous default (`post_comment: bool = False`).
