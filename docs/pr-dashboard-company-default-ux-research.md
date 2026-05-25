# PR Dashboard as the Company-Wide Review Portal

Date: 2026-05-25

## Goal

Make PR Guardian the default place engineers open first for code review work, with
GitHub and Azure DevOps becoming execution backends rather than daily review
destinations.

The product bet is not "copy GitHub PRs better." GitHub and Azure DevOps already
own repository-native mechanics: branches, raw diffs, checks, inline comments,
votes, merge boxes, branch policies, and code ownership. PR Guardian should own
the company-wide review overview: what needs attention, why it matters, who is
blocked, what automation found, and what a reviewer should do next.

## Current State

The repo already moved in the right direction:

- `/` redirects to `/reviews`.
- `/pr-dashboard`, `/browse-pr`, and `/scans` redirect into `/reviews`.
- `/dashboard` redirects to `/insights`.
- Primary nav has been reduced to Reviews, Insights, Settings, and Help.
- `/reviews` already has a paste-URL trigger, unified PR/scan queue, stale
  badges, risk summaries, repo/author/risk filters, and live-review redirects.
- The older `pr_dashboard.html` still exists and is useful as a data-rich PR
  inventory, but its card-heavy "My PRs / Queue / Stale / All" framing is less
  compelling than the newer queue-first `/reviews` surface.

The gap: `/reviews` is now the correct route, but it still feels like a compact
queue. To displace GitHub/Azure DevOps as the default habit, it needs stronger
review orchestration, trust signals, policy parity, and team-level visibility.

## External Research Notes

- GitHub PRs provide line comments, suggested changes, conversation resolution,
  requested reviews, CODEOWNERS auto-review requests, and required approvals
  before merge. Source: [GitHub pull request reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/about-pull-request-reviews), [GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners).
- Azure Repos PRs provide required/optional reviewers, branch-policy reviewers,
  line/file/general comments, suggested changes, thread statuses, reviewed-file
  tracking, votes, completion, and comment-resolution policy. Source:
  [Azure Repos review PRs](https://learn.microsoft.com/en-us/azure/devops/repos/git/review-pull-requests?view=azure-devops),
  [Azure Repos branch policies](https://learn.microsoft.com/en-us/azure/devops/repos/git/branch-policies).
- GitHub's accessibility guide describes PR review as a multi-tab workflow:
  Conversation, Commits, Checks, and Files changed. Source:
  [GitHub PR accessibility guide](https://accessibility.github.com/documentation/guide/pull-requests/).

Implication: PR Guardian must respect platform-native review primitives, but
win on cross-platform prioritization, reviewer focus, automation evidence, and
team queue health.

## Findings

### 1. The Default View Should Be "Work To Do," Not "Open PRs"

GitHub and ADO list PRs. A company review portal should answer:

- What needs human review now?
- What is blocking merge?
- What can be safely approved?
- What has changed since I last looked?
- Which review is the best next use of my time?

The current `/reviews` queue is close, but `All` as the default weakens the
signal. "Needs review" should be the default for reviewers, with "All" available
as inventory.

### 2. Platform Parity Is Required for Trust

Engineers will not switch if PR Guardian hides platform facts they rely on:

- Required reviewers and CODEOWNERS/path policies.
- Required approvals remaining.
- CI/check status.
- Active/unresolved conversation count.
- Merge conflicts and draft state.
- "Changed since last viewed/reviewed."
- Current user's review/vote state.

Without these, users will keep GitHub/ADO open as the source of truth.

### 3. PR Guardian's Differentiator Is Review Triage

The unique value is that Guardian has already run discovery, mechanical gates,
triage, agents, and the decision engine. The queue should show the result as an
actionable priority model, not only badges:

- `Ready to approve`
- `Needs human judgment`
- `Author blocked`
- `CI/policy blocked`
- `New commits since review`
- `High-risk change`
- `No Guardian run yet`

This reframes the app from a PR browser into a review command center.

### 4. "Mine" Is Too Narrow for a Company Default

Personal queues matter, but company adoption comes from team queue health. The
primary overview should support:

- My required reviews.
- My authored PRs needing action.
- Team review queue.
- Repos/services I own.
- Stale or blocked PRs across the org.

The older `pr_dashboard.html` summary cards are useful, but should be recast as
team operations metrics rather than five equal inventory cards.

### 5. The Viewer Needs a Stronger "Why This Review Matters" Header

The three review modes are a strong direction, but the first screen should
explain the review job before showing findings:

- What changed?
- What is risky?
- What must the human decide?
- Which files/chapters matter most?
- What can be skimmed or batch-acknowledged?

This is the main opportunity to beat GitHub/ADO: reviewer attention management.

## Recommendations

### A. Make `/reviews` the Company Queue

Default filter: `Needs review`, not `All`.

Top tabs should be role-driven:

- `Needs review`
- `Assigned to me`
- `My PRs`
- `Team blocked`
- `Ready to approve`
- `All`

Rows should be sorted by urgency:

1. High/critical Guardian findings and human-review decision.
2. Required reviewer is current user or user's team.
3. Stale after new commits.
4. Oldest waiting time.
5. CI/policy blocked after author action.

### B. Replace Generic Summary Cards With Queue Health

Use compact operational counters:

- `12 need review`
- `5 assigned to me`
- `8 author blocked`
- `3 stale after update`
- `21 ready / policy clean`

Each card should filter the queue. Avoid large card rows that push the actual
work list down.

### C. Add a "Review Readiness" Column

Every row should make readiness scannable:

- Guardian: not run / running / reviewed / failed.
- CI: passing / failing / pending.
- Policy: approvals remaining / comment resolution / required owner missing.
- Merge: clean / conflicts / draft.
- Activity: new commits / unresolved threads / author replied.

This is the minimum parity layer needed before engineers trust Guardian as the
starting point.

### D. Add Ownership and Routing

PR Guardian should understand team ownership beyond platform labels:

- CODEOWNERS/GitHub teams.
- ADO required reviewer policies.
- Internal service ownership mapping.
- "I own this service" subscriptions.
- On-call/release captain review queues.

Queue facets should include `owner team`, `service`, `policy`, and `SLA`.

### E. Make the Side Panel a Decision Preview

The old `pr_dashboard.html` side panel is useful, but it should not just mirror
metadata. It should answer:

- Why is this PR in this queue?
- What must I decide?
- What will happen if I click Start Review?
- What comments/verdict will Guardian post?
- What platform requirements remain?

Primary actions:

- `Open guided review`
- `Open in GitHub/ADO`
- `Assign to me`
- `Re-run Guardian`
- `Snooze / hand off`

### F. Add a Reviewer Workbench

Inside `/reviews/{id}`, keep Wizard / Chapters / Findings, but add a persistent
review workbench header:

- PR title, repo, author, branch.
- Decision status: approve / request changes / block / undecided.
- Required approvals and policy status.
- Guardian confidence and top risks.
- Mode switcher.
- Platform link.
- Finish review button.

The viewer should feel like the place where review decisions are made, not a
report that eventually sends the user back to GitHub/ADO.

### G. Close the Loop With Postback Receipts

After posting to GitHub/ADO, show a receipt:

- Inline comments posted.
- Summary comment posted.
- Vote/verdict posted.
- Threads skipped or summary-only.
- Links to platform comments.
- Next review in queue.

This builds trust and reduces the "I need to verify in GitHub" habit.

### H. Add Company Rollout Features

To become default, product and workflow nudges matter:

- Slack/Teams links should point to PR Guardian review URLs, not raw PR URLs.
- Browser/bookmark default route is `/reviews`.
- PR Guardian comments on platform PRs should include "Review in Guardian" links.
- GitHub/ADO PR templates can include a Guardian review link once configured.
- Admin settings should expose repo onboarding, platform connection health, and
  policy coverage.
- Insights should show adoption: reviews completed in Guardian, median time to
  first human action, stale PR reduction, auto-approved clean PRs.

## Proposed Information Architecture

```text
/reviews                      company review queue
/reviews?scope=mine           personal assigned/authored work
/reviews?scope=team           team/service queue
/reviews/{id}                 reviewer workbench
/reviews/{id}/live            pipeline progress
/insights                     review health, automation value, adoption
/settings                     platform connections, owners, policy, prompts
```

Legacy paths should continue redirecting, but product copy should stop using
"PR Dashboard" as the primary label. The user-facing product surface is
"Reviews".

## Wireframe 1: Company Review Queue

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Reviews                                                   18 waiting  Sync ↻ │
├──────────────────────────────────────────────────────────────────────────────┤
│ Paste GitHub/ADO PR URL or owner/repo for scan                  [Review →]  │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Needs review 12] [Assigned to me 5] [My PRs 4] [Blocked 8] [Ready 21] [All] │
│ repo: any ▾   owner: any ▾   service: any ▾   risk: any ▾   policy: any ▾   │
├──────────────────────────────────────────────────────────────────────────────┤
│ PR GH #482  feat/auth-refactor                         HIGH · ~40m      →  │
│ demo/api · Auth Platform · alice · 12m ago                                  │
│ Why now: required reviewer = you · Guardian found 3 high · new commits       │
│ Guardian reviewed ✓  CI passing ✓  Policy 1 approval left  Threads 2 open    │
├──────────────────────────────────────────────────────────────────────────────┤
│ PR ADO #917  migrate billing worker                    MED · ~12m       →  │
│ finance/billing · Payments · ravi · 1h ago                                  │
│ Why now: team queue · owner approval missing                                 │
│ Guardian reviewed ✓  CI pending ◐  Policy owner missing  Merge clean ✓       │
├──────────────────────────────────────────────────────────────────────────────┤
│ PR GH #479  refactor/billing-job                       ready            →  │
│ demo/api · Billing · carol · 6h ago                                         │
│ Why now: safe to approve · clean Guardian run                                │
│ Guardian clean ✓  CI passing ✓  Policy satisfied ✓  Threads resolved ✓       │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Wireframe 2: Row Expanded Preview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ feat/auth-refactor                                           Open platform ↗ │
│ demo/api · PR #482 · alice → main · updated 12m ago                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ Queue reason                                                                 │
│ Required reviewer: you · Guardian decision: human review · Risk: HIGH        │
│ New commits landed after previous review.                                    │
│                                                                              │
│ Required before merge                                                        │
│ [✓] CI passing     [ ] 1 approval left     [ ] 2 active threads              │
│ [✓] no conflicts   [✓] branch policy loaded                                  │
│                                                                              │
│ Guardian summary                                                             │
│ Auth signing path changed. Highest risk is token_service.py.                 │
│ 3 high findings, 1 medium, 51 files. Suggested review order ready.           │
│                                                                              │
│ [Open guided review] [Assign to me] [Re-run Guardian] [Snooze]               │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Wireframe 3: Reviewer Workbench

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Reviews  GH demo/api #482  feat/auth-refactor                 Open GH ↗   │
│ Human review · HIGH · 3 high / 1 med · CI passing · 1 approval left          │
│ [Wizard] [Chapters] [Findings]                         [Finish review →]     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Briefing                                                                     │
│ This PR refactors JWT signing and adds Redis-backed sessions.                │
│ Human judgment needed on token key handling and TTL behavior.                │
│                                                                              │
│ Review plan                                                                  │
│ 1. Auth core          186 LOC  2 high      token_service.py, user_auth.py     │
│ 2. Session storage    210 LOC  1 medium    redis_session.py                  │
│ 3. API endpoints      195 LOC  clean       route changes                     │
│ 4. Tests/fixtures     620 LOC  clean       batch acknowledge                 │
│                                                                              │
│ Active finding                                                               │
│ [issue] Hardcoded KEY on line 24                              HIGH CWE-798   │
│ Evidence · suggested fix · platform comment target                           │
│ [Accept] [Request fix] [Dismiss]                                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Wireframe 4: Team Health / Insights

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Insights                                                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Review health                                                                │
│ Need review 12   Median wait 3.4h   Stale 3   Auto-approved 41 this week     │
│                                                                              │
│ Team queues                                                                  │
│ Auth Platform     5 waiting   2 blocked   oldest 1d 4h                       │
│ Payments          4 waiting   3 blocked   oldest 2d 1h                       │
│ Infra             2 waiting   0 blocked   oldest 5h                          │
│                                                                              │
│ Adoption                                                                       │
│ 78% of human reviews completed in Guardian                                    │
│ 63% fewer clean PRs sent to humans                                            │
│ Top escape hatch: missing policy/CODEOWNER detail                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Specific Improvement Ideas

### Queue Prioritization

- Default to `Needs review`.
- Add `Ready to approve` as a first-class queue.
- Add `Author blocked` for PRs with requested changes, active threads, failed CI,
  or policy failures.
- Add `Waiting on me` based on GitHub requested reviewers, CODEOWNERS, ADO
  required reviewers, and user's identity mapping.
- Add `Updated since reviewed` based on head SHA comparison.
- Add queue reason text to each row.

### Review Row Data

- Show required approvals remaining.
- Show unresolved thread count.
- Show current user's vote/review status.
- Show owner team/service.
- Show last Guardian run age and result.
- Show top finding category, not only severity.
- Show estimated review time with a confidence hint.

### Filters

- Add `owner team`.
- Add `service`.
- Add `policy state`.
- Add `Guardian state`.
- Add `changed since I reviewed`.
- Keep filter state in the URL for team links.

### Viewer

- Replace "raw report first" with briefing-first.
- Keep the three modes, but add one persistent header.
- Add next-best-action copy: "verify token key handling", "review owner policy",
  "only tests changed; batch acknowledge available".
- Add keyboard-driven review decisions.
- Add comment preview before postback.
- After finish, route to the next queue item.

### Platform Integration

- Add "Open exact platform tab" links: Conversation, Files changed, Checks
  where platform URLs support it.
- Preserve suggested changes and inline comments as platform-native comments.
- Surface platform policy failures inside Guardian rather than forcing a tab
  switch.
- Link every posted Guardian comment back to the Guardian review.

### Team Adoption

- Make Slack/Teams notifications link to `/reviews/{id}`.
- Add a platform PR comment on first Guardian run: "Reviewed by Guardian. Open
  guided review."
- Add repo onboarding status in Settings.
- Add an admin "coverage" page: repos synced, policies visible, owners mapped,
  stale syncs, token health.
- Add adoption metrics in Insights.

## Data/API Needs

- Platform policy summary per PR:
  - GitHub required status checks, required reviews, CODEOWNERS requests.
  - ADO branch policies, required reviewers, minimum reviewers, comment
    resolution status.
- Review-thread summary:
  - active/resolved/won't-fix counts.
  - whether current user has unread/new activity.
- Current user's platform review state:
  - requested reviewer, approved, requested changes, commented, not involved.
- Ownership mapping:
  - CODEOWNERS, ADO reviewer policies, internal service catalog if available.
- Head SHA comparison:
  - reviewed SHA vs current SHA.
- Postback receipt:
  - platform comment IDs/URLs, vote/verdict status, failures.

## Suggested Rollout

1. Queue trust pass: add readiness/status columns and queue reason text.
2. Prioritization pass: change default to `Needs review`, add `Ready` and
   `Blocked` queues.
3. Workbench pass: unify the review header and make briefing the default entry.
4. Postback receipt pass: show exactly what was posted to GitHub/ADO.
5. Adoption pass: update notifications/deep links and add Insights adoption
   metrics.

## Success Metrics

- Percentage of review notification links opening PR Guardian first.
- Percentage of human reviews completed from Guardian.
- GitHub/ADO "open platform" click rate after starting in Guardian.
- Median time from review requested to first human decision.
- Median time from Guardian findings to platform comments posted.
- Number of clean PRs auto-approved without human review.
- Stale PR count over time.
- User-reported reasons for leaving Guardian during review.

## Bottom Line

PR Guardian becomes the default by being the cross-platform review operating
system: prioritized queue, policy-aware readiness, guided review, reliable
postback, and team health. GitHub and Azure DevOps remain the authoritative
systems of record, but engineers should only need to go there for raw platform
details, merge execution, or unusual edge cases.
