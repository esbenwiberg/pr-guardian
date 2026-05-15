# Brief 02 — Reviews queue + paste-URL trigger

## What
Build `/reviews` — the new app root. Unified queue listing PR reviews and repo scans together. Paste-URL bar pinned to the top kicks off a new review and redirects to `/reviews/{id}/live`. Filter chips (All / Needs review / Mine / Scans) and faceted filters (repo, author, risk). Trigger-origin chip per row. Stale-review badge when an auto re-review has occurred.

## Why
This is the landing page reviewers use every day. The current `/dashboard` + `/pr-dashboard` + `/browse-pr` + `/scans` + `/reviews` split forces a navigation choice they shouldn't have to make.

## Where
- New: `src/pr_guardian/dashboard/reviews_queue.html` (replaces what `index.html` did as the landing surface).
- `src/pr_guardian/api/dashboard_page.py` — `/reviews` GET serves this template.
- `src/pr_guardian/api/pr_dashboard_api.py` — extend the existing list endpoint to return unified items (PR + scan) with `trigger_origin` and `stale` flags. Reuse, don't rebuild.
- Existing data: `pr_dashboard.html` and `reviews.html` both query `/api/pr-dashboard/*`. Pick the richer endpoint and merge what each adds. The new queue endpoint is `/api/reviews/queue` and returns the unified shape below.

## Queue row data shape
```ts
{
  id: string,                            // review id
  subject_type: "pr" | "scan",
  platform: "github" | "ado" | null,    // null for scan
  title: string,                         // PR title or scan name
  repo: string,                          // owner/name
  author: string | null,                 // PR author; null for scheduled scans
  branch: string | null,
  decision: "human_review" | "auto_approve" | "reject" | "hard_block" | "pending",
  risk_tier: "trivial" | "low" | "medium" | "high",
  findings: { high: number, medium: number, low: number, critical: number },
  estimated_review_minutes: number,
  files_changed: number,
  trigger_origin: "webhook" | "manual" | "scan",
  triggered_by: string | null,           // user email/name when manual
  stale: boolean,                        // true when auto re-review happened since last viewed
  started_at: string,                    // ISO
}
```

## Paste-URL behaviour
- Input accepts:
  - GitHub PR URL: `https://github.com/{owner}/{repo}/pull/{n}`
  - ADO PR URL: `https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{n}`
  - `owner/repo` shortform → triggers a repo scan
- Validate client-side, then `POST /api/reviews/trigger` with `{url, mode: "pr" | "scan"}`.
- Server returns `{id, status: "queued"}`. Redirect to `/reviews/{id}/live`.
- On unsupported platform / unauthenticated repo: inline error under the input, no navigation.

## Filter chips
```
[All] [Needs review] [Mine] [Scans]   repo:▾ author:▾ risk:▾
```
- `All` (default) — everything not closed.
- `Needs review` — `decision = human_review` AND not yet completed by a reviewer.
- `Mine` — `author == current_user.email` OR `triggered_by == current_user.email`.
- `Scans` — `subject_type == "scan"`.
- Faceted filters are dropdown multi-select. Active filters render as removable pills.

State is reflected in the URL (`?filter=needs_review&repo=demo/api`) so links are shareable.

## Stale badge
Backend already has the data — `pr_dashboard_api.py` tracks last-known commit SHA. When `current_head_sha != reviewed_head_sha` AND an auto re-review has fired, set `stale: true` on the row. UI shows a `⚠ updated` chip; clicking the row opens the new review (not the old one).

## Visual polish
- Row height 72px (title + subtitle).
- Subject icon column 28px wide; platform sub-chip `GH` / `ADO` 20px wide.
- Risk dots: 0–4 dots reflecting `risk_tier`, coloured per existing scheme.
- Trigger origin chip 11px text: `auto` (slate), `manual` (indigo), `scan` (slate), `you triggered` (indigo) when `triggered_by == current_user`.
- Hover row → background tint, no border change.
- Click anywhere except an action chip → navigate to `/reviews/{id}`.

## Empty states
- Queue empty (no reviews at all): "Nothing waiting for review. Paste a PR URL above to trigger one." Centered, shield icon, 200px from top.
- Filter empty: "No reviews match these filters. [Clear filters]"

## Success signal
- Visiting `/reviews` shows the queue + paste bar.
- Pasting a valid GitHub PR URL kicks off a new review and lands on `/reviews/{id}/live`.
- Filter chips work and reflect in URL.
- Repo scans appear in the same queue, distinguished by icon + chip.
- Stale badge appears on a row whose underlying PR got new commits since the original review.

## Non-goals
- Building the live progress page (brief 03).
- Building the viewer (brief 04).
- Personal "mine-only" as the default — `All` is default; `Mine` is a chip.
- Sorting controls. Default sort = stale-first, then newest-first.
- Pagination — top 100 rows, scroll. Pagination only if perf suffers.

## Validation
1. Seed demo data (existing `scripts/agent-serve.sh` does this when Postgres is available).
2. `/reviews` shows ≥3 rows of mixed types after seed.
3. Paste a fake PR URL — inline error appears.
4. Paste a valid configured PR URL — redirects to live page.
5. Filter to `Scans` — only scan rows visible; URL shows `?filter=scans`.
