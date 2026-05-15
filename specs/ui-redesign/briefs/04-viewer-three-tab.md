# Brief 04 — Review viewer (3-tab: Wizard · Chapters · Findings)

## What
Wrap the three existing viewer pages (`human_wizard.html`, `human_review.html`, `review_detail.html`) into a single `/reviews/{id}` surface with a mode-switcher in the header. The system picks a default based on PR size + findings count; the user can flip modes at any time, and progress (decisions, scroll position, comment draft) is preserved across flips.

## Why
Today there are three different URLs for the same review, with no visible mode toggle. Reviewers either bookmark one and never see the others, or get confused by the three-way overlap. One viewer, three modes is the clean model.

## Where
- New: `src/pr_guardian/dashboard/review_viewer.html` — thin wrapper that:
  - Renders the header (breadcrumb, verdict chip, mode switcher, "Open in platform").
  - Loads the review data once.
  - Mounts the active mode's body (Wizard / Chapters / Findings) into a content slot.
  - Persists decisions/draft/scroll into a shared `ReviewState` object on `window`.
- `src/pr_guardian/api/dashboard_page.py` — `/reviews/{id}` serves this template; old `/wizard` and `/human-review` paths redirect here with `?mode=`.
- Keep the existing JS that each viewer ships, but extract the body markup into module-style functions (`renderWizardBody(state)`, `renderChaptersBody(state)`, `renderFindingsBody(state)`) sharing a single `ReviewState`.

## Mode default heuristic
First visit (no `mode` param, no `localStorage["prg:viewer_mode"]`):
- `findings_count ≤ 2 AND capabilities ≤ 1` → `findings`
- `findings_count ≤ 10 AND files ≤ 20` → `chapters`
- otherwise (or `subject_type == "scan"`) → `wizard`

After the user explicitly picks a mode (clicks a tab), persist `localStorage["prg:viewer_mode"]` and honour it on the next review. A URL `?mode=` always wins for the current page load.

## State preserved across mode flips
Single object on the viewer page:
```ts
type ReviewState = {
  review_id: string,
  decisions: Record<finding_id, "accept" | "fix" | "dismiss">,
  comment_draft: string,                  // wrap-up textarea
  scroll: Record<mode, number>,           // pixels per mode
  active_step: number,                    // wizard step index
  expanded: Record<id, boolean>,          // chapters/findings expand states
}
```

On mode flip, the new mode reads from this object and renders accordingly. Wizard "current step" attempts to map to the chapter containing the first un-decided finding when entering from Chapters/Findings.

## Header
```
← Reviews   PR #482 · feat/auth-refactor   ⚠ Human review · score 7.2 · HIGH
            [ Wizard | Chapters | Findings ]              [Open in GitHub →]
```

- Breadcrumb (left): `← Reviews`.
- Title: PR title or scan name.
- Verdict chip (right of title): colour-coded per decision.
- Mode switcher: 3-tab segmented control. Sticky on scroll. Keyboard `1/2/3` to switch.
- "Open in platform" link: GitHub or ADO URL.

## Visual polish — mode bodies
- **Wizard** (`human_wizard.html`): adopt as-is. Step pips kept. Briefing-style prose typography. Per-concern Accept/Fix/Dismiss inline buttons.
- **Chapters** (`human_review.html`): adopt as-is. Right-rail chapter map kept (was already there).
- **Findings** (`review_detail.html`): adopt as-is, but strip its duplicate decision banner — the new header replaces it.

The shared header's "verdict chip" is the single source of truth; sub-pages stop rendering their own banner.

## Repo-scan handling
For `subject_type == "scan"`:
- Default mode is always `wizard`.
- Wizard "Briefing" reads "Reviewing full repo {owner}/{repo} as a snapshot. {n} files in {m} capabilities."
- Capabilities are derived from path grouping (`src/`, `tests/`, `infra/`, etc.).
- Each file is treated as added; diffs show file contents.
- Wrap-up still posts back, but to the repo's default branch as a discussion / scheduled scan summary comment (extend `inline-pr-comments` to handle this in brief 05).

## Success signal
- `/reviews/{id}` loads in the default mode based on size.
- Mode switcher works; flipping preserves Accept/Fix/Dismiss decisions, comment draft, and scroll.
- `?mode=wizard` URL forces wizard mode.
- Repo-scan reviews default to wizard, with "snapshot" briefing copy.
- Keyboard `1/2/3` switches modes.

## Non-goals
- Building the wrap-up post-back (brief 05).
- Adding new modes beyond the three existing.
- Cross-mode finding ID alignment work beyond what exists — if a Wizard finding doesn't already have a matching Chapters/Findings entry by `id`, that's a data fix, not a viewer fix.
- Mobile / narrow viewport — viewer is desktop-only.

## Validation
1. Open a small review (≤2 findings) — defaults to Findings mode.
2. Open a medium review — defaults to Chapters.
3. Open a large review or any scan — defaults to Wizard.
4. Flip Wizard → Chapters mid-review; decisions and comment draft preserved.
5. URL `?mode=findings` forces Findings on a large review.
6. Reload after picking a mode — that mode is the default for subsequent reviews.
