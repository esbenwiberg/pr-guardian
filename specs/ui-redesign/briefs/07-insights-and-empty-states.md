# Brief 07 — Insights + empty-state pass

## What
Rename `/dashboard` to `/insights`. The content is what `index.html` already renders — stat cards, decision donut, risk-tier bars, severity bars, top repos, last-20 trend strip — but it stops pretending to be the landing page. Add a cost-over-time line chart (data exists; just unrendered). Do a polish pass on every empty state across the app so they tell the user what to do next.

## Why
Today `/dashboard` is what users land on, full of zeros and "No data" boxes when there are no reviews yet — the worst possible first impression. Moving it to `/insights` makes it a deliberate destination for team leads / engineering managers who want trend data. Empty states are an afterthought across the app; they should be a real surface treatment.

## Where
- Rename: `src/pr_guardian/dashboard/index.html` → `src/pr_guardian/dashboard/insights.html`.
- `src/pr_guardian/api/dashboard_page.py` — `/insights` serves it; `/dashboard` → `/insights` redirect (handled in brief 01).
- `src/pr_guardian/api/dashboard.py` — confirm `/api/dashboard/stats` returns cost-over-time series (it returns aggregated cost; check whether per-day breakdown exists, add if needed).
- Empty-state CSS class `empty-state` exists in `styles.css` — extend it, don't rewrite.

## Insights content
Keep all existing widgets. Add:

**Cost over time** (new)
```
Cost over time · last 30 days
$  │     ▁▂▂▃▅▄▃▅▆▇▇▆█▆▅▄▃▂▁▂▃▂▁ ▁▁
   └─────────────────────────────────
   30d ago                       today
   Total: $4.82  ·  per-review avg: $0.04  ·  trending ↓ 12%
```

Single line chart, indigo accent, no fancy axes. Tooltip on hover. Uses CSS or inline SVG — no chart library.

## Empty states (every page)

| Where | Old | New |
|---|---|---|
| `/reviews` queue, no reviews | "No reviews found" | "Nothing waiting for review. Paste a PR URL above to trigger one." Shield icon. |
| `/reviews` queue, filter empty | (nothing) | "No reviews match these filters. [Clear filters]" |
| `/insights`, no data | "No data" in widgets | Page-level callout above widgets: "No reviews yet — once Guardian processes PRs, decision and risk metrics will appear here. [Trigger your first review →]" |
| `/insights`, individual widget no data | "No data" | "—" (single dash, slate). Don't shout at the user; the page-level callout covers the why. |
| `/settings#api-keys`, no keys | "Failed to load keys." (current!) | "No API keys yet. Create one to let CI bots and agents call Guardian." [Agent API explainer card] |
| `/settings#admins`, no admins | "Failed to load admins." | "Only you so far. Add admins by email (must match their Entra ID login)." |
| `/settings#pats`, no PATs | "No PATs configured. The GITHUB_TOKEN environment variable will be used as fallback." (keep — this one's good) | (unchanged — it's already helpful) |
| Live progress page, error | (red text, vague) | Stage row in red with the actual error message + "[Re-run]" button. (Already in brief 03.) |
| Wrap-up post-back failure | (toast, vague) | Inline error box with platform message + retry button. (Already in brief 05.) |

## Visual polish — Insights
- Stat cards: reduce font weight of values from 700 → 600. Add `text-slate-300` for label. Tighter line-height.
- Donut: drop border thickness from 12px → 8px. Center value 24px semibold.
- Bar rows: row height 32px → 28px. Bar fill 18px → 14px. Lighter background track.
- Top repos: max 5 rows visible; "+N more" link expands.
- Last-20 trend strip: keep as-is; it's already well-designed.

## Success signal
- `/insights` exists and renders the analytics that used to live on `/dashboard`.
- `/dashboard` redirects to `/insights` (brief 01).
- Cost-over-time chart renders with real data after a few reviews.
- Every empty state across the app has a real CTA, not a dead-end.

## Non-goals
- Major chart library adoption (Chart.js, etc.). Native CSS/SVG is enough for this footprint.
- New metrics. Cost, decision, risk, severity, repo — these are the existing five.
- Date-range picker on Insights. Last 30 days is fine for v1.
- Per-team or per-author filters on Insights. Future.

## Validation
1. Open `/insights` — same widgets as today's `/dashboard`, plus cost-over-time.
2. Empty state on a fresh install: page-level callout shows, no dead "0" cards alone.
3. Visit every empty surface in the app — none say just "No data" or "Failed to load".
4. `/dashboard` → 302 → `/insights`.
