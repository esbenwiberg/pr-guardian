# UI Redesign

## Problem
The dashboard grew out of POC iteration. It has 11 sidebar items, three competing review viewers (`/reviews/{id}`, `/human-review`, `/wizard`), two overlapping inboxes (`/dashboard` analytics + `/pr-dashboard` Linear-style), a separate `/scans` page for whole-repo reviews, and seven static prototypes in `/prototypes/` exploring further patterns. The landing surface opens with stat cards (`Total Reviews: 0`, donut charts) instead of "what should I review next." A reviewer who arrives flagged on a PR has to navigate to find their work; a developer who wants to trigger an ad-hoc review has to find the hidden `/browse-pr` page. The vocabulary (chapters, capabilities, briefing, wrap-up) competes for the same job.

## Outcome
A focused four-page app: **Reviews** (unified queue of PR reviews and repo scans, with paste-a-URL trigger pinned at top), **Insights** (the analytics that used to pretend to be the landing page), **Settings** (admin-only — Prompts, LLM, API keys, PATs, exclusions), and a **Help** menu. One review viewer with three modes the user can flip between (Wizard, Chapters, Findings); the system picks the right default based on PR size. The wrap-up step posts inline comments and a final verdict back to GitHub/ADO via the existing `inline-pr-comments` work. Aesthetic shifts toward Linear/Vercel — tighter type, fewer borders, briefing-style prose in the wizard.

## Users
**Primary**: human reviewers handling PRs that Guardian has flagged for human review. They open the app multiple times a day to clear a queue, working through escalated reviews one at a time and posting decisions back to the platform.

**Secondary**: developers (often the same people) who want to trigger Guardian on a specific PR — usually their own or a teammate's — and watch the pipeline run.

**Tertiary**: admins who configure prompts, LLM providers, exclusions, and API keys. They are gated to Settings only.

## Success signal
A logged-in reviewer who lands on `/reviews` (the new root) can pick the top item in their team's queue and complete a full review — including verdict and inline comments posted back to GitHub or ADO — without visiting any other top-level nav item. A developer can paste a PR URL into the same screen and watch a live pipeline progress page until the review is ready to start. Observable by walking the flow end-to-end in a browser against seeded demo data, and by checking that the sidebar has exactly four primary items (Reviews, Insights, Settings, Help).

## Non-goals
- Light theme. Dark slate stays; aesthetic shift is type/spacing/density, not palette inversion.
- Replacing the existing pipeline / agents / decision engine — this is a UI redesign, not a re-architecture of the review engine.
- Migrating the dashboard to a framework (React/Vue). Vanilla HTML + Tailwind + sidebar.js stays.
- Mobile-first. Reviewer workstation only.
- Personal "your queue" filter as the default surface — queue is team-shared; personal filter can be a chip on the queue.
- Per-user theme/layout preferences.
- New review viewer modes beyond Wizard / Chapters / Findings. The seven prototypes in `/prototypes/` are reference, not deliverables.
- Stale-review detection beyond auto-re-review on new commits (existing webhook trigger is the mechanism — no separate cron / TTL system).

## Glossary
- **Review** — a single Guardian run against a subject. Subject is either a PR (GitHub or ADO) or a repo snapshot (whole-repo scan). Reviews are the unit in the queue.
- **Queue** — the unified team-wide list of reviews awaiting human attention. Mixed PRs and repo scans.
- **Trigger origin** — how a review came to exist. `webhook` (platform fired it), `manual` (a user pasted a URL or hit Re-scan), or `scan` (scheduled repo scan).
- **Viewer mode** — Wizard / Chapters / Findings. Three views over the same review data. User can flip at any time; the system picks a default.
- **Wizard** — linear step-by-step viewer: Briefing → Capability 1..N → Wrap-up. Per-concern Accept/Fix/Dismiss buttons. Keyboard nav. Default for large PRs.
- **Chapters** — single scrolling page, AI-grouped 200–400 LOC chunks with inline findings on diffs. Right-rail map. Default for medium PRs.
- **Findings** — flat list of findings grouped by AI agent. The current `/reviews/{id}` page. Default for small PRs (≤2 findings) and engineering/audit use.
- **Wrap-up** — final wizard step. Generates the post-back payload: inline comments (per `inline-pr-comments` spec) + final verdict + summary comment.
- **Live progress page** — the page shown when a user triggers a manual review. SSE stream of pipeline stages (Discovery → Mechanical → Triage → Agents → Decision). Shareable URL. Auto-transitions to the review viewer when complete.
- **Insights** — the secondary analytics page. Houses what `/dashboard` shows today: decision distribution, risk tiers, severity counts, top repos, cost trends.
