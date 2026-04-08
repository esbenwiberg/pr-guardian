# Human Review Interface — Design Concepts & Plan

## Context

The existing `review_detail.html` groups findings **by AI agent** — useful for understanding what the pipeline found, but wrong for a human reviewer who thinks in terms of files and code. The new page targets `decision = human_review` PRs, especially large ones (50+ files), and makes the human's actual job easier: verify AI findings, exercise judgment on uncertain items, and understand the PR as a coherent change.

**Key constraint**: SAST + 6 AI agents have already run before a human sees this. The human is NOT re-discovering bugs — they are **verifying, judging, and contextualising** what automation found. This changes everything about what the interface should surface.

---

## Research Summary (what the evidence says)

| Finding | Source | Design implication |
|---|---|---|
| 200–400 LOC optimal review size; 70–90% detection | SmartBear/Cisco, 2,500 reviews | Cap each "review unit" at ~400 LOC |
| Past 600 LOC, only style/typo comments remain | Cognitive Load Cliff research | Never show 50 files as a flat list |
| Reviewers approve large PRs **faster** (LGTM paradox) | Multiple studies | Actively manage attention budget |
| Only 15% of review comments are about defects | Microsoft (1.5M comments) | Optimise for judgment/context, not bug-hunting |
| More files → fewer useful comments | Microsoft | File count is as important as LOC |
| Only 10.2% think alphabetical ordering is optimal | ICSE 2026 (Breaking the Alphabet) | Order by dependency + risk, not alphabet |
| Guided checklists reduce cognitive load | Springer 2022 | Provide structure, not open-ended review |
| Walkthrough-first reduces review time ~70% | CodeRabbit/Qodo data | Show AI summary BEFORE any code |
| Stacked diffs are the #1 solution for large PRs | Graphite/Phabricator | Simulate stacking at the VIEW level |
| Modular scaffolding reduces workload by 25% | NASA Task Load Index study | Break into chapters |
| Evidence-based comments reduce AI fatigue | CodeRabbit "receipts" approach | Show WHY each finding matters |

---

## The Original 4 Concepts (reference)

**A — File Map**: Two-panel split; file tree (role-grouped) + diff/findings panel. Good spatial orientation, non-linear navigation.

**B — Story Flow**: Linear scroll with AI-clustered change groups. Good for medium PRs, bad for 50+ files.

**C — Triage Board**: Kanban columns (Needs Review / Acknowledged / Clean). Good audit trail, wide viewport only.

**D — Focus Mode**: One file at a time, sorted by risk, keyboard nav. No global overview.

**Problem with all four**: They present files and let the reviewer decide where to go. For 50+ file PRs, this is the wrong model. The reviewer needs the interface to **manage their attention budget**, not just display information.

---

## New Concept E: "Chapters" — Simulated Stacking ⭐ RECOMMENDED

**Core insight** (from Graphite/Phabricator research): The #1 solution for large PRs is stacked diffs — small, focused, independently reviewable units. For PRs that are already monolithic, the REVIEW VIEW can simulate this by decomposing the PR into **chapters** of ~200–400 LOC each.

The AI groups files into 4–8 chapters based on shared path prefix, dependency relationships, and shared agent findings categories. Chapters are ordered: highest-risk first, dependencies before dependents (callee before caller). Each chapter targets ~200–400 LOC — the SmartBear optimal range.

```
┌──────┬───────────────────────────────────────────────────────────────────────┐
│ SIDE │  ◀ Reviews / PR #482 — feat/auth-refactor              [Open in GitHub]│
│  B   ├───────────────────────────────────────────────────────────────────────┤
│  A   │  👁 HUMAN REVIEW REQUIRED  ·  alice  ·  Score 7.2  ·  HIGH risk       │
│  R   │  51 files changed  ·  +1,847 LOC  ·  Est. review: ~40 min             │
│      │  ⚠ This PR touches 3 separate concerns — consider reviewing in order  │
│      ├───────────────────────────────────────────────────────────────────────┤
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ AI SUMMARY                                                      │  │
│      │  │ Refactors JWT signing path, adds scope-based access control,    │  │
│      │  │ and introduces a Redis session store. The signing path changes  │  │
│      │  │ are the highest-risk area — 3 critical/high findings here.      │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │                                                                       │
│      │  CHAPTERS  ── 1 of 6 ──────────────────────── 186 LOC reviewed ──   │
│      │  ████░░░░░░░░░░░░░░░░ 17%                       budget: ~400 LOC     │
│      │                                                                       │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▼ CHAPTER 1 · Auth Core  ●●  ACTIVE                    +186 LOC│  │
│      │  │   token_service.py · user_auth.py  [PRODUCTION]  2 findings    │  │
│      │  │   "JWT signing refactored — highest risk in this PR"            │  │
│      │  ├─────────────────────────────────────────────────────────────────┤  │
│      │  │                                                                 │  │
│      │  │ ┌─ src/auth/token_service.py  modified  +42 -8 ─────────────┐  │  │
│      │  │ │  18 │ def create_token(user_id, scope):                   │  │  │
│      │  │ │+ 24 │   return jwt.encode(payload, KEY)                   │  │  │
│      │  │ │                                                            │  │  │
│      │  │ │  ┌─ [issue] ● DETECTED · Critical · Secrets · CWE-798 ─┐ │  │  │
│      │  │ │  │ Hardcoded KEY on line 24. Is this loaded from env     │ │  │  │
│      │  │ │  │ at runtime, or is it a literal?                       │ │  │  │
│      │  │ │  │ [▼ Evidence]  [▼ Suggestion]  [Dismiss ▼]  [✓ Noted] │ │  │  │
│      │  │ │  └───────────────────────────────────────────────────────┘ │  │  │
│      │  │ └────────────────────────────────────────────────────────────┘  │  │
│      │  │                                                                 │  │
│      │  │ ┌─ src/auth/user_auth.py  modified  +18 -3  (0 findings) ───┐  │  │
│      │  │ │  [diff content — no AI annotations]                        │  │  │
│      │  │ └────────────────────────────────────────────────────────────┘  │  │
│      │  │                                                                 │  │
│      │  │ ┌─ 1 dismissed in this chapter ▶ ─────────────────────────┐   │  │
│      │  │ │ ░ MEDIUM · false_positive — "uses parameterized queries" │   │  │
│      │  │ └─────────────────────────────────────────────────────────┘   │  │
│      │  │                                                                 │  │
│      │  │            [ ◀ Back ]   [ ✓ Chapter done → Next ▶ ]           │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │                                                                       │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▶ CHAPTER 2 · Session & Redis                  +210 LOC · ● 1  │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▶ CHAPTER 3 · API Endpoints                    +195 LOC · ● 1  │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▶ CHAPTER 4 · Config Changes                   +38 LOC  · ● 1  │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▶ CHAPTER 5 · Tests  (21 files)   [Acknowledge all ▶]  ○ clean │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
│      │  ┌─────────────────────────────────────────────────────────────────┐  │
│      │  │ ▶ CHAPTER 6 · Infra / Generated  [Skip ▶]              ○ clean │  │
│      │  └─────────────────────────────────────────────────────────────────┘  │
└──────┴───────────────────────────────────────────────────────────────────────┘
```

**Finding cards use Conventional Comments taxonomy** (from conventionalcomments.org):
- `[issue]` — must fix before merge
- `[suggestion]` — improvement, not blocking
- `[question]` — needs human judgment
- `[nit]` — minor, non-blocking

**Dismissed items**: Collapsed drawer at the bottom of each chapter, showing prior dismissals for files in that chapter. Global "All Previously Dismissed" at page bottom.

**Chapter computation** (client-side from diff + findings):
1. Group files by longest common path prefix (e.g. `src/auth/` → Chapter "Auth Core")
2. Merge small groups (<100 LOC) with related neighbours
3. Split chapters exceeding 500 LOC
4. Order by: max finding severity DESC → production before test/config
5. Within a chapter, order files by dependency (files that others import come first)

**Why this is the right design:**
- Caps review units at ~200–400 LOC (SmartBear optimal)
- Groups by concern not alphabet (ICSE 2026, PeerJ 2019)
- Gives reading order instead of a flat list
- Progress tracking gives reviewer a clear sense of completion
- Batch-acknowledge for test/infra chapters (Tier 3 files)
- Dismissed items visible but non-intrusive

---

## New Concept F: "Three-Pass Review"

**Core insight** (Springer 2022, SmartBear): Structured review passes with explicit goals outperform open-ended review. Different goals need different UIs.

Three distinct modes accessed via a top tab bar:

```
┌─[ 1. ORIENT ]──[ 2. DEEP DIVE ]──[ 3. SWEEP ]──────────────────────────────┐
│                                                                             │
│  PASS 1: ORIENT  (5 min)                                                   │
│  ─────────────────────────────────────────────────────────────────────    │
│  Read the AI summary. Understand WHAT and WHY before seeing any code.      │
│                                                                             │
│  "JWT auth refactored. Redis sessions added. 3 critical/high findings."    │
│                                                                             │
│  [Sequence diagram of component interactions]  (if available)              │
│                                                                             │
│  51 files changed  ·  +1,847 LOC  ·  ~40 min estimated                    │
│  ⚠ Warning: This PR is large. 9 files actually need your eyes.             │
│                                                                             │
│  FILES AT A GLANCE:                                                        │
│  ●● token_service.py    PRODUCTION  detected·critical  → needs deep dive  │
│  ●  user_auth.py        PRODUCTION  suspected·high    → needs deep dive   │
│  ○  test_auth.py (×21)  TEST        no findings       → batch acknowledge │
│  ○  Dockerfile (×11)    INFRA       no findings       → skip              │
│                                                                             │
│                    [ I understand — Start Deep Dive → ]                    │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────── │
│  PASS 2: DEEP DIVE  (30 min)                                               │
│  Only files with active detected/suspected findings. Full diff + AI.       │
│  LOC budget: ████████░░  312 / 400                                         │
│  [file diffs with inline annotations, one file at a time]                  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────── │
│  PASS 3: SWEEP  (10 min)                                                   │
│  Everything else. Lightweight cards. Batch dismiss/acknowledge.            │
│  [ TEST files (21) — Acknowledge all ]  [ INFRA (11) — Skip ]             │
└─────────────────────────────────────────────────────────────────────────── │
```

**Why it works**: Matches research that separates "understanding" from "verifying" from "sweeping". Each pass has a time budget and a completion criterion. Reviewers can see they're spending ~5 min orienting, ~30 min on the 5 files that matter, ~10 min sweeping the rest.

---

## New Concept G: "Risk Radar" — Visual-First Overview

**Core insight** (CodeScene, NDepend, Meta DRS): A visual overview gives instant triage information that a file list cannot.

```
┌──────┬───────────────────────────────────────────────────────────────────┐
│ SIDE │  ◀ Reviews / PR #482 — feat/auth-refactor              [Open PR]  │
│  B   ├───────────────────────────────────────────────────────────────────┤
│  A   │  👁 HUMAN REVIEW  ·  Score 7.2  ·  51 files                       │
│  R   │                                                                   │
│      │  ┌── CHANGE MAP ─────────────────────────────────────────────┐   │
│      │  │                                                           │   │
│      │  │   PRODUCTION                    TEST           INFRA      │   │
│      │  │                                                           │   │
│      │  │  ╔══════════╗  ●●  ┌──────┐   ○○○○○○○○○○   ○○○○○○○○   │   │
│      │  │  ║token_svc ║      │user_ │   small dots    small dots  │   │
│      │  │  ║ (42 LOC) ║      │auth  │                             │   │
│      │  │  ╚══════════╝      └──────┘                             │   │
│      │  │                                                           │   │
│      │  │   ┌────────┐  ●   ┌───────┐  ●                          │   │
│      │  │   │session │      │oauth  │                              │   │
│      │  │   │ .py    │      │_cb.py │                              │   │
│      │  │   └────────┘      └───────┘                              │   │
│      │  │                                                           │   │
│      │  │  ● = findings  Size = LOC changed  Color = severity      │   │
│      │  └───────────────────────────────────────────────────────────┘   │
│      │                                                                   │
│      │  Click any file → full diff + findings slide in from right       │
│      │                                                                   │
│      │  BLAST RADIUS:  ◎ 9 files changed  ◎◎ +14 indirect              │
│      │                                                                   │
│      │  [ Review largest risks first ]  [ Show all 51 files ]           │
└──────┴───────────────────────────────────────────────────────────────────┘
```

**Best for**: Reviewers who want spatial orientation. The visual map shows immediately that this PR is concentrated in `src/auth/` with high-risk changes, with a long tail of small test/infra files.

**Trade-offs**: Requires D3.js or Canvas rendering; bubble sizing/layout needs tuning for very large PRs; may feel unfamiliar.

---

## Concept Comparison

| | E: Chapters ⭐ | F: Three-Pass | G: Risk Radar | A: File Map |
|---|---|---|---|---|
| **Large PR strategy** | Simulates stacking | Explicit passes | Visual triage | Smart filter |
| **Reading order** | AI-computed, dependency-aware | Risk-first per pass | User-driven | Role-grouped |
| **400 LOC budget** | ✅ Per chapter | ✅ Per pass | ✗ User decides | ✗ User decides |
| **Progressive disclosure** | ✅ Summary → chapters | ✅ Orient → Deep Dive | Partial | Partial |
| **Dismissed items** | Per-chapter drawer | Per-file in Deep Dive | In detail drawer | Per-file |
| **Implementation effort** | Medium | Medium | High (viz library) | Medium |
| **Familiarity** | Medium | High | Low | High |

---

## Recommendation: Concept E "Chapters" as primary, with F's opening screen

1. **Opening screen** (from Concept F's Pass 1): AI summary + annotated file list + "can be split" warning + estimated time. No code yet. User clicks to enter Chapter review.

2. **Chapter review** (Concept E): 4–8 chapters, each ~200–400 LOC, ordered by risk and dependency. Each chapter is a self-contained mini-review. Progress bar across the top.

3. **Batch chapter actions**: Test/infra/generated chapters show one-click "Acknowledge all" instead of diffs.

4. **Dismissed items**: Collapsed drawer at bottom of each chapter.

5. **Secondary navigation**: A collapsed file map panel (Concept A style) available for non-linear reviewers who want to jump around.

---

## Data Gaps (must resolve before building)

### 1. Diff patch data — not persisted
`DiffFile.patch` is computed during the pipeline but not stored in the DB.

**Solution (recommended)**: Add `GET /api/dashboard/reviews/{review_id}/diff` endpoint that re-fetches the PR diff from the platform (GitHub/ADO) on-demand. Uses existing platform adapters (`platform/github.py`, `platform/ado.py`). No schema migration needed.

### 2. File role data — not stored per review
`FileRole` classifications are computed but not persisted.

**Solution**: Recompute client-side using simple JS pattern matching (the rules in `discovery/file_roles.py` are straightforward `fnmatch` patterns — trivially portable to JS). Custom patterns can be included in the review response if needed.

---

## Critical Files

| File | Change |
|---|---|
| `src/pr_guardian/api/dashboard.py` | Add `GET /reviews/{id}/diff` endpoint |
| `src/pr_guardian/api/dashboard_page.py` | Add `/reviews/{id}/human-review` HTML route |
| `src/pr_guardian/dashboard/human_review.html` | New page (main implementation, ~800–1000 lines) |
| `src/pr_guardian/dashboard/review_detail.html` | Add "Open Review Mode" button on `human_review` banner |
| `src/pr_guardian/platform/github.py` | Reuse `get_diff()` for the diff endpoint |
| `src/pr_guardian/platform/ado.py` | Reuse `get_diff()` for ADO PRs |

---

## Implementation Steps

1. **Add diff endpoint** in `dashboard.py` — fetch PR diff on-demand via platform adapter, compute file roles client-replicable patterns
2. **Add HTML route** in `dashboard_page.py`
3. **Build `human_review.html`**:
   - Opening screen: summary + file overview table (from existing review data)
   - Chapter computation function (JS): group files by path prefix, order by risk
   - Chapter renderer: diff parser (unified diff → HTML blocks), finding injector
   - Progress bar + chapter navigation
   - Dismissed items drawer per chapter
   - Batch acknowledge for test/infra chapters
4. **Wire "Open Review Mode" button** in `review_detail.html`

---

## Verification

1. Open a `human_review` decision PR → verify "Open Review Mode" button appears
2. Click it → verify opening screen shows AI summary + file overview
3. Verify chapters are computed (4–8 chapters, each ≤500 LOC, ordered by risk)
4. Click into Chapter 1 → verify diff renders with AI findings as inline callouts
5. Verify `[issue]` / `[suggestion]` / `[question]` labels on findings
6. Dismiss a finding → verify it moves to dismissed drawer without reload
7. Click "Acknowledge all" on tests chapter → verify dismissals created
8. Verify dismissed findings from prior reviews show in chapter drawers
9. Test with a 0-finding PR (all chapters show clean)
10. Test with a 50+ file PR — verify chapters keep each under ~500 LOC
