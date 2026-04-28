# Design — Inline PR Comments

## Blast radius

```
src/pr_guardian/platform/protocol.py          — new protocol methods
src/pr_guardian/platform/github.py            — implement inline comments (Reviews API)
src/pr_guardian/platform/ado.py               — implement inline comments (threads + threadContext)
src/pr_guardian/core/orchestrator.py          — _post_results() wired for inline mode
src/pr_guardian/decision/actions.py           — build_inline_comment_body()
src/pr_guardian/persistence/models.py         — PostedInlineCommentRow, comment_mode on ReviewRow
src/pr_guardian/persistence/storage.py        — save/load posted comment IDs
src/pr_guardian/config/schema.py              — inline_comments.severity_threshold
src/pr_guardian/api/review.py                 — comment_mode replaces post_comment
src/pr_guardian/dashboard/reviews.html        — checkbox → tri-state selector
src/pr_guardian/dashboard/review_detail.html  — re-review inherits comment_mode from ReviewRow
scripts/dev_seed.py                           — seed review with comment_mode=inline
alembic/versions/<new>.py                     — migration for new columns/table
```

## Seams

**Seam 1 — UI/API boundary** (Brief 04 → Brief 03):
`ReviewRequest.comment_mode` replaces `post_comment: bool`. Owner: Brief 04 defines the field; Brief 03 consumes it in the orchestrator.

**Seam 2 — Orchestrator → Platform** (Brief 03 → Brief 02):
`_post_results()` calls `adapter.post_inline_comments()` and `adapter.delete_inline_comments()`. Owner: Brief 02 defines the protocol + implementations; Brief 03 calls them.

**Seam 3 — Orchestrator → Persistence** (Brief 03 → Brief 01):
`_post_results()` writes `PostedInlineCommentRow` entries after posting; reads them before re-review to get IDs to delete. Owner: Brief 01 defines the model and storage helpers; Brief 03 uses them.

**Seam 4 — Re-review inheritance** (Brief 04 → Brief 03):
`review_detail.html` reads `ReviewRow.comment_mode` and passes it when triggering re-review. Owner: Brief 01 adds the column; Brief 04 reads it in the UI; Brief 03 honours it in the orchestrator.

## Contracts

### `comment_mode` enum

```python
from typing import Literal
CommentMode = Literal["none", "summary", "inline"]
```

Used on `ReviewRequest` (API input), `ReviewRow` (persisted), and passed through the orchestrator.

### `PlatformAdapter` new methods

```python
async def post_inline_comments(
    self,
    pr: PlatformPR,
    findings: list[Finding],       # already filtered to threshold
    *,
    threshold: str = "MEDIUM",     # passed for reference only; caller pre-filters
) -> list[str]:
    """Post one inline comment per unique file+line group.
    Returns list of platform-native comment IDs (GitHub: comment id, ADO: thread id).
    Findings whose line is None or outside the diff are silently skipped.
    """

async def delete_inline_comments(
    self,
    pr: PlatformPR,
    comment_ids: list[str],
) -> None:
    """Delete previously posted inline comments by their platform-native IDs.
    GitHub: DELETE /repos/{owner}/{repo}/pulls/comments/{id}
    ADO: PATCH thread status to 4 (byDesign) — ADO does not support thread deletion.
    """
```

### `PostedInlineCommentRow` ORM model

```python
class PostedInlineCommentRow(Base):
    __tablename__ = "posted_inline_comments"
    id: uuid.UUID          # PK
    review_id: uuid.UUID   # FK → reviews.id
    platform_comment_id: str  # GitHub comment ID or ADO thread ID (as string)
    platform: str
    pr_id: str
    repo: str
```

### `ReviewRow.comment_mode` column

```python
comment_mode: Mapped[str] = mapped_column(String, default="none")
```

### Config addition

```python
class InlineCommentsConfig(BaseModel):
    severity_threshold: str = "MEDIUM"   # MEDIUM | HIGH | CRITICAL

class GuardianConfig(BaseModel):
    ...
    inline_comments: InlineCommentsConfig = Field(default_factory=InlineCommentsConfig)
```

## UX flows

### Triggering a review (`reviews.html`)

Current: checkbox "Post comment on PR" → `post_comment: bool`.

New: radio group or `<select>` with three options:
- **No comment** → `comment_mode: "none"`
- **Summary comment** → `comment_mode: "summary"`
- **Inline comments** → `comment_mode: "inline"`

Default selection: "No comment" (preserves existing default behaviour).

### Re-review (`review_detail.html`)

Current: hardcodes `post_comment: true` in the fetch body (line 843).

New: reads `reviewData.comment_mode` (already loaded with the review) and passes it as `comment_mode` in the fetch body. If `comment_mode` is `"inline"`, the orchestrator will delete old inline comments and repost.

## Reference reading

- `src/pr_guardian/platform/protocol.py` — abstract base to extend with new methods.
- `src/pr_guardian/platform/github.py` — existing adapter; `post_comment()` at line 84 shows request pattern. Inline comments use `POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews` with `comments` array and `"event": "COMMENT"`.
- `src/pr_guardian/platform/ado.py` — existing adapter; `post_comment()` at line 290 shows thread pattern. Inline comments add `threadContext` with `filePath` and `rightFileStart`/`rightFileEnd`.
- `src/pr_guardian/core/orchestrator.py:945-982` — `_post_results()` phase where platform calls are made.
- `src/pr_guardian/decision/actions.py` — `build_summary_comment()` shows the comment-building pattern to follow for `build_inline_comment_body()`.
- `src/pr_guardian/persistence/models.py` — ORM patterns; `FindingDismissalRow` (line 257) is a good model for `PostedInlineCommentRow`.
- `src/pr_guardian/config/schema.py` — existing config sections show how to add `InlineCommentsConfig`.
- `docs/decisions/ADR-001-inline-comment-mode-tristate.md` — rationale for replacing `post_comment: bool`.

## Decisions

- ADR-001: Replace `post_comment: bool` with `comment_mode` tri-state (introduced by this feature).
