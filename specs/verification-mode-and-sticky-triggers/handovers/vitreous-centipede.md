# Handover: vitreous-centipede (Brief 04 — Snippet Disclosure)

## What was built

Added a "Show code" disclosure to each finding card on `/reviews/{id}`.
Clicking it fetches the relevant diff hunk inline; clicking again collapses it.

### Changes

**`src/pr_guardian/api/dashboard.py`**
- Added `_parse_patch_lines(patch)` — parses a unified diff patch string into
  a flat list of annotated lines (fields: `new_ln`, `old_ln`, `marker`,
  `content`, `type`). Used internally by the endpoint; not exported.
- Added `_extract_hunk(patch, target_line, context)` — filters parsed lines to
  those within `[target_line - context, target_line + context]` of the new
  file. Returns structured dicts for `renderSnippet`.
- Extended `GET /api/dashboard/reviews/{id}/diff` with three optional query
  params: `path` (str), `line` (int, ge=1), `context` (int, ge=0, default 3).
  Without params, behaviour is identical to before (full diff). With `path` +
  `line`, returns `{"file", "line", "context", "lines": [...]}`.

**`src/pr_guardian/dashboard/static/snippet.js`** (NEW)
- `fetchSnippet(reviewId, path, line, context=3)` — fetches the hunk from the
  dashboard API; returns `null` on any failure (never throws).
- `renderSnippet(container, hunkData)` — DOM-only renderer. Appends a `.hunk`
  element (reusing the CSS primitive from `human_wizard.html`). If called again
  on a container already containing `.hunk`, removes it (toggle). On null or
  empty `lines`, appends a muted "snippet unavailable" line.

**`src/pr_guardian/dashboard/review_detail.html`**
- `renderFinding()` — adds a `.snippet-area` div and a "Show code" button
  (`data-action="show-code"`, `data-file`, `data-line`) for any finding that
  has both `file` and `line`.
- Added `async toggleSnippet(btn)` — orchestrates the toggle: checks if
  already rendered, fetches via `fetchSnippet`, renders via `renderSnippet`,
  updates button text.
- Loads `snippet.js` before `command-palette.js`.

**`tests/test_snippet_endpoint.py`** (NEW) — 5 tests:
- `test_path_and_line_returns_hunk` — 200 + structured hunk
- `test_context_zero_returns_only_target_line` — context=0 narrows to exactly
  the target line
- `test_unknown_file_returns_404` — path not in diff → 404
- `test_unknown_review_returns_404` — review missing → 404
- `test_no_params_returns_full_diff` — backward compat: no params → files array

## Interfaces / contracts downstream pods must know

### `GET /api/dashboard/reviews/{id}/diff?path=X&line=N&context=M`

Response shape when `path` + `line` are present:
```json
{
  "file": "src/auth.py",
  "line": 42,
  "context": 3,
  "lines": [
    {"ln": 40, "marker": " ", "content": "    x = 1", "type": "ctx"},
    {"ln": 41, "marker": "-", "content": "    return x", "type": "del"},
    {"ln": 42, "marker": "+", "content": "    return y", "type": "add"}
  ]
}
```

`ln` is old-file line for `del` lines, new-file line for `add`/`ctx`.

### `snippet.js` exports (global scope, no module system)

```js
fetchSnippet(reviewId, path, line, context=3) // → Promise<hunkData|null>
renderSnippet(container, hunkData)             // → void, idempotent toggle
```

Pod 5 can load `snippet.js` in `human_wizard.html` and call these directly —
the functions are on `window` (no import required).

## Files this pod owns — do not modify without good reason

- `src/pr_guardian/dashboard/static/snippet.js`
- `tests/test_snippet_endpoint.py`

## Files modified that downstream pods should be aware of

- `src/pr_guardian/api/dashboard.py` — new query params on the diff endpoint
  (backward-compatible). Also added `import re` at the top.
- `src/pr_guardian/dashboard/review_detail.html` — new `.snippet-area` div and
  "Show code" button inside `renderFinding()`. New `toggleSnippet()` function.
  Loads `snippet.js` before `command-palette.js` (order matters — `toggleSnippet`
  calls `fetchSnippet`/`renderSnippet` from `snippet.js`).

## Discovered constraints / landmines

- **del-line `new_ln` invariant**: `_parse_patch_lines` sets `new_ln` on
  deletion lines to the current "next new-file position" (i.e., the `new_ln`
  that the following add/ctx line will get). This lets `_extract_hunk` filter
  del lines by new-file proximity. If this helper is ever reused for something
  that needs del lines at old-file positions, the caller must use `old_ln`.
- **`snippet.js` loads after inline script**: `toggleSnippet` in
  `review_detail.html` calls `fetchSnippet`/`renderSnippet` from `snippet.js`.
  The `<script src="/static/snippet.js">` tag is placed after the inline
  `<script>` block (before `</body>`) so `fetchSnippet`/`renderSnippet` are
  defined before any user interaction but after DOM construction — correct order.
- **Full diff re-fetch on every call**: The diff endpoint re-fetches the full
  diff from GitHub even when only one file's hunk is needed. No caching was
  added (out of scope for this brief). Pod 5 or a later pod may want to add
  a `(review_id, head_sha)` cache similar to `_capability_cache` in the same
  file.
- **`.hunk` CSS is in `styles.css`**: The `.hunk` primitive was originally
  inline-only in `human_wizard.html` using CSS variables. A concrete-value
  equivalent was added to `static/styles.css` so the snippet disclosure
  renders styled on `review_detail.html`. Pod 5 wiring `snippet.js` into
  `human_wizard.html` can rely on `styles.css` for `.hunk` styling instead
  of the inline styles (or keep both — specificity is equivalent).
