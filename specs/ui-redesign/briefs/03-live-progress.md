# Brief 03 — Live progress page

## What
Build `/reviews/{id}/live` — a single page that streams the Guardian pipeline run for a review in progress. Pipeline stages tick off live (Discovery → Mechanical → Triage → Agents → Decision), the page is shareable, and on completion it auto-redirects to `/reviews/{id}` in the user's preferred viewer mode.

## Why
Triggering a fresh review takes minutes. Today, the user has no good place to wait — `/dashboard` shows "active reviews" as a tiny list item, and there's no per-review live view. A shareable, progressive page makes the wait useful (and makes "I started a review, look" a one-link share).

## Where
- New: `src/pr_guardian/dashboard/live_progress.html`.
- `src/pr_guardian/api/dashboard_page.py` — `/reviews/{id}/live` route.
- `src/pr_guardian/api/dashboard.py` — the existing `/api/dashboard/events` SSE stream already publishes per-review events. Filter on the client side by `review_id`, or add a `?review_id=` query param to the server endpoint and filter at source. Pick the lighter change.

## SSE event handling
Existing event shape (confirm in `dashboard.py`):
```json
{ "type": "stage_update", "review_id": "...", "stage": "agents", "agent": "security", "status": "running" | "complete" | "error", "duration_ms": 12340, "findings_count": 3 }
```
The page listens, builds up the stage list incrementally. On `{stage: "complete", status: "complete"}` for the whole review, transition to the redirect.

## Stage rendering
```
Discovery     ✓  detected 51 files, 3 languages              1.2s
Mechanical    ✓  semgrep · gitleaks · deps · migrations      18s
Triage        ✓  HIGH risk · 6 agents selected               0.4s
Agents        ●  4 of 6 running
  Security    ✓  3 findings                                  12s
  Performance ✓  1 finding                                   9s
  Architecture ● running...
  Hotspot     ● running...
  Code Quality  · queued
  Test Quality  · queued
Decision      · waiting
```

- States per stage: `queued` (slate dot), `running` (pulsing indigo dot), `complete` (emerald check), `error` (red ×).
- Agent sub-stages render only when `stage == agents`.
- Summary line per stage shows what completed (count of files, gates passed, findings found).
- Total elapsed time updates every second.

## Controls
- **Share link** button copies the current URL.
- **Cancel review** button calls `DELETE /api/dashboard/reviews/{id}` (existing endpoint). Returns to `/reviews` on success.

## Completion behaviour
- On `decision` stage `complete`:
  - 200ms fade of the pipeline column.
  - Redirect to `/reviews/{id}` with user's preferred mode (or default heuristic).
  - If the user navigated away, no redirect — they'll see the completed review next time they open the queue.

## Error behaviour
- A stage hitting `status: error`:
  - Stage row goes red with the error message.
  - Pipeline halts; subsequent stages stay `queued`.
  - `[Re-run]` button appears, POSTs to `/api/reviews/{id}/rerun`.
  - URL stays valid so the error is shareable.

## Visual polish
- Mono font for stage names + timings (tabular nums).
- Sans for summary text.
- Pulsing dot for `running` (existing CSS animation; reuse).
- Single column, max width 720px, vertically anchored ~120px from top.

## Success signal
- Triggering a review from `/reviews` redirects here.
- Stages appear and tick over live as the pipeline progresses.
- URL is copy-shareable; opening it in a second browser shows the same stream.
- On completion, redirects to `/reviews/{id}`.
- On a stage error, stops cleanly with a re-run option.

## Non-goals
- Building the review viewer (brief 04).
- Push notifications when the review completes — the queue + this page are enough for now.
- A "review history" sidebar showing prior runs of the same PR — that lives on the review detail page already; not duplicated here.

## Validation
1. Trigger a review via the queue paste bar.
2. Land on `/reviews/{id}/live`.
3. Watch stages tick over.
4. Share the URL to a second browser tab; both see the same state.
5. On completion, redirect to `/reviews/{id}` in the default mode.
6. Simulate an agent failure (stub a model error) — see the failed stage in red and the re-run button.
