# Finding Feedback Loop

> PR authors can dismiss/comment on findings in the dashboard, then trigger a
> re-review that feeds that context into the agent prompts.

## Motivation

PR Guardian is currently one-shot: review runs, findings posted, done. Authors
have no way to say "that's by design" or "false positive" and get a cleaner
second pass. This turns the tool into a conversation instead of a verdict.

---

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Where do authors leave feedback? | **Web UI** (review detail page) | Structured data > parsing PR comments. Works identically for GitHub + ADO. Author already visits dashboard for full findings. |
| How is context carried to agents? | **Prompt injection** — dismissed findings are serialized into the agent's user message | Simple, no agent code changes needed. Agents are already good at "don't flag X because the author said Y". |
| How do dismissals survive across reviews? | **Signature matching** + prompt context | Signature = `(file, category, agent_name)` hash. Fuzzy enough to survive line shifts, specific enough to avoid false matches. |
| Dismissal scope | **Per-PR, not per-review** | A dismissal on PR #42 carries forward to all future reviews of PR #42. New PR = clean slate. |
| Who can dismiss? | **Anyone with dashboard access** | Single-tenant, no RBAC (per earlier decision). Revisit if multi-tenant lands. |
| Dismissal staleness | **One re-review cycle** | If the finding doesn't reappear, the dismissal is quietly archived. If it does, it's auto-matched and carried over. |

---

## Data Model

### New Table: `finding_dismissals`

```
finding_dismissals
├── id              UUID, PK
├── pr_id           String — the platform PR identifier (e.g. "42")
├── repo            String — "owner/repo"
├── platform        String — "github" | "ado"
├── signature       String — deterministic hash for cross-review matching
├── status          String(24) — "by_design" | "false_positive" | "acknowledged" | "will_fix"
├── comment         Text — free-form author explanation
├── source_finding  JSONB — snapshot of the original finding for context
│   ├── file, line, category, agent_name
│   ├── severity, certainty
│   └── description (first 500 chars)
├── active          Boolean, default true — false when archived
├── created_at      DateTime
├── updated_at      DateTime
```

**Why not FK to `findings`?** Findings are per-review. Dismissals are per-PR
and must survive across reviews. The `signature` field is the join key.

### Signature Computation

```python
def finding_signature(file: str, category: str, agent_name: str) -> str:
    """Stable hash that survives line-number shifts."""
    raw = f"{file}::{category}::{agent_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

Line numbers are intentionally excluded — they shift between commits. The
file+category+agent combo is stable enough for matching.

---

## Implementation Steps

### Step 1: Database Migration

**File: New Alembic migration**

- Add `finding_dismissals` table as defined above.
- Add index on `(repo, pr_id, platform, active)` for fast lookup.
- Add index on `(signature, repo, pr_id)` for matching.

**File: `src/pr_guardian/persistence/models.py`**

- Add `FindingDismissalRow` ORM model.

### Step 2: Storage Layer

**File: `src/pr_guardian/persistence/storage.py`**

New functions:

```python
async def upsert_dismissal(
    pr_id: str, repo: str, platform: str,
    finding: dict, agent_name: str,
    status: str, comment: str,
) -> uuid.UUID:
    """Create or update a dismissal. Computes signature from finding fields."""

async def remove_dismissal(dismissal_id: uuid.UUID) -> None:
    """Delete a dismissal (un-dismiss)."""

async def get_active_dismissals(
    pr_id: str, repo: str, platform: str,
) -> list[dict]:
    """All active dismissals for a PR. Used by orchestrator + UI."""

async def match_dismissals_to_findings(
    pr_id: str, repo: str, platform: str,
    findings: list[dict],  # new review findings with agent_name
) -> dict[str, dict]:
    """Returns {signature: dismissal} for findings that match an active dismissal."""

async def archive_stale_dismissals(
    pr_id: str, repo: str, platform: str,
    active_signatures: set[str],
) -> int:
    """Mark dismissals as inactive if their signature didn't appear in the latest review."""
```

### Step 3: API Endpoints

**File: `src/pr_guardian/api/dashboard.py`**

```python
# --- Dismissals ---

POST /api/dashboard/findings/{finding_id}/dismiss
    Body: { "status": "by_design", "comment": "This is intentional because..." }
    → Looks up finding + agent_result to get file/category/agent_name
    → Calls storage.upsert_dismissal()
    → Returns { "id": "...", "signature": "..." }

DELETE /api/dashboard/dismissals/{dismissal_id}
    → Calls storage.remove_dismissal()
    → Returns 204

GET /api/dashboard/reviews/{review_id}/dismissals
    → Gets review's PR identifiers
    → Returns all active dismissals for that PR
    → Each dismissal includes match status against current review findings

# --- Re-review ---

POST /api/dashboard/reviews/{review_id}/re-review
    → Looks up original review to get pr_url, repo, platform
    → Fetches active dismissals for the PR
    → Triggers run_review() with dismissal context
    → Returns { "status": "queued", "new_review_id": "..." }
```

### Step 4: Orchestrator Changes

**File: `src/pr_guardian/core/orchestrator.py`**

Add optional `dismissals` parameter to `run_review()`:

```python
async def run_review(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    service_config: GuardianConfig | None = None,
    *,
    post_comment: bool = True,
    base_url: str = "",
    dismissals: list[dict] | None = None,  # <-- NEW
) -> ReviewResult:
```

Changes in the function body:

1. **Before agent stage**: If `dismissals` is not empty, build a context string:
   ```
   ## Previously Dismissed Findings
   The PR author has reviewed the following findings and provided context.
   Consider this feedback — do not re-flag dismissed items unless new code
   changes make them relevant again.

   1. [auth.py :: SQL Injection :: security_privacy] Status: false_positive
      Author: "This uses parameterized queries, the ORM handles escaping."

   2. [config.py :: Hardcoded Secret :: security_privacy] Status: by_design
      Author: "Test fixture, not a real secret."
   ```

2. **Pass to `build_agent_context()`**: Add the dismissal context string as an
   additional section in the user message. Each agent only sees dismissals
   relevant to its own `agent_name`.

3. **After agents complete**: Call `match_dismissals_to_findings()` to tag
   findings that match existing dismissals. Call
   `archive_stale_dismissals()` to clean up dismissals whose findings
   didn't reappear.

**File: `src/pr_guardian/agents/base.py`**

Update `build_agent_context()` to accept an optional `dismissal_context: str`
parameter and append it to the user message.

### Step 5: Review Detail UI — Dismiss Action

**File: `src/pr_guardian/dashboard/review_detail.html`**

Per-finding card changes:

```html
<!-- Add dismiss button to each finding card, after the suggestion div -->
<div class="flex items-center gap-2 mt-2">
  <button class="dismiss-btn text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600"
          data-finding-id="${f.id}" data-agent="${agent_name}">
    Dismiss
  </button>
  <!-- If already dismissed, show badge instead -->
  <span class="dismiss-badge text-xs px-2 py-0.5 rounded bg-amber-900/50 text-amber-400"
        data-dismissal-id="${dismissal.id}" style="display:none">
    ${status} — "${comment}"
    <button class="ml-1 text-amber-600 hover:text-amber-300">[undo]</button>
  </span>
</div>
```

Dismiss flow (JS):
1. Click "Dismiss" → show inline dropdown with status options + comment textarea
2. Submit → `POST /api/dashboard/findings/{id}/dismiss`
3. Replace button with dismiss badge showing status + comment
4. Undo → `DELETE /api/dashboard/dismissals/{id}` → restore button

### Step 6: Review Detail UI — Re-review Button

**File: `src/pr_guardian/dashboard/review_detail.html`**

Add to the review header area (near the decision banner):

```html
<div class="flex items-center gap-3">
  <span class="text-xs text-slate-400" id="dismissal-count">
    0 findings dismissed
  </span>
  <button id="re-review-btn"
          class="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-sm font-medium">
    Re-review with feedback
  </button>
</div>
```

JS flow:
1. On page load, fetch `GET /api/dashboard/reviews/{id}/dismissals` → update count + mark dismissed findings
2. Click "Re-review" → `POST /api/dashboard/reviews/{id}/re-review`
3. Show toast "Review queued — redirecting..."
4. Redirect to new review's detail page (or dashboard with SSE active)

### Step 7: Enrich Review Detail API Response

**File: `src/pr_guardian/api/dashboard.py`**

When returning review detail (`GET /api/dashboard/reviews/{review_id}`):

- Fetch active dismissals for the PR
- For each finding in the response, check if its signature matches a dismissal
- Add `"dismissal": { "id", "status", "comment" } | null` to each finding dict

This lets the UI render dismissed state on initial page load without a second request.

---

## File Change Summary

| File | Change |
|------|--------|
| `alembic/versions/xxx_add_finding_dismissals.py` | New migration |
| `src/pr_guardian/persistence/models.py` | Add `FindingDismissalRow` |
| `src/pr_guardian/persistence/storage.py` | Add dismissal CRUD + matching functions |
| `src/pr_guardian/api/dashboard.py` | Add dismiss/un-dismiss/re-review endpoints, enrich review detail |
| `src/pr_guardian/core/orchestrator.py` | Accept `dismissals` param, build context, post-review matching |
| `src/pr_guardian/agents/base.py` | `build_agent_context()` accepts dismissal context string |
| `src/pr_guardian/api/review.py` | Update `_run_review_background` to pass dismissals |
| `src/pr_guardian/dashboard/review_detail.html` | Dismiss buttons, badges, re-review button, JS wiring |

---

## Out of Scope (for now)

- **PR-comment-based feedback** — revisit if users want to stay in GitHub/ADO
- **RBAC on dismissals** — single-tenant means anyone can dismiss
- **Dismissal analytics** — "how often do authors dismiss security findings?" (interesting but later)
- **Auto-dismiss on merge** — clean up dismissals when PR is merged
- **Bulk dismiss** — leverage existing checkbox selection to dismiss multiple findings at once (nice follow-up)

---

## Resolved Questions

1. **PR summary comment mentions dismissals** — Yes. e.g. "3 findings dismissed
   by author, 2 new findings". Keeps the PR thread informed.
2. **Dismissed findings and scoring** — `false_positive`/`by_design` do NOT
   count toward combined score. `acknowledged`/`will_fix` still count.
3. **Review diff** — Yes, include it. Show "2 resolved, 1 new, 3 carried over"
   between reviews.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SYSTEM OVERVIEW                              │
│                                                                     │
│  ┌──────────┐    webhook/     ┌──────────────┐     ┌────────────┐  │
│  │ GitHub / │───────────────→ │  Orchestrator │────→│  Agents    │  │
│  │ ADO      │    manual       │  run_review() │     │  (6x LLM)  │  │
│  └──────────┘                 └──────┬───────┘     └─────┬──────┘  │
│                                      │                   │         │
│                                      │    ┌──────────────┘         │
│                                      ▼    ▼                        │
│                               ┌──────────────┐                     │
│                               │  PostgreSQL   │                     │
│                               │  ┌─────────┐  │                     │
│                               │  │reviews  │  │                     │
│                               │  │findings │  │                     │
│                               │  │dismissals│ │  ◄── NEW           │
│                               │  └─────────┘  │                     │
│                               └──────┬───────┘                     │
│                                      │                             │
│                                      ▼                             │
│                               ┌──────────────┐                     │
│                               │  Dashboard   │                     │
│                               │  (Web UI)    │                     │
│                               └──────────────┘                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Feedback Loop Flow

```
 FIRST REVIEW                          FEEDBACK                         RE-REVIEW
 ═══════════                           ════════                         ═════════

 ┌─────────┐                      ┌──────────────┐                ┌─────────────┐
 │ PR #42  │    run_review()      │ Review Detail│                │ run_review() │
 │ opened  │──────────────────→   │    Page      │                │ + dismissals │
 └─────────┘                      └──────┬───────┘                └──────┬──────┘
                                         │                               │
              ┌──────────────────────────┘                               │
              ▼                                                          │
 ┌────────────────────────────────────┐                                  │
 │  Findings displayed:               │                                  │
 │                                    │                                  │
 │  ┌──────────────────────────────┐  │                                  │
 │  │ HIGH  SQL Injection          │  │                                  │
 │  │ auth.py:42                   │  │                                  │
 │  │ [Dismiss ▼]                  │──┼──── author clicks ──┐           │
 │  └──────────────────────────────┘  │                     │           │
 │  ┌──────────────────────────────┐  │                     │           │
 │  │ MED   Hardcoded Secret       │  │                     │           │
 │  │ config.py:10                 │  │                     │           │
 │  │ [Dismiss ▼]                  │──┼──── author clicks ──┤           │
 │  └──────────────────────────────┘  │                     │           │
 │  ┌──────────────────────────────┐  │                     │           │
 │  │ LOW   Missing Null Check     │  │                     │           │
 │  │ utils.py:88                  │  │                     │           │
 │  │ [Dismiss ▼]                  │  │                     │           │
 │  └──────────────────────────────┘  │                     │           │
 └────────────────────────────────────┘                     │           │
                                                            ▼           │
                                               ┌────────────────────┐   │
                                               │ Dismiss Dialog     │   │
                                               │                    │   │
                                               │ Status:            │   │
                                               │ (•) false_positive │   │
                                               │ ( ) by_design      │   │
                                               │ ( ) acknowledged   │   │
                                               │ ( ) will_fix       │   │
                                               │                    │   │
                                               │ Comment:           │   │
                                               │ ┌────────────────┐ │   │
                                               │ │Uses ORM param  │ │   │
                                               │ │queries, safe.  │ │   │
                                               │ └────────────────┘ │   │
                                               │         [Submit]   │   │
                                               └────────┬───────────┘   │
                                                        │               │
                                                        ▼               │
                                               ┌────────────────────┐   │
                                               │ finding_dismissals │   │
                                               │ table (PostgreSQL) │   │
                                               └────────┬───────────┘   │
                                                        │               │
              ┌─────────────────────────────────────────┘               │
              ▼                                                          │
 ┌────────────────────────────────────┐                                  │
 │  Updated Findings:                 │                                  │
 │                                    │      ┌──────────────────────┐    │
 │  ┌──────────────────────────────┐  │      │ [Re-review with     │    │
 │  │ HIGH  SQL Injection          │  │      │  feedback]           │────┘
 │  │ auth.py:42                   │  │      │                      │
 │  │ false_positive ─ "Uses ORM"  │  │      │ 2 findings dismissed │
 │  │ [undo]                       │  │      └──────────────────────┘
 │  └──────────────────────────────┘  │
 │  ┌──────────────────────────────┐  │
 │  │ MED   Hardcoded Secret       │  │
 │  │ config.py:10                 │  │
 │  │ by_design ─ "Test fixture"   │  │
 │  │ [undo]                       │  │
 │  └──────────────────────────────┘  │
 │  ┌──────────────────────────────┐  │
 │  │ LOW   Missing Null Check     │  │
 │  │ utils.py:88                  │  │
 │  │ [Dismiss ▼]                  │  │
 │  └──────────────────────────────┘  │
 └────────────────────────────────────┘
```

## Re-review Pipeline (what happens inside)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  POST /api/dashboard/reviews/{id}/re-review                     │
 └──────────────────────────┬───────────────────────────────────────┘
                            │
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ 1. Load original review → get pr_url, repo, platform            │
 │ 2. Fetch active dismissals for this PR                          │
 │ 3. Hydrate PR from platform API (latest diff)                   │
 └──────────────────────────┬───────────────────────────────────────┘
                            │
                            ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  run_review(pr, adapter, dismissals=[...])                      │
 │                                                                  │
 │  ┌─────────────┐   ┌─────────────┐   ┌────────────────────────┐ │
 │  │  Discovery   │──→│ Mechanical  │──→│  Triage                │ │
 │  └─────────────┘   └─────────────┘   └───────────┬────────────┘ │
 │                                                   │              │
 │                                                   ▼              │
 │  ┌────────────────────────────────────────────────────────────┐  │
 │  │  Build agent context (per agent):                          │  │
 │  │                                                            │  │
 │  │  Normal context: diff, files, language map, blast radius   │  │
 │  │                         +                                  │  │
 │  │  ┌──────────────────────────────────────────────────────┐  │  │
 │  │  │ ## Previously Dismissed Findings                     │  │  │
 │  │  │                                                      │  │  │
 │  │  │ The PR author has reviewed the following findings     │  │  │
 │  │  │ and provided context. Do not re-flag dismissed items  │  │  │
 │  │  │ unless new code changes make them relevant again.     │  │  │
 │  │  │                                                      │  │  │
 │  │  │ 1. [auth.py :: SQL Injection] false_positive          │  │  │
 │  │  │    "Uses ORM parameterized queries, safe."            │  │  │
 │  │  │                                                      │  │  │
 │  │  │ 2. [config.py :: Hardcoded Secret] by_design          │  │  │
 │  │  │    "Test fixture, not a real secret."                 │  │  │
 │  │  └──────────────────────────────────────────────────────┘  │  │
 │  └───────────────────────────┬────────────────────────────────┘  │
 │                              │                                   │
 │                              ▼                                   │
 │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐  │
 │  │ sec/  │ │ perf  │ │ arch  │ │ qual  │ │ test  │ │ hot   │  │
 │  │ priv  │ │       │ │       │ │       │ │       │ │ spot  │  │
 │  └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘  │
 │      └─────────┴─────────┴────┬────┴─────────┴─────────┘       │
 │                               │                                  │
 │                               ▼                                  │
 │  ┌────────────────────────────────────────────────────────────┐  │
 │  │  Post-review processing:                                   │  │
 │  │                                                            │  │
 │  │  1. Match new findings against dismissals (by signature)   │  │
 │  │  2. Tag matched findings as dismissed                      │  │
 │  │  3. Exclude false_positive/by_design from score            │  │
 │  │  4. Archive stale dismissals (finding didn't reappear)     │  │
 │  │  5. Build review diff: "2 resolved, 1 new, 3 carried"     │  │
 │  └────────────────────────────────────────────────────────────┘  │
 │                               │                                  │
 │                               ▼                                  │
 │  ┌────────────────────────────────────────────────────────────┐  │
 │  │  Decision + Post Results                                   │  │
 │  │                                                            │  │
 │  │  PR Comment includes:                                      │  │
 │  │  "Re-review of PR #42 — 2 findings dismissed by author,   │  │
 │  │   1 new finding, 1 resolved. Score: 8.2 → auto_approve"   │  │
 │  └────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────┘
```

## Signature Matching (cross-review)

```
  Review #1 findings              Dismissals              Review #2 findings
  ═══════════════════          ═══════════════           ═══════════════════

  ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐
  │ auth.py:42       │    │ sig: a3f2...        │    │ auth.py:47       │
  │ SQL Injection    │───→│ false_positive      │───→│ SQL Injection    │  MATCH
  │ security_privacy │    │ "Uses ORM..."       │    │ security_privacy │  (line shifted
  │                  │    └─────────────────────┘    │                  │   but sig same)
  │ sig: a3f2...     │                               │ sig: a3f2...     │
  └──────────────────┘                               └──────────────────┘

  ┌──────────────────┐    ┌─────────────────────┐
  │ config.py:10     │    │ sig: 7b1e...        │    Finding didn't
  │ Hardcoded Secret │───→│ by_design           │    reappear in
  │ security_privacy │    │ "Test fixture..."   │    Review #2
  │                  │    └─────────────────────┘    → archived
  │ sig: 7b1e...     │
  └──────────────────┘                               ┌──────────────────┐
                                                      │ handler.py:15    │
                          No matching dismissal  ◄────│ Missing Auth     │  NEW
                                                      │ security_privacy │  finding
                                                      │                  │
                                                      │ sig: c9d4...     │
                                                      └──────────────────┘

  Signature = sha256("file::category::agent_name")[:16]
  Line numbers excluded — they drift between commits.
```
