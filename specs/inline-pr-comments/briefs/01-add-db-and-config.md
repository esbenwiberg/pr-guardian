---
title: "Add DB schema and config for inline comment mode"
depends_on: []
acceptance_criteria:
  - { type: none, test: "python -m pytest", pass: "all tests pass", fail: "any failure" }
  - { type: none, test: "alembic upgrade head", pass: "exit 0 with no errors", fail: "migration fails or errors" }
  - { type: none, test: "python -c \"from pr_guardian.persistence.models import PostedInlineCommentRow, ReviewRow; r = ReviewRow.__table__.c; assert 'comment_mode' in r.keys(); print('ok')\"", pass: "prints ok", fail: "ImportError or AssertionError" }
  - { type: none, test: "python -c \"from pr_guardian.config.schema import GuardianConfig; c = GuardianConfig(); assert c.inline_comments.severity_threshold == 'MEDIUM'; print('ok')\"", pass: "prints ok", fail: "AttributeError or AssertionError" }
touches:
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/config/schema.py
  - alembic/versions/
does_not_touch:
  - src/pr_guardian/platform/
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/api/review.py
  - src/pr_guardian/dashboard/
---

## Task

Add the persistence and config foundations that later briefs depend on:

1. Add `comment_mode: str` column (default `"none"`) to `ReviewRow`.
2. Create new `PostedInlineCommentRow` table to track platform-native comment IDs posted per review.
3. Add storage helpers `save_inline_comment_ids()` and `load_inline_comment_ids()` to `storage.py`.
4. Add `InlineCommentsConfig` to `GuardianConfig` with a `severity_threshold` field defaulting to `"MEDIUM"`.
5. Write an Alembic migration for the new column and table.

## Touches

- `src/pr_guardian/persistence/models.py` — add `PostedInlineCommentRow` ORM class and `comment_mode` column to `ReviewRow`.
- `src/pr_guardian/persistence/storage.py` — add `save_inline_comment_ids()` and `load_inline_comment_ids()` helpers.
- `src/pr_guardian/config/schema.py` — add `InlineCommentsConfig` Pydantic model and `inline_comments` field on `GuardianConfig`.
- `alembic/versions/<new>.py` — migration adding `comment_mode` to `reviews` and creating `posted_inline_comments` table.

## Does not touch

- `src/pr_guardian/platform/` — adapters are brief 02's responsibility.
- `src/pr_guardian/core/orchestrator.py` — wiring is brief 03's responsibility.
- `src/pr_guardian/api/review.py` — API change is brief 04's responsibility.
- `src/pr_guardian/dashboard/` — UI is brief 04's responsibility.

## Constraints

Follow the ORM patterns in `design.md` → Contracts:

- `PostedInlineCommentRow`: columns `id` (UUID PK), `review_id` (UUID FK → reviews.id, indexed), `platform_comment_id` (String), `platform` (String), `pr_id` (String), `repo` (String).
- `ReviewRow.comment_mode`: `Mapped[str]`, server_default `"none"`, nullable False.
- `InlineCommentsConfig.severity_threshold`: `str = "MEDIUM"`. Valid values are `"MEDIUM"`, `"HIGH"`, `"CRITICAL"`. No validation enforcement needed — the orchestrator uses it as a string comparison.
- Follow `FindingDismissalRow` (models.py line 257) as the ORM style reference.
- Storage helpers use `async_session()` context manager matching existing helpers in `storage.py`.

```python
# storage.py additions (signatures only — implement to match existing async patterns)
async def save_inline_comment_ids(review_id: uuid.UUID, ids: list[str], platform: str, pr_id: str, repo: str) -> None: ...
async def load_inline_comment_ids(review_id: uuid.UUID) -> list[str]: ...
```

## Wrap-up
Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run `python -m pytest` and `alembic upgrade head`; both must still pass.
3. Commit and push.
