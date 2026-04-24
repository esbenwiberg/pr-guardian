# Plan: "Browse PR" nav entry — open the chapter-review UI for any PR

> **Worker handoff.** You have fresh context. Read this plan and the referenced files before editing. Do not re-plan — implement as specified. When unsure between two equivalent phrasings, pick the shorter one.

## Context

The chapter-review UI (phased Orient → Chapters flow with a right-rail chapter map) lives at `/reviews/{review_id}/human-review` and today is only reachable from a review-detail page — so it's gated on a PR having been routed to `human_review` by the pipeline.

The user wants a new **"Browse PR"** entry in the left nav that opens the same chapter view for any GitHub or ADO PR URL:
- If the PR has an existing review in the DB → load it with findings (same as today's experience).
- If not → render the chapter view with empty findings (pure navigation aid for large PRs).

Clarifications from the user:
- No new AI review is kicked off; chapters-only for PRs we don't have a review for.
- The URL-input form renders in the main content area to the right of the nav (not a modal).
- Nav label is **"Browse PR"**.

## Approach

Extend the existing `human_review.html` page to support an ad-hoc mode driven by `?pr=<url>` instead of duplicating ~500 lines of chapter CSS/JS. Add a thin landing page (`browse_pr.html`) with a URL input. The `/review-mode` route serves the **landing page** when there's no `?pr=` query param, and the **chapter viewer** when there is — so `human_review.html`'s existing bootstrap can take over for ad-hoc URLs with only minor edits. The `computeChapters` function at `human_review.html:320` is already pure and works with empty findings.

## Data-shape reference

Fields on `reviewData` that `human_review.html` actually reads (from grep — anything not listed can stay undefined):
- `agent_results` (array) — referenced at lines 300, 425, 464. Safe when empty.
- `decision` — line 437, looked up in an emoji map, falls back to `'🔍'`.
- `repo`, `pr_id`, `title` — line 439.
- `author`, `commits` — line 440, with fallbacks (`'unknown'`, `0`).
- `pr_url` — line 455, conditional.
- `summary` on each agent_result — line 464, filtered.

The ad-hoc `reviewData` stub needs at minimum: `agent_results: []`, `repo`, `pr_id`. Everything else is optional.

`ReviewRow` columns (from `src/pr_guardian/persistence/models.py:24-65`): `id`, `pr_id` (str), `repo` (str), `platform` (str), `started_at`. Use those names as-is.

## Implementation

### Step 1 — Storage helper

**File:** `src/pr_guardian/persistence/storage.py`

Insert after `get_review` (ends at line 176), before `list_reviews`:

```python
async def get_latest_review_by_pr(
    pr_id: str, repo: str, platform: str,
) -> dict[str, Any] | None:
    """Return the most recent review for a given PR (by pr_id + repo + platform), or None."""
    async with async_session() as session:
        q = (
            select(ReviewRow)
            .where(
                ReviewRow.pr_id == pr_id,
                ReviewRow.repo == repo,
                ReviewRow.platform == platform,
            )
            .order_by(ReviewRow.started_at.desc())
            .limit(1)
        )
        row = (await session.scalars(q)).first()
        return _review_to_dict(row) if row else None
```

### Step 2 — Backend endpoints

**File:** `src/pr_guardian/api/dashboard.py`

Add both endpoints after the existing `/reviews/{review_id}/diff` handler (ends at line 159).

```python
class ResolvePRRequest(BaseModel):
    pr_url: str


@router.post("/resolve-pr")
async def dashboard_resolve_pr(req: ResolvePRRequest):
    """Resolve a PR URL to an existing review (if any).

    Used by the "Browse PR" landing page to decide whether to open the
    full findings view or the ad-hoc chapters-only view.
    """
    from pr_guardian.api.review import _parse_pr_url

    stub, platform_name = _parse_pr_url(req.pr_url)  # raises 400 on bad URL
    existing = await storage.get_latest_review_by_pr(
        pr_id=stub.pr_id, repo=stub.repo, platform=platform_name,
    )
    if existing:
        return {"mode": "existing", "review_id": existing["id"]}
    return {"mode": "ad_hoc", "pr_url": req.pr_url}


@router.get("/pr-diff")
async def dashboard_pr_diff(pr_url: str = Query(..., description="GitHub or ADO PR URL")):
    """Fetch a PR diff by URL, no review record required.

    Used by the ad-hoc chapter viewer. Mirrors /reviews/{id}/diff but
    takes the PR URL directly instead of looking it up from a review row.
    """
    from pr_guardian.api.review import _parse_pr_url, _hydrate_pr
    from pr_guardian.platform.factory import create_adapter

    stub, platform_name = _parse_pr_url(pr_url)
    adapter = create_adapter(platform_name)

    try:
        pr = await _hydrate_pr(adapter, stub, platform_name)
    except Exception as e:
        raise HTTPException(422, f"Failed to fetch PR info: {e}")

    try:
        diff = await adapter.fetch_diff(pr)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch diff from platform: {e}")

    return {
        "pr_id": pr.pr_id,
        "repo": pr.repo,
        "pr_url": pr_url,
        "title": pr.title,
        "author": pr.author,
        "platform": platform_name,
        "files": [
            {
                "path": f.path,
                "status": f.status,
                "old_path": f.old_path,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch,
            }
            for f in diff.files
        ],
    }
```

`Query` is already imported at line 9. `BaseModel` is already imported at line 11. `storage` and `HTTPException` are already imported.

### Step 3 — Page route (serves landing OR chapter view)

**File:** `src/pr_guardian/api/dashboard_page.py`

Add near the other `_*_HTML` constants (around line 19-22):
```python
_BROWSE_PR_HTML = _DASHBOARD_DIR / "browse_pr.html"
```

Add a handler (place it near `reviews_page` at line 31):
```python
@router.get("/review-mode", response_class=HTMLResponse)
async def browse_pr_page(pr: str | None = None):
    """Serve the 'Browse PR' landing page, or the chapter viewer if ?pr=... is set.

    When ?pr is present we hand off to human_review.html, whose bootstrap
    detects the /review-mode path and fetches the diff from /api/dashboard/pr-diff.
    """
    if pr:
        return _HUMAN_REVIEW_HTML.read_text()
    return _BROWSE_PR_HTML.read_text()
```

### Step 4 — Landing page (new file)

**File:** `src/pr_guardian/dashboard/browse_pr.html`

Create with exactly this content:

```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PR Guardian — Browse PR</title>
<link rel="stylesheet" href="/static/styles.css">
</head>
<body class="bg-surface-overlay text-slate-50 min-h-screen font-sans antialiased">

<aside id="sidebar"></aside>
<script src="/static/sidebar.js"></script>

<div class="ml-16 lg:ml-64">

<header class="sticky top-0 z-20 flex items-center h-14 px-8 border-b border-slate-800 bg-slate-900/80 backdrop-blur-md">
  <span class="font-semibold text-slate-50 text-sm">Browse PR</span>
</header>

<main class="max-w-2xl mx-auto px-6 py-16">
  <div class="card">
    <div class="card-body space-y-4">
      <div>
        <h1 class="text-lg font-bold text-slate-50">Open any PR in review mode</h1>
        <p class="text-sm text-slate-400 mt-1">
          Paste a GitHub or Azure DevOps PR URL. You'll get the chapter-by-chapter
          reading view — useful for large PRs. If the PR has been AI-reviewed,
          findings are loaded automatically.
        </p>
      </div>

      <form id="browse-form" class="space-y-3" onsubmit="event.preventDefault(); submitUrl()">
        <input
          id="pr-url"
          type="url"
          required
          autocomplete="off"
          placeholder="https://github.com/owner/repo/pull/123"
          class="form-input w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-200"
        />
        <div class="flex items-center justify-between gap-3">
          <div id="browse-error" class="text-xs text-red-400"></div>
          <button type="submit" id="browse-submit" class="btn btn-primary px-6">Open</button>
        </div>
      </form>

      <div id="browse-recent-wrap" style="display:none" class="pt-4 border-t border-slate-800">
        <div class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Recent</div>
        <div id="browse-recent" class="space-y-1"></div>
      </div>
    </div>
  </div>
</main>

</div>

<script>
const RECENT_KEY = 'prg:browse-pr:recent';
function loadRecent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; } }
function pushRecent(url) {
  const list = [url, ...loadRecent().filter(u => u !== url)].slice(0, 5);
  localStorage.setItem(RECENT_KEY, JSON.stringify(list));
}
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function renderRecent() {
  const list = loadRecent();
  if (!list.length) return;
  document.getElementById('browse-recent-wrap').style.display = '';
  document.getElementById('browse-recent').innerHTML = list.map(u =>
    `<button type="button" class="block w-full text-left text-xs text-slate-400 hover:text-slate-200 font-mono truncate py-1"
            onclick="document.getElementById('pr-url').value=${JSON.stringify(u)};submitUrl();">${esc(u)}</button>`
  ).join('');
}

async function submitUrl() {
  const input = document.getElementById('pr-url');
  const errEl = document.getElementById('browse-error');
  const btn   = document.getElementById('browse-submit');
  const url   = input.value.trim();
  errEl.textContent = '';
  if (!url) { errEl.textContent = 'Enter a PR URL.'; return; }

  btn.disabled = true; btn.textContent = 'Opening…';
  try {
    const resp = await fetch('/api/dashboard/resolve-pr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pr_url: url }),
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    pushRecent(url);
    if (data.mode === 'existing') {
      location.href = `/reviews/${data.review_id}/human-review`;
    } else {
      location.href = `/review-mode?pr=${encodeURIComponent(url)}`;
    }
  } catch (e) {
    errEl.textContent = String(e.message || e);
    btn.disabled = false; btn.textContent = 'Open';
  }
}
renderRecent();
</script>
</body>
</html>
```

### Step 5 — Teach `human_review.html` about ad-hoc mode

**File:** `src/pr_guardian/dashboard/human_review.html`

Three surgical edits. No other `REVIEW_ID` references exist in the file (confirmed via grep — only the lines touched below).

**Edit A — line 244** — replace the single `REVIEW_ID` const with mode detection.

Replace:
```js
const REVIEW_ID = location.pathname.split('/reviews/')[1]?.split('/')[0] || '';
```

With:
```js
const URL_PATH = location.pathname;
const QS = new URLSearchParams(location.search);
const REVIEW_ID = URL_PATH.startsWith('/reviews/') ? (URL_PATH.split('/reviews/')[1]?.split('/')[0] || '') : '';
const AD_HOC_PR_URL = URL_PATH === '/review-mode' ? QS.get('pr') : null;
```

**Edit B — lines 256-274** — replace the `init()` body with a mode-aware bootstrap.

Replace:
```js
async function init() {
  if (!REVIEW_ID) { showError('No review ID in URL.'); return; }

  let [reviewResp, diffResp] = await Promise.allSettled([
    fetch(`/api/dashboard/reviews/${REVIEW_ID}`),
    fetch(`/api/dashboard/reviews/${REVIEW_ID}/diff`),
  ]);

  if (reviewResp.status === 'rejected') { showError('Failed to load review: ' + reviewResp.reason); return; }
  const reviewJson = await reviewResp.value.json();
  if (reviewJson.error) { showError(reviewJson.error); return; }
  reviewData = reviewJson;

  if (diffResp.status === 'fulfilled' && diffResp.value.ok) {
    diffData = await diffResp.value.json();
  }

  buildOrientScreen();
}
```

With:
```js
async function init() {
  if (REVIEW_ID) {
    const [reviewResp, diffResp] = await Promise.allSettled([
      fetch(`/api/dashboard/reviews/${REVIEW_ID}`),
      fetch(`/api/dashboard/reviews/${REVIEW_ID}/diff`),
    ]);
    if (reviewResp.status === 'rejected') { showError('Failed to load review: ' + reviewResp.reason); return; }
    const reviewJson = await reviewResp.value.json();
    if (reviewJson.error) { showError(reviewJson.error); return; }
    reviewData = reviewJson;
    if (diffResp.status === 'fulfilled' && diffResp.value.ok) {
      diffData = await diffResp.value.json();
    }
  } else if (AD_HOC_PR_URL) {
    let resp;
    try {
      resp = await fetch(`/api/dashboard/pr-diff?pr_url=${encodeURIComponent(AD_HOC_PR_URL)}`);
    } catch (e) {
      showError('Failed to fetch PR: ' + e); return;
    }
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      showError(detail.detail || `Failed to load PR (HTTP ${resp.status})`);
      return;
    }
    const data = await resp.json();
    diffData = { files: data.files };
    reviewData = {
      agent_results: [],
      decision: null,
      repo: data.repo,
      pr_id: data.pr_id,
      pr_url: data.pr_url,
      title: data.title,
      author: data.author,
    };
  } else {
    showError('No review ID or PR URL provided.');
    return;
  }
  buildOrientScreen();
}
```

**Edit C — lines 459-461** — guard the back-links against missing `REVIEW_ID`.

Replace:
```js
document.getElementById('hdr-review-link').href = `/reviews/${REVIEW_ID}`;
document.getElementById('orient-back-link').href = `/reviews/${REVIEW_ID}`;
document.getElementById('done-back-link').href   = `/reviews/${REVIEW_ID}`;
```

With:
```js
const backHref = REVIEW_ID ? `/reviews/${REVIEW_ID}` : '/review-mode';
document.getElementById('hdr-review-link').href = backHref;
document.getElementById('orient-back-link').href = backHref;
document.getElementById('done-back-link').href   = backHref;
if (!REVIEW_ID) {
  document.getElementById('orient-back-link').textContent = '← Back to Browse PR';
  document.getElementById('done-back-link').textContent   = 'Back to Browse PR';
  document.getElementById('hdr-review-link').textContent  = 'Browse PR';
}
```

### Step 6 — Nav entry

**File:** `src/pr_guardian/dashboard/static/sidebar.js`

Insert into the `NAV` array (currently lines 11-21), between the `Reviews` entry (line 13) and `Scans` (line 14):

```js
    { name: 'Browse PR',    url: '/review-mode',   icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"/>' },
```

(Heroicon `book-open`, 1.5 stroke — matches existing entries.)

## Files touched

| File | Change |
|---|---|
| `src/pr_guardian/persistence/storage.py` | Add `get_latest_review_by_pr` |
| `src/pr_guardian/api/dashboard.py` | Add `POST /api/dashboard/resolve-pr` + `GET /api/dashboard/pr-diff` |
| `src/pr_guardian/api/dashboard_page.py` | Add `/review-mode` handler (serves landing OR chapter view) |
| `src/pr_guardian/dashboard/browse_pr.html` | **New** |
| `src/pr_guardian/dashboard/human_review.html` | Mode-aware bootstrap (3 edits in Step 5) |
| `src/pr_guardian/dashboard/static/sidebar.js` | Add "Browse PR" NAV entry |

## Verification

Run in order; stop and diagnose on any failure.

1. **Imports:**
   ```
   python -c "from pr_guardian.api import dashboard, dashboard_page; from pr_guardian.persistence import storage; print('ok')"
   ```

2. **Tests:**
   ```
   python -m pytest -x -q
   ```

3. **Boot:**
   ```
   bash scripts/agent-serve.sh
   ```
   Then:
   - `curl -sS http://localhost:8000/review-mode | head -20` → landing HTML (title "Browse PR").
   - Browse to `http://localhost:8000/review-mode`. "Browse PR" nav item active; URL input visible; no chapter UI.

4. **Existing-review path** (find a seeded `pr_url` via `curl -sS http://localhost:8000/api/dashboard/reviews | head -40`):
   Paste it → Open → redirects to `/reviews/{id}/human-review` and shows findings exactly as before.

5. **Ad-hoc path:** paste a public GitHub PR URL (e.g. `https://github.com/fastapi/fastapi/pull/12345`) → Open → lands on `/review-mode?pr=...` → chapter UI renders, severity tiles show 0, file list and chapters populate from the diff. Public PRs work unauthenticated but may hit rate limits; set `GITHUB_TOKEN` for private repos or to avoid throttling.

6. **Invalid URL:** paste `not-a-url` → `/resolve-pr` returns 400 → inline error in the form (from `_parse_pr_url`).

7. **Regression:** open any existing `/reviews/{id}/human-review` and confirm it still renders findings and behaves identically to before.

## Out of scope

- No DB migration (only new read queries).
- No new automated tests required; verify manually. Add a unit test for `get_latest_review_by_pr` only if it's low-cost given existing fixtures.
- Do not extract the chapter JS into a shared module — reuse is via `human_review.html` itself.
- Do not modify the dismissal flow — in ad-hoc mode there are no findings, so no dismiss buttons render.
