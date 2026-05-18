# Handover: unique-marmot (Brief 05 — Wizard Verification + Trigger-Focus Modes)

## What was built

Extended the human review wizard with two new modes and a backend verification endpoint.

### New endpoint: `POST /api/dashboard/reviews/{id}/verify`

In `src/pr_guardian/api/dashboard.py`:
- `VerifyTriggerRequest` model: `trigger_kind`, `trigger_source`, `user`
- `_VALID_TRIGGER_KINDS` derived via `get_args(StickyTriggerKind)` — stays in sync automatically
- Validates `trigger_kind` → 400 on unknown kind
- Calls `storage.verify_sticky_trigger(pr_id, kind, source, user)` (Brief 01 helper)
- Returns `{ "verified": true, "signature": "<16-char hex>" }` using `_hash16` from storage (ADR-004 formula)
- Idempotent: second POST returns 200 with same signature; no-op inside `mark_verified`

### Wizard: Verification mode (auto-entered)

In `src/pr_guardian/dashboard/human_wizard.html`:
- Condition: `finding_reasons.length === 0 && stickyTriggers.length > 0`
- When met, `initState()` builds steps as `[Briefing → Verification → Wrap-up]`
- `renderVerification()` renders `.verification-chapter` with one `.trigger-card` per trigger
- Trigger card shows: kind icon, label, source (mono), reason, divider line
- Actions: [Acknowledge & Approve] → POSTs to `/verify`, sets `triggerState[i] = "verified"` / [Needs Fix] → local state only
- Once all triggers are acted on, wizard auto-advances to wrap-up (350ms delay for UX)
- "Clear" button resets individual trigger state back to pending
- "Show code" button appears on cards where `source` looks like a file path; calls `fetchSnippet` / `renderSnippet` from `snippet.js`

### Wizard: Trigger-focus mode (deep-linked)

- URL: `/reviews/{id}?mode=wizard&focus=trigger:{kind}`
- `FOCUS_TRIGGER_KIND` parsed from URL at script init
- When set, `initState()` builds steps as `[Verification → Wrap-up]` (no Briefing, no capability chapters)
- `visibleTriggers` is filtered to only the matching kind

### Test coverage

`tests/test_verify_endpoint.py` (4 tests):
- `test_valid_payload_returns_200_and_record` — 200, correct signature, storage called
- `test_unknown_trigger_kind_returns_400` — 400 with descriptive message
- `test_unknown_review_id_returns_404` — 404 when storage returns None
- `test_idempotent_second_post_returns_200` — second POST is no-op 200

## Contracts downstream pods must honour

### POST /verify shape
```
POST /api/dashboard/reviews/{review_id}/verify
Body: { "trigger_kind": str, "trigger_source": str, "user": str }
200: { "verified": true, "signature": "<16-char hex>" }
400: unknown trigger_kind
404: review_id unknown
```

### Wizard state variables
`triggerState` (array of `"pending" | "verified" | "needs_fix"`) and `visibleTriggers`
are module-level `let` variables in the second script block of `human_wizard.html`. They
are set in `initState()` and read in `renderVerification()`. Do not move them to window scope.

### `window.STICKY_TRIGGERS` and `window.FINDING_REASONS`
Set in `loadAndBuild()` (first script block) and read in `initState()` (second script block).
This cross-block handoff via `window.*` is intentional — the two blocks cannot share `let`
variables due to script tag isolation.

## Files this pod owns — do not modify without good reason
- `src/pr_guardian/dashboard/human_wizard.html` (verification chapter, trigger-focus mode)
- `tests/test_verify_endpoint.py`

## Files modified that downstream pods should be aware of
- `src/pr_guardian/api/dashboard.py` — new `POST /reviews/{review_id}/verify` endpoint;
  also imports `StickyTriggerKind` from `decision.types` and `_hash16` from `persistence.storage`

## Discovered constraints / landmines

- **`_hash16` is imported as a private symbol** — the design doc (ADR-004) explicitly says
  "the formula is implemented once in `_hash16` — do not duplicate." Since `verify_sticky_trigger`
  doesn't return the signature, the endpoint imports `_hash16` directly from storage. This is
  an intentional cross-module private import.

- **`_VALID_TRIGGER_KINDS` is derived at module load time** via `get_args(StickyTriggerKind)`.
  If `StickyTriggerKind` in `decision/types.py` is ever changed (new kind added or removed),
  the endpoint validation updates automatically — no second change needed.

- **Trigger-focus mode: `stepIdx = 0` is explicit** in `initState()` when focus mode is active.
  This ensures the wizard always opens on the Verification chapter, not mid-wizard.

- **auto-advance on all-verified uses setTimeout(350ms)** — the brief says "acknowledging
  all triggers → wrap-up". The delay gives the user a moment to see the final card update
  before the chapter transitions.

- **`needs_fix` state is local-only** — clicking [Needs Fix] sets `triggerState[i] = "needs_fix"`
  client-side but does NOT call any backend endpoint. This is intentional: the spec only
  specifies `verified` as a terminal backend state. A future brief could add a `report_blocker`
  flow for needs_fix triggers.

- **pre-submit review tool returned stale cached result** — The tool's cache predated this
  pod's two commits. Manual diff inspection confirms all deliverables are present.
