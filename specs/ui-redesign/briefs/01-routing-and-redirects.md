# Brief 01 — Routing, redirects, and new nav shell

## What
Collapse the 11-item sidebar to four: **Reviews · Insights · Settings (admin-only) · Help (footer popover)**. Wire all old routes to redirect to the new ones. Render the new sidebar shell. No new content surfaces yet — this brief is pure plumbing + nav.

## Why
Every other brief in this spec assumes the new IA exists. Routing first means each subsequent brief just builds a page; it doesn't have to re-litigate nav.

## Where
- `src/pr_guardian/api/dashboard_page.py` — add redirect handlers; remove old route handlers that no longer have content (or repoint them).
- `src/pr_guardian/dashboard/static/sidebar.js` — rewrite the `NAV` array; add admin gating; add Help popover.
- `src/pr_guardian/dashboard/index.html` — repurpose as `/insights` content (move/rename to `insights.html`) OR keep filename and just change route mapping. Decide based on git diff cost; renaming is cleaner.
- `src/pr_guardian/auth/` — confirm there is an `is_admin(request)` helper. If not, add one that consults the existing admin allowlist. The redirect handler for `/settings` must use this.

## Routes table
| Old path | New path | Type |
|---|---|---|
| `/` | `/reviews` | 302 |
| `/dashboard` | `/insights` | 302 |
| `/pr-dashboard` | `/reviews` | 302 |
| `/browse-pr` | `/reviews` | 302 |
| `/scans` | `/reviews?subject=repo` | 302 |
| `/scans/{id}` | `/reviews/{id}` | 302 |
| `/prompts` | `/settings#prompts` | 302 |
| `/admin` | `/settings#admin` | 302 |
| `/how-it-works` | `/help/how-it-works` | rename |
| `/cli-reference` | `/help/cli` | rename |
| `/api-reference` | `/help/api` | rename |
| `/reviews/{id}/wizard` | `/reviews/{id}?mode=wizard` | 302 |
| `/reviews/{id}/human-review` | `/reviews/{id}?mode=chapters` | 302 |

Help routes (`/help/*`) stay as static HTML pages; they just don't have a sidebar nav item anymore.

## Sidebar (new)
```
PR Guardian  v0.1
─────────────────
◉ Reviews
▦ Insights
⚙ Settings              ← only when is_admin
─────────────────
🔍 Search    ⌘K
? Help ▾                ← popover: How It Works · CLI · API
● Connected             ← SSE status (existing)
```

Active-state detection in `sidebar.js` currently maps `/dashboard` → Dashboard tile; rewrite the `isActive` table for the new four items. The Help popover is its own small JS — list of `<a>` items.

## Admin gating
`/settings` server-side check:
- If `is_admin(request)` → serve the page.
- Else → redirect to `/reviews` with `?error=admin_required` (the queue page renders a one-shot toast that consumes the param).

`sidebar.js` receives admin status via a small injected JSON snippet (`window.__currentUser = {is_admin: true|false}`); only renders the Settings item when admin.

## Success signal
- Visiting any of the old paths in the table above resolves to its new path.
- Sidebar has exactly four primary slots (or three for non-admins).
- Help popover opens from sidebar footer, lists three items, no other behaviour.
- Non-admin user requesting `/settings` lands on `/reviews` with a "Settings is admin-only" toast.

## Non-goals
- Building actual `/reviews`, `/insights`, `/settings` content. Those are stub pages (`<h1>Reviews</h1>` + `<p>Coming in brief 02.</p>`) for this brief. Following briefs fill them in.
- Changing visual design (type scale, palette). That's per-surface polish in each later brief.
- Search / command palette changes — kept as-is.

## Validation
1. `python -m pytest` passes.
2. `bash scripts/agent-serve.sh` starts; `GUARDIAN_DEV_ADMIN=1` is admin.
3. Open `/dashboard` — lands on `/insights`.
4. Open `/scans/abc` — lands on `/reviews/abc`.
5. Toggle admin off (env var) — `/settings` redirects to `/reviews` with a toast; Settings item is hidden from sidebar.
