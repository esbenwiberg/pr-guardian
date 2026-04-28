---
title: "Add API tri-state, update UI, and seed inline review"
depends_on: ["01-add-db-and-config", "03-wire-orchestrator-and-actions"]
acceptance_criteria:
  - { type: none, test: "python -m pytest", pass: "all tests pass", fail: "any failure" }
  - { type: api, test: "POST /api/review with body {\"pr_url\": \"https://github.com/esbenwiberg/pr-guardian/pull/101\", \"comment_mode\": \"inline\"}", pass: "202 accepted, no 422 validation error", fail: "422 or 500" }
  - { type: api, test: "POST /api/review with body {\"pr_url\": \"https://github.com/esbenwiberg/pr-guardian/pull/101\", \"post_comment\": true}", pass: "422 validation error (old field rejected)", fail: "200 or 202 — old field must not silently pass" }
  - { type: none, test: "python scripts/dev_seed.py", pass: "exit 0, prints seeded N reviews", fail: "any error" }
  - { type: none, test: "python -c \"import asyncio; from pr_guardian.persistence.database import async_session; from pr_guardian.persistence.models import ReviewRow; ...\"", pass: "a ReviewRow with comment_mode='inline' exists in DB after seeding", fail: "no such row" }
touches:
  - src/pr_guardian/api/review.py
  - src/pr_guardian/dashboard/reviews.html
  - src/pr_guardian/dashboard/review_detail.html
  - scripts/dev_seed.py
does_not_touch:
  - src/pr_guardian/platform/
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/config/
---

## Task

Complete the user-facing surface: replace the `post_comment` bool with the `comment_mode` tri-state in the API, update both UI touchpoints, and add a seeded review so the agent can self-validate without a live GitHub/ADO token.

### 1. `api/review.py` — replace `post_comment` with `comment_mode`

Replace:
```python
post_comment: bool = False
```
With:
```python
from typing import Literal
comment_mode: Literal["none", "summary", "inline"] = "none"
```

Pass `comment_mode` through to the orchestrator. Remove any remaining references to `post_comment`.

### 2. `reviews.html` — checkbox → tri-state selector

Replace the single "Post comment on PR" checkbox (line 170) with a `<select>` or radio group:

- **No comment** → value `none` (default)
- **Summary comment** → value `summary`
- **Inline comments** → value `inline`

Update the JavaScript that reads the value (currently `document.getElementById('review-post-comment').checked`, line 405) to read the new element and pass `comment_mode` in the fetch body instead of `post_comment`.

### 3. `review_detail.html` — inherit `comment_mode` on re-review

Replace the hardcoded `post_comment: true` at line 843 with:
```js
comment_mode: reviewData.comment_mode ?? "summary",
```
(`reviewData` is already loaded with the full review object; the new column is available after the brief 01 migration.)

### 4. `dev_seed.py` — add inline review seed

Add a seventh seeded review with `comment_mode="inline"` and at least two MEDIUM+ findings that have real `file` and `line` values. Also add two `PostedInlineCommentRow` rows for that review (with fake `platform_comment_id` strings like `"gh-comment-001"`) to demonstrate the schema is wired correctly.

Update the final print statement to reflect the new count.

## Touches

- `src/pr_guardian/api/review.py` — `ReviewRequest` model change, remove `post_comment`.
- `src/pr_guardian/dashboard/reviews.html` — checkbox → tri-state, JS update.
- `src/pr_guardian/dashboard/review_detail.html` — re-review `comment_mode` inheritance.
- `scripts/dev_seed.py` — new inline-mode review + `PostedInlineCommentRow` seed rows.

## Does not touch

- `src/pr_guardian/platform/` — adapters complete from brief 02.
- `src/pr_guardian/core/orchestrator.py` — wiring complete from brief 03.
- `src/pr_guardian/persistence/models.py` — schema complete from brief 01.
- `src/pr_guardian/config/` — config complete from brief 01.

## Constraints

From `design.md` → Contracts and UX flows:

- `comment_mode` default is `"none"` — this preserves the existing behaviour where no comment is posted unless the user actively opts in.
- The `<select>` default selected option must be "No comment".
- Do not add backwards-compat shims for the old `post_comment` field — the AC explicitly verifies the old field is rejected with a 422.

## Risks / pitfalls

- `reviewData.comment_mode` may be `null` for reviews seeded before this migration (they predate the column). The `?? "summary"` fallback in the re-review JS handles this — `"summary"` is the closest equivalent to the old hardcoded `post_comment: true`.

## Wrap-up
Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run `python -m pytest` and `python scripts/dev_seed.py` (with `DATABASE_URL` set); both must pass.
3. Commit and push.
