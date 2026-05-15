# UI Redesign — Design

## Decisions locked

| # | Topic | Decision |
|---|---|---|
| 1 | Primary user | Human reviewer on flagged PRs. Secondary: trigger Guardian on a self/teammate PR. |
| 2 | Landing | Queue-first; paste-URL bar pinned at top of `/reviews`. |
| 3 | Review viewer | Smart default + 3-tab switcher: Wizard · Chapters · Findings. State preserved on flip. |
| 4 | Queue model | Unified: PR reviews and repo scans live in the same queue. Filter chip distinguishes. |
| 5 | Identity | Authenticated. Queue is team-wide (no personal-mine filter as primary). |
| 6 | Nav | Aggressive cut: **Reviews · Insights · Settings (admin-only) · Help-menu**. 11 → 4. |
| 7 | Wrap-up | Posts inline comments + final verdict to GitHub/ADO via `inline-pr-comments`. |
| 8 | Trigger wait | Live progress page; SSE pipeline stream; shareable URL. |
| 9 | Aesthetic | Linear/Vercel — dark, tighter type, fewer borders, briefing-style prose. |
| 10 | Repo scans | Treat as full snapshot — every file = "added". Chapters group by directory/layer. |
| 11 | Trigger origin | Visually distinct via subtle chip per row. Filterable. |
| 12 | Stale reviews | Auto re-review on new commits; queue row updates with a badge. |

## Sitemap

```
/reviews             Queue + paste-URL trigger (the new root, default landing)
/reviews/{id}        Review viewer (Wizard | Chapters | Findings — mode in querystring or local pref)
/reviews/{id}/live   Live progress page during a fresh manual review run
/insights            Analytics (charts that used to live on /dashboard)
/settings            Admin-only — Prompts, LLM provider, API keys, PATs, Exclusions, Admins
/help                Footer menu opening How It Works / CLI Ref / API Ref (no top-level nav slot)
```

Redirects:
- `/` → `/reviews`
- `/dashboard` → `/insights`
- `/pr-dashboard` → `/reviews`
- `/browse-pr` → `/reviews` (paste box is inline)
- `/scans` → `/reviews?subject=repo`
- `/scans/{id}` → `/reviews/{id}`
- `/prompts` → `/settings#prompts`
- `/admin` → `/settings#admin`
- `/how-it-works` → `/help/how-it-works` (kept under help, not sidebar)
- `/cli-reference` → `/help/cli`
- `/api-reference` → `/help/api`
- `/reviews/{id}/wizard` → `/reviews/{id}?mode=wizard`
- `/reviews/{id}/human-review` → `/reviews/{id}?mode=chapters`

## Navigation

```
┌──────────────────┐
│  ⛨ PR Guardian   │
│  ── ── ── ── ──  │
│  ◉ Reviews       │   ← default landing, always shown
│  ▦ Insights      │   ← always shown
│  ⚙ Settings      │   ← admin-only; gated by identity scope
│  ── ── ── ── ──  │
│  Footer:         │
│  · Search (⌘K)   │   ← command palette (existing, kept)
│  · Help ▾        │   ← popover: How It Works, CLI Ref, API Ref
│  · Status dot    │   ← SSE connection status
└──────────────────┘
```

Sidebar collapses to icon-only below `lg`. The "Agent API" promo banner that lives in the sidebar today moves into Settings/API Keys empty state — it's a feature discovery cue, not a navigation item.

## Surface 1 — Reviews (the new root)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Reviews                                                                       │
├────────────────────────────────────────────────────────────────────────────────┤
│  🔍  Paste a PR URL...                                            [Review →]  │
│  ────────────────────────────────────────────────────────────────────────────  │
│  [All] [Needs review] [Mine] [Scans]    repo:▾   author:▾   risk:▾    ⟳ Sync  │
│                                                                                │
│  PR  #482  feat/auth-refactor                          ●● 3 high · ~40m  →    │
│  GH  demo/api · alice · 12m ago                  auto · ⚠ updated · 51 files  │
│  ────────────────────────────────────────────────────────────────────────────  │
│  PR  #481  fix/n-plus-one                              ●  1 med · ~6m   →    │
│  GH  demo/api · bob · 1h ago                                    auto · 3 files│
│  ────────────────────────────────────────────────────────────────────────────  │
│  📁  scan/legacy-billing                               ●● 4 high · ~25m  →    │
│  ADO demo/legacy · scheduled · 4h ago                              scan · full│
│  ────────────────────────────────────────────────────────────────────────────  │
│  PR  #479  refactor/billing-job                        ·  clean         →    │
│  GH  demo/api · carol · 6h ago                       manual · you triggered   │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Row anatomy:**
- Subject icon: `PR` chip (with platform `GH` / `ADO` sub-chip) or `📁` for repo scan.
- Title — branch/PR title for PRs; scan name for repo scans.
- Risk dots + finding summary (mirrors today's `stat-card-value` colour scheme).
- Trigger-origin chip: `auto` (webhook) / `manual` (paste) / `scan` (scheduled) / `you triggered` when self-triggered.
- Stale signal: `⚠ updated` badge when new commits landed and an auto re-review fired — clicking opens the new run.
- Click anywhere on the row → `/reviews/{id}` opens in the user's last viewer mode.

**Filters** (default = all):
- `Needs review` (decision = `human_review` and undecided)
- `Mine` (author = me OR I triggered)
- `Scans` (subject = repo)
- Faceted: repo, author, risk tier
- Empty queue state: "Nothing waiting for review. Paste a PR URL above to trigger a fresh review."

**Paste-a-URL bar:**
- Single input. Accepts GitHub PR URL, ADO PR URL, or `owner/repo` (triggers a repo scan).
- `Review →` button validates platform, kicks off a new review, navigates to `/reviews/{id}/live`.
- Inline error if URL is malformed or platform isn't configured.

## Surface 2 — Live progress page

Path: `/reviews/{id}/live`. Auto-redirects to `/reviews/{id}` on completion.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  ← Reviews    PR #482 · feat/auth-refactor · running                          │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  Discovery     ✓  detected 51 files, 3 languages              1.2s             │
│  Mechanical    ✓  semgrep · gitleaks · deps · migrations      18s              │
│  Triage        ✓  HIGH risk · 6 agents selected               0.4s             │
│  Agents        ●  4 of 6 running                                               │
│    Security    ✓  3 findings                                  12s              │
│    Performance ✓  1 finding                                   9s               │
│    Architecture ● running...                                                   │
│    Hotspot     ● running...                                                    │
│    Code Quality  · queued                                                      │
│    Test Quality  · queued                                                      │
│  Decision      · waiting                                                       │
│                                                                                │
│  Total elapsed: 42s        Share link [📋]      [Cancel review]              │
└────────────────────────────────────────────────────────────────────────────────┘
```

- Reuses the existing SSE stream (`/api/dashboard/events` → filtered to this review id).
- Each agent ticks off independently — uses the parallel-fan-out info already in event payloads.
- URL is shareable; another reviewer can land on `/reviews/{id}/live` and see the same stream.
- On `complete`: 200ms fade, then redirect to `/reviews/{id}` in the user's preferred viewer mode.
- On `error`: pipeline pane shows the failed stage in red with the error message and a `Re-run` button.

## Surface 3 — Review viewer (3-tab)

Path: `/reviews/{id}?mode=wizard|chapters|findings`. Mode persists in `localStorage` after first explicit pick.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  ← Reviews   PR #482 · feat/auth-refactor   ⚠ Human review · score 7.2 · HIGH │
│              [ Wizard | Chapters | Findings ]              [Open in GitHub →]  │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│   ... viewer body for the selected mode ...                                    │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Mode-pick default heuristic** (when no user preference is stored):
- `findings_count ≤ 2 AND capabilities ≤ 1` → **Findings**
- `findings_count ≤ 10 AND files ≤ 20` → **Chapters**
- otherwise (or any repo scan) → **Wizard**

State preserved across flips: scroll position, decisions made (Accept/Fix/Dismiss), comment-to-author draft. Switching mode does not lose progress.

### 3a · Wizard mode

Adopted from `human_wizard.html`. Briefing → Capability 1..N → Wrap-up.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  ● Briefing — ● Cap.1 — ◐ Cap.2 — ○ Cap.3 — ○ Cap.4 — ○ Wrap                  │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  CAPABILITY 2  ·  Token signing                                                │
│  Refactors JWT signing path. 2 concerns flagged.                               │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │ ● Hardcoded KEY on line 24                            [HIGH] [CWE-798]   │ │
│  │   Is this loaded from env at runtime or a literal? See evidence.         │ │
│  │   ▸ Evidence   ▸ Suggested fix                                           │ │
│  │   [ ✓ Accept ]  [ ✎ Fix ]  [ — Dismiss ]                                 │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │ ● Token TTL reduced from 24h → 15m                    [MEDIUM]           │ │
│  │   Intentional or regression? Worth a comment to author.                  │ │
│  │   ▸ Diff   ▸ Suggested fix                                               │ │
│  │   [ ✓ Accept ]  [ ✎ Fix ]  [ — Dismiss ]                                 │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│                                            [ ← prev cap. ]   [ next cap. → ]  │
└────────────────────────────────────────────────────────────────────────────────┘
```

Keyboard:
- `j/k` next/prev concern
- `a` accept, `f` fix, `d` dismiss
- `←/→` prev/next capability
- `g w` jump to wrap-up

### 3b · Chapters mode

Adopted from `human_review.html`. Single scrolling page, AI-grouped chapters, inline finding callouts on diff hunks. Right-rail chapter map (kept).

### 3c · Findings mode

Adopted from `review_detail.html`. Flat list grouped by AI agent. Compact, no inline diff. Used for tiny PRs and engineering audits.

### Wrap-up step (Wizard only — other modes have a `Finish review` button that opens the same panel)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Wrap-up · PR #482                                                             │
├────────────────────────────────────────────────────────────────────────────────┤
│  Decisions                                                                     │
│    ✓ 4 accepted (silent)                                                       │
│    ✎ 2 fix requested  →  posted as inline comments on the PR                   │
│    — 1 dismissed (logged in audit)                                             │
│                                                                                │
│  Comment to author (final summary):                                            │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │ Solid refactor overall. Two concerns to address before merge:            │ │
│  │  - Hardcoded KEY on token_service.py:24                                  │ │
│  │  - Token TTL change — was this intentional?                              │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  Verdict:   [ ✓ Approve ]   [ ⟳ Request changes ]   [ ⊘ Block ]               │
│                                                                                │
│  Inline-comment mode for this post: ● Inline (default) ○ Summary only ○ None  │
│  [ Post to GitHub →  ]                                                         │
└────────────────────────────────────────────────────────────────────────────────┘
```

- Reuses the `inline-pr-comments` posting pipeline.
- Inline-comment mode toggle on this page overrides the per-review default for this post only.
- After posting: success toast, redirect to the next item in the queue.

## Surface 4 — Insights

The current `/dashboard` content, but no longer pretending to be the home. Stat cards, donut, risk-tier bars, severity bars, top repos, last-20 trend strip. Plus a new "cost over time" line chart (data already exists; just unrendered).

No new behaviour. This is a relocation + slight polish, not a redesign.

## Surface 5 — Settings (admin-only)

Single page with anchored sections (the URL fragments support direct deep-links from redirects):

```
/settings#llm           LLM provider — current /settings page content
/settings#prompts       Agent prompt editor — current /prompts page content
/settings#api-keys      Agent API keys — current admin.html section
/settings#admins        Admin allowlist — current admin.html section
/settings#pats          GitHub PATs — current admin.html section
/settings#exclusions    Org/repo exclusions — current admin.html section
```

Identity guard: redirect to `/reviews` with a toast if user is not an admin. No "Settings" nav item shown to non-admins.

## Surface 6 — Help (footer popover, not a page)

Click "Help ▾" in sidebar footer → small popover lists How It Works · CLI Reference · API Reference. Each opens its existing page. The pages stay as-is for now; they're documentation, not product surfaces.

## Empty states

Every surface gets a real empty state, not "No reviews found":

| Surface | State | Treatment |
|---|---|---|
| Reviews | Queue empty | "Nothing waiting for review. Paste a PR URL above to trigger one." Illustrated shield icon. |
| Reviews | Filter empty | "No reviews match these filters. [Clear filters]" |
| Insights | No data | "No reviews yet — once Guardian processes PRs, decision and risk metrics will appear here." |
| Settings | No API keys | The Agent API discovery card moves here, full size. |

## Visual direction

- Type: keep ui-sans, but tighter base size (14px body, 12.5px secondary). Add an `xl` step for wizard step titles (28px, semibold, -0.01em tracking — matches the `human_wizard.html` prototype).
- Borders: remove ~half of the divider lines. Use whitespace + background tint to separate sections.
- Accent: single indigo (`--accent-400`) for primary actions and active states. Status colours (emerald/orange/red) only for verdict states.
- Density: row height 56px (queue), 72px (review row with subtitle). Current density is fine; just consistent.
- Wizard prose: 15px line-height 1.7, like a memo. Inline `code` chips, italic for emphasis.

## Implementation order (suggested briefs)

See `briefs/`. Each is a single-pod chunk:

1. **01-routing-and-redirects.md** — collapse the nav surface, set up redirects, gate `/settings` by admin, render new sidebar shell.
2. **02-reviews-queue.md** — build the unified queue with paste-URL trigger, filter chips, trigger-origin badges, stale badge.
3. **03-live-progress.md** — wire the SSE stream into the live progress page, share link, auto-redirect on complete.
4. **04-viewer-three-tab.md** — wrap the three existing viewer pages into a single `/reviews/{id}` surface with mode-switcher and default heuristic.
5. **05-wrap-up-and-postback.md** — wrap-up screen, decisions summary, comment-to-author editor, hook into `inline-pr-comments` posting.
6. **06-settings-consolidation.md** — fold `/prompts`, `/admin`, `/settings` into a single anchored Settings page with admin gating.
7. **07-insights-and-empty-states.md** — rename `/dashboard` → `/insights`, add cost-over-time chart, polish all empty states.

Aesthetic polish is folded into each brief — not a separate pass.
