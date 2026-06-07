# Handover: Brief 04 — Post Inline Findings, Sticky Guidance, Statuses, and Configured Approval

**Pod:** financial-duck  
**Branch:** autopod/yelling-monkey (stacked)  
**Date:** 2026-06-07

## What was built

### Sticky guidance comment lifecycle

Every linked GitHub PR now gets a persistent "Guardian is watching this PR" comment
that is created when `guardian/review` first becomes pending and updated throughout
the review lifecycle.

- **`GUIDANCE_MARKER = "<!-- guardian-guidance -->"`** in `decision/actions.py` — the
  hidden HTML marker embedded in all guidance bodies so they can be recovered by scan.
- **`build_guidance_comment_body(state, *, review_url="")`** — constructs the short
  body: marker + state label + optional deeplink to `/reviews/{id}` + `@guardian` re-review
  instruction.
- **`GitHubAdapter.upsert_guidance_comment(pr, body, *, stored_comment_id=None)`** —
  implements the recovery chain: (1) PATCH stored ID; if 404 falls through to (2) GET
  `list_issue_comments` and scan for marker; if found PATCH that comment; if not found
  (3) POST new comment. Returns the platform comment ID string.
- **`readiness.py`** — on `is_new=True` candidate creation, posts initial `"pending"`
  guidance comment via duck-typed `getattr(adapter, "upsert_guidance_comment", None)`.
- **`orchestrator._upsert_guidance_comment()`** helper — called at review-start
  (`"reviewing"` state) and at `_post_results()` completion (`"success"` / `"failure"` /
  `"blocked"` state) with the deeplink.

### Persistence

- **Migration 023** (`alembic/versions/023_add_guidance_comments_and_postback.py`):
  - Creates `guidance_comments` table with `(platform, repo, pr_id)` unique constraint.
  - Adds `postback_meta` JSON column to `reviews` table.
- **`GuidanceCommentRow`** ORM model in `persistence/models.py`.
- **`storage.load_guidance_comment_id` / `storage.save_guidance_comment_id`** CRUD helpers.
- **`ReviewResult.postback_meta: dict`** — populated by `_post_results()` and persisted
  to `ReviewRow.postback_meta`.

### Postback metadata

`_post_results()` now collects a `postback` dict with these keys:
```
status_posted         bool — guardian/review status was posted
status_state          str  — "success" | "failure"
inline_comments_posted int — count of inline finding comments posted
guidance_posted       bool — guidance comment was created/updated
guidance_comment_id   str  — platform comment ID
formal_approval       str  — "posted" | "skipped_profile" | "skipped_fork" | "skipped_fork_unknown"
```

### Formal approval gating

`_apply_platform_actions()` gating: `platform_approval_enabled=True` AND
`side_effects.formal_approve=True` AND not fork AND result is `AUTO_APPROVE`. Tracked
in `postback_meta["formal_approval"]`.

### Review detail postback panel

`review_detail.html` now renders a `#postback-panel` div populated by JS from
`d.postback_meta` on review load, showing: `guardian/review` state, inline comments
count, guidance comment status, and formal approval outcome.

## Interfaces and contracts Brief 05+ must know about

### `upsert_guidance_comment` is duck-typed

Only `GitHubAdapter` implements `upsert_guidance_comment`. ADO adapter does not.
Callers use `getattr(adapter, "upsert_guidance_comment", None)` — do not add it to
the protocol unless ADO should also have it.

### `storage.load_guidance_comment_id / save_guidance_comment_id`

```python
async def load_guidance_comment_id(platform: str, repo: str, pr_id: str) -> str | None
async def save_guidance_comment_id(platform: str, repo: str, pr_id: str, comment_id: str) -> None
```

Brief 05 (ChatOps) should call `load_guidance_comment_id` before updating the guidance
comment after a re-review command so it can pass the stored ID to `upsert_guidance_comment`.

### `ReviewResult.postback_meta` is a plain dict

Keys are optional; callers should use `.get(key)` not direct access. The dict is
persisted as JSON in `ReviewRow.postback_meta` and returned by `_review_to_dict()`.

### `_upsert_guidance_comment` in orchestrator

```python
async def _upsert_guidance_comment(
    adapter, pr: PlatformPR, state: str, *, review_url: str = "", storage=None
) -> str | None
```

Brief 05 should call the same helper (or replicate the pattern) when posting
post-re-review guidance updates. The helper handles missing-method gracefully.

### `test_readiness_storage.py` hand-rolled schema includes `guidance_comments`

The in-memory SQLite test schema now includes the `guidance_comments` table and the
`postback_meta` column on `reviews`. Future briefs that add columns to `reviews` or
`guidance_comments` **must** also update the hand-rolled schema in
`tests/test_readiness_storage.py` or tests that use `_make_session_factory()` will
fail with `OperationalError: no such column`.

## Files I own — downstream should not modify without good reason

- `alembic/versions/023_add_guidance_comments_and_postback.py` — append-only
- `src/pr_guardian/decision/actions.py` — `GUIDANCE_MARKER` and `build_guidance_comment_body`
- `src/pr_guardian/platform/github.py` — `upsert_guidance_comment` method (lines around 200–260)
- `tests/test_github_guidance_comment.py` — fact test for sticky guidance
- `tests/browser/review_detail_postback.spec.mjs` — fact test for postback panel

## Brief 03 (embarrassing-booby) was not present

`specs/github-app-devx-hardening/handovers/embarrassing-booby.md` did not exist at
the start of this pod. Brief 04 does not depend on merge-gate enforcement (Brief 03),
so the absence did not block any implementation. Brief 05 and 06 should check whether
Brief 03 landed before assuming `ensure_required_review_check` is available.

## Discovered constraints / landmines

1. **Sticky guidance is NOT coupled to `comment_mode`** — it is always attempted for
   GitHub adapters regardless of whether review comments are enabled. Do not add a
   `comment_mode` guard around guidance calls.

2. **Recovery scan reads all issue comments** — `list_issue_comments` returns all
   top-level PR comments, not just Guardian ones. For PRs with many comments this is a
   full-page GET. Future optimization: pass `since` parameter or cache the last-known ID.

3. **`_post_results` signature now includes `storage=None`** — Brief 04 added
   `storage: Any = None` to `_post_results()` so guidance persistence works. Callers
   that don't pass `storage` get no persistence (guidance comment ID not saved), but the
   comment is still posted. Brief 05 should pass `storage` when calling orchestrator
   methods.

4. **Formal approval `skipped_fork_unknown`** — when `fetch_pr_metadata` raises or
   returns `None`, the fork check result is unknown. The conservative path is to skip
   approval and record `"skipped_fork_unknown"`. Downstream briefs should not rely on
   this case resolving to `"posted"`.
