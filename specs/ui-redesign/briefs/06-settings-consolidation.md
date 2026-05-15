# Brief 06 — Consolidate /settings, /prompts, /admin under one admin-only page

## What
Fold `/settings` (LLM provider), `/prompts` (agent prompt editor), and `/admin` (API keys, admins, GitHub PATs, exclusions) into a single `/settings` page with anchored sections. Gate the entire page to admins. Non-admins never see the Settings nav item and are redirected if they try to visit directly.

## Why
The current split is incidental: three separate top-level destinations for what's really one role's job — "configure Guardian." Admins shouldn't have to remember which page holds which control.

## Where
- New: `src/pr_guardian/dashboard/settings.html` (replaces existing settings.html, prompts.html, admin.html). Use anchored sections (`<section id="llm">`, `<section id="prompts">`, etc.) so the redirects from brief 01 (`/prompts` → `/settings#prompts`) work.
- `src/pr_guardian/api/dashboard_page.py` — `/settings` route serves this; admin-gate via `is_admin(request)`.
- Backend APIs for each section already exist (`/api/admin/*`, `/api/prompts/*`, `/api/settings/*`) — reuse, don't rebuild.
- `src/pr_guardian/auth/` — confirm or add `is_admin(request)` (already needed by brief 01).

## Page layout
```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Settings                                                                      │
│  ──────────────                                                                │
│  • LLM Provider                                                                │
│  • Agent Prompts                                                               │
│  • API Keys                                                                    │
│  • Admins                                                                      │
│  • GitHub PATs                                                                 │
│  • Exclusions                                                                  │
├──────┬─────────────────────────────────────────────────────────────────────────┤
│ Nav  │ Section content                                                         │
│ rail │                                                                         │
│      │  ## LLM Provider                                                        │
│ LLM  │  [ Anthropic Direct | Azure AI Foundry ]                                │
│ Promp│  API key: [ ******** ]   [ Save ]                                       │
│ Keys │                                                                         │
│ Admin│  ## Agent Prompts                                                       │
│ PATs │  Tabs: architecture / code_quality / hotspot / ... ───────────          │
│ Excl │  [ <textarea with prompt body> ]  [ Save ]                              │
│      │                                                                         │
│      │  ## API Keys                                                            │
│      │  ...                                                                    │
└──────┴─────────────────────────────────────────────────────────────────────────┘
```

Left rail is anchor navigation (intersection observer highlights the active section). Right column is one continuous scroll with all sections — each section's existing content drops in as-is.

## Admin gating
Server-side:
```python
@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not is_admin(request):
        return RedirectResponse("/reviews?error=admin_required", status_code=302)
    return HTMLResponse(_SETTINGS_HTML.read_text())
```

Client-side gating happens via sidebar.js (brief 01) — Settings item only renders for admins. Server-side is the authoritative check.

Non-admins who follow a deep link (`/settings#prompts`) land on `/reviews` with a one-shot toast: "Settings is admin-only."

## Section sources (existing files — adopt their content)
| Section | Source file | Source API |
|---|---|---|
| LLM Provider | `settings.html` | `/api/settings` |
| Agent Prompts | `prompts.html` | `/api/prompts/*` |
| API Keys | `admin.html` (top section) | `/api/admin/keys` |
| Admins | `admin.html` (middle section) | `/api/admin/admins` |
| GitHub PATs | `admin.html` (middle section) | `/api/admin/pats` |
| Exclusions | `admin.html` (bottom sections) | `/api/admin/exclusions`, `/api/admin/excluded-repos` |

The "Agent API · NEW" promo card that lives in sidebar.js today moves into the `API Keys` section as its empty-state callout.

## Success signal
- Admin user visits `/settings` — sees all six sections, can configure each.
- Admin opens `/settings#prompts` — scrolls to (or anchors at) the Prompts section, with that nav-rail item highlighted.
- Non-admin user visits `/settings` — redirected to `/reviews` with "admin-only" toast.
- Non-admin user does not see Settings in sidebar.
- All existing config still works (LLM key save, prompt edit, key create, admin add, PAT add, exclusion add, repo exclude).

## Non-goals
- Per-section route paths (`/settings/llm`, `/settings/prompts`). Single page with anchors is simpler and works with the redirects.
- New settings, new fields. This is consolidation only.
- Audit log / change history UI. Future.
- Role granularity beyond admin/non-admin. The existing model is the model.

## Validation
1. Admin user (`GUARDIAN_DEV_ADMIN=1`) opens `/settings` — all six sections render.
2. Click each rail item — content scrolls to the right anchor; rail highlights update.
3. Save changes in each section — they persist (existing APIs).
4. Non-admin opens `/settings` — redirected with toast.
5. Non-admin's sidebar shows three items, no Settings.
