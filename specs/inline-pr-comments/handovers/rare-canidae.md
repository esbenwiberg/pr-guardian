# Handover: rare-canidae (Brief 01 — Persistence & Config Foundations)

## What was built

- **`ReviewRow.comment_mode`** — new `String(32)` column with `server_default="none"`, nullable=False. Tracks whether inline comments are posted for a review.
- **`PostedInlineCommentRow`** — new ORM table `posted_inline_comments` tracking platform-native comment IDs per review. Columns: `id` (UUID PK), `review_id` (UUID FK→reviews.id, indexed), `platform_comment_id` (String 256), `platform` (String 16), `pr_id` (String 64), `repo` (String 256), `created_at` (DateTime with timezone).
- **`save_inline_comment_ids()` / `load_inline_comment_ids()`** — async storage helpers in `storage.py` following existing `async_session()` patterns.
- **`InlineCommentsConfig`** — Pydantic model with `severity_threshold: str = "MEDIUM"`. Added as `inline_comments` field on `GuardianConfig`.
- **`alembic/versions/010_add_inline_comments.py`** — idempotent migration (checks column/table existence before creating).

## Deviations from brief

- Added `created_at` column to `PostedInlineCommentRow` (not in brief's explicit column list). The brief says to follow `FindingDismissalRow` as the style reference, which has timestamps. Added for audit consistency.

## Interfaces downstream pods must know about

- `PostedInlineCommentRow` is in `pr_guardian.persistence.models` — import it from there.
- `storage.save_inline_comment_ids(review_id, ids, platform, pr_id, repo)` — saves a list of comment ID strings in one transaction.
- `storage.load_inline_comment_ids(review_id)` — returns `list[str]` of platform comment IDs.
- `GuardianConfig.inline_comments.severity_threshold` — `str`, default `"MEDIUM"`. Orchestrator brief (03) should read this for filtering before posting.
- `ReviewRow.comment_mode` — default `"none"`. The API brief (04) and orchestrator brief (03) should set this to the appropriate mode when inline commenting is enabled.

## Files owned — do not modify without good reason

- `alembic/versions/010_add_inline_comments.py` — migration is already applied; modifying it post-deploy will break alembic state.
- `src/pr_guardian/persistence/models.py` — `PostedInlineCommentRow` and the `comment_mode` column on `ReviewRow`.

## Discovered constraints / landmines

- The `alembic/env.py` uses `async_engine_from_config` with asyncpg. Migrations run synchronously via `run_sync` inside an async context — the `_column_exists()` / `_table_exists()` idempotency helpers use `op.get_bind()` (sync connection), which is correct for this pattern.
- `server_default="none"` (string literal) on `comment_mode` is correct for SQLAlchemy — it gets passed directly as a SQL expression string to the DB, so existing rows get the value `none`.
- `PostedInlineCommentRow` has no cascade delete from `ReviewRow`. If reviews are hard-deleted, orphaned rows will remain. This is consistent with `FindingDismissalRow` (no cascade either).
