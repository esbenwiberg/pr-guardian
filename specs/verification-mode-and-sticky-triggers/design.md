# Design — Verification mode and sticky triggers

## Blast radius

```
src/pr_guardian/persistence/models.py                 — FindingDismissalRow grows lifecycle columns
src/pr_guardian/persistence/storage.py                — FindingState enum, mark_*/get_finding_states/infer_fixes/verify_sticky_trigger helpers
src/pr_guardian/decision/engine.py                    — check_overrides() return shape; DecisionResult drops override_reasons/trust_tier_reasons
src/pr_guardian/decision/types.py                     — new StickyTrigger dataclass
src/pr_guardian/api/dashboard.py                      — review GET payload split fields; new POST /verify endpoint; /re-review wired to infer_fixes
src/pr_guardian/api/review.py                         — full re-run wired to infer_fixes
src/pr_guardian/dashboard/review_detail.html          — render split fields; add Show-code disclosure
src/pr_guardian/dashboard/human_wizard.html           — Verification chapter; trigger-focus mode
src/pr_guardian/dashboard/static/snippet.js           — shared renderer (NEW)
alembic/versions/018_add_finding_lifecycle.py         — additive migration (NEW)
tests/test_finding_lifecycle.py                       — storage helpers (NEW)
tests/test_engine_sticky_split.py                     — bucketing (NEW)
tests/test_fix_inference.py                           — re-run inference (NEW)
tests/test_snippet_endpoint.py                        — diff endpoint params (NEW)
tests/test_verify_endpoint.py                         — verify endpoint contract (NEW)
```

## Seams

| # | Seam | Contract crossing | Producer | Consumer |
|---|------|-------------------|----------|----------|
| 1 | Storage → engine | `FindingState` enum + `get_finding_states(pr_id)` | Brief 01 | Briefs 02, 03, 05 |
| 2 | Storage → re-run | `mark_fixed` / `mark_regressed` / `infer_fixes()` | Brief 01 | Brief 03 |
| 3 | Engine → dashboard payload | `sticky_triggers` + `finding_reasons` (replaces `override_reasons`) | Brief 02 | Briefs 04 (passive), 05 (active) |
| 4 | Dashboard → wizard | Snippet renderer (`renderSnippet`, `fetchSnippet`) | Brief 04 | Brief 05 |
| 5 | Wizard → storage | `verify_sticky_trigger(pr_id, kind, source, user)` + `POST /verify` endpoint | Brief 05 (both sides) | — |

Gate ordering (after iter-2 serialization):

```
Gate 1: 01 ─┐
Gate 2:     ├─→ 02 ─→ 03 ─→ 04
Gate 3:     └─────────────────→ 05 (depends on 01,02,03,04)
```

## Contracts

### FindingState (Brief 01, in `persistence/storage.py`)

```python
from enum import StrEnum

class FindingState(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    FIXED = "fixed"
    REGRESSED = "regressed"
    VERIFIED = "verified"  # terminal
```

Transitions allowed:

```
open  → dismissed | fixed | verified
fixed → regressed | verified
dismissed → verified
regressed → fixed | verified
verified → (terminal — no further transitions)
```

### Storage helpers (Brief 01)

```python
async def mark_fixed(pr_id: str, signature: str, fixed_by_sha: str) -> None: ...
async def mark_regressed(pr_id: str, signature: str, sha: str, prev_sha: str) -> None: ...
async def mark_verified(pr_id: str, signature: str, user: str) -> None: ...
async def get_finding_states(pr_id: str) -> dict[str, FindingState]: ...
async def infer_fixes(
    pr_id: str,
    prev_sigs: set[str],
    current_sigs: set[str],
    current_sha: str,
) -> tuple[set[str], set[str]]: ...  # (fixed, regressed)
async def verify_sticky_trigger(
    pr_id: str,
    trigger_kind: str,
    trigger_source: str,
    user: str,
) -> None: ...
```

`verify_sticky_trigger` stores into `finding_dismissals` using the
synthetic signature `sha256(pr_id::trigger_kind::trigger_source)[:16]`
(per ADR-004).

### StickyTrigger (Brief 02, in `decision/types.py`)

```python
from dataclasses import dataclass
from typing import Literal

StickyTriggerKind = Literal[
    "new_dep", "path_risk", "hotspot", "trust_tier", "repo_risk", "high_diff",
]

@dataclass(frozen=True)
class StickyTrigger:
    kind: StickyTriggerKind
    label: str        # short human label, e.g. "New dependency: requests==2.32.3"
    source: str       # stable id used for verification (e.g. "requests==2.32.3", "src/auth/")
    reason: str       # one-line explanation
```

### DecisionResult payload shape (Brief 02 — break-cleanly, ADR-002)

```python
# Removed: override_reasons: list[str], trust_tier_reasons: list[str]
# Added:
sticky_triggers: list[StickyTrigger]
finding_reasons: list[str]
```

`/api/dashboard/reviews/{id}` response mirrors the new shape verbatim.

### Verify endpoint (Brief 05)

```
POST /api/dashboard/reviews/{review_id}/verify
Body: { "trigger_kind": "new_dep", "trigger_source": "requests==2.32.3", "user": "alice@…" }
Response: 200 { "verified": true, "signature": "abc123…" }
        | 400 { "error": "unknown trigger_kind" }
        | 404 if review_id unknown
```

Idempotent: posting the same `(pr_id, trigger_kind, trigger_source)`
twice is a no-op success.

## UX flows

### Flow A — Verification mode (auto-entered)

```
Re-run completes → engine returns finding_reasons=[] and sticky_triggers=[T1, T2]
   │
   ▼
User opens /reviews/{id}?mode=wizard
   │
   ▼
Wizard detects condition (no findings + triggers exist) → renders Verification chapter
   │
   ▼
User clicks [Acknowledge & Approve] on T1, then T2
   │
   ▼
POST /verify fires for each → marks verified in finding_dismissals
   │
   ▼
Wizard advances to wrap-up → close.
```

States:
- **Loading**: spinner while wizard loads review payload.
- **Empty**: if both `finding_reasons` and `sticky_triggers` are empty,
  wizard shows existing "no concerns" wrap-up (no new path).
- **Error**: `/verify` 4xx → toast on the card, button re-enables.

Verification chapter wireframe (approved):

```
┌─ Wizard ─────────────────────────────────┐
│  ◉ Verification (1/1)                    │
├──────────────────────────────────────────┤
│                                          │
│  Dev fixed all findings — nice.          │
│  These triggers still need a human eye:  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ 🔗 New dependency                  │  │
│  │ requests==2.32.3 added             │  │
│  │ ─────────────────────────────────  │  │
│  │ Maintained, MIT, no CVEs in scan.  │  │
│  │ [Acknowledge & Approve] [Needs Fix]│  │
│  └────────────────────────────────────┘  │
│                                          │
└──────────────────────────────────────────┘
```

### Flow B — Snippet disclosure (review_detail)

```
User on /reviews/{id} → sees finding card → clicks "Show code"
   │
   ▼
fetchSnippet(reviewId, file, line, context=3) → /api/dashboard/reviews/{id}/diff?…
   │
   ▼
renderSnippet(card, hunk) → .hunk element appended inline below card
   │
   ▼
User clicks again → hunk collapses.
```

States:
- **Loading**: brief muted "loading snippet…" line.
- **Empty / out-of-diff**: muted "snippet unavailable" line.
- **Error**: same muted line — never throws.

Snippet disclosure wireframe (approved):

```
┌─ /reviews/{id} ──────────────────────────┐
│  Finding · medium · security_privacy     │
│  src/api/auth.py:42                      │
│  "Hardcoded JWT secret"                  │
│  ─────────────────────────────────────   │
│  ▸ Show code                             │
│  ─────────────────────────────────────   │
│  [Accept] [Dismiss] [Re-review]          │
└──────────────────────────────────────────┘

after click:
┌─ /reviews/{id} ──────────────────────────┐
│  Finding · medium · security_privacy     │
│  ▾ Show code                             │
│  ┌──────────────────────────────────┐    │
│  │ 39  def get_jwt_secret():        │    │
│  │ 40      # TODO: env var          │    │
│  │ 41-     return "dev-secret-123"  │    │
│  │ 42+     return os.environ["JWT"] │    │
│  └──────────────────────────────────┘    │
└──────────────────────────────────────────┘
```

### Flow C — Trigger-focus mode (deep-linked)

`/reviews/{id}?mode=wizard&focus=trigger:new_dep` opens wizard directly on
the focused trigger card. Other chapters suppressed. Approve/needs-fix
behaves identically to verification mode.

## Reference reading

- `src/pr_guardian/persistence/models.py:290-311` — `FindingDismissalRow`
  shape to extend.
- `src/pr_guardian/persistence/storage.py:623-626` — existing
  `finding_signature()` helper; reuse, don't replace.
- `src/pr_guardian/decision/engine.py:110-145` — `check_overrides()` —
  Brief 02's primary target.
- `src/pr_guardian/decision/engine.py:215-237` — how `trust_tier_reasons`
  and `override_reasons` are currently assembled into `DecisionResult`.
- `src/pr_guardian/triage/classifier.py:36-152` — `_apply_amplifiers`
  pattern; precedent for "sticky-upward" semantics.
- `src/pr_guardian/dashboard/review_detail.html:73-78, 264-268` — the
  `override-reasons-section` markup and JS Brief 02 rewrites.
- `src/pr_guardian/dashboard/human_wizard.html:115-132` — `.hunk` CSS
  primitive and `details.expander` Brief 04 reuses.
- `docs/plan/tiered-trust.md` — design precedent for sticky-upward trust
  classification.
- `alembic/versions/017_add_exclusion_rules.py` — most recent migration;
  use as the template for `018`.
- `specs/inline-pr-comments/briefs/01-add-db-and-config.md` — most recent
  spec using alembic; mirror its migration-AC style.

## Decisions

- **ADR-002** — Sticky-trigger semantic split (break-cleanly). Introduced.
- **ADR-003** — Finding lifecycle state machine. Introduced.
- **ADR-004** — Fix-by-inference (strict, no rename) + synthetic
  signature for sticky-trigger verification storage. Introduced.
- **ADR-001** — `comment_mode` tri-state. Reused as precedent for
  enum-over-boolean (informs `FindingState` shape).
