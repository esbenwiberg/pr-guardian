# Verification mode and sticky triggers

## Problem

When PR Guardian flags a PR for human review, the developer often fixes the
findings themselves before the reviewer ever opens the wizard. By the time
the reviewer arrives, every concern card the wizard wanted to walk them
through is stale — the code it points to no longer exists. The wizard
either disappears entirely (giving the impression "nothing to check, just
approve") or surfaces empty chapters. Either way the structural reasons
the PR was flagged in the first place — a newly added dependency, a hit on
a critical path, a hotspot file, a trust-tier escalation — quietly get no
attention.

Separately, every finding card on `/reviews/{id}` and inside the wizard
shows the file and line of the issue but never the relevant code itself.
A reviewer has to flip to the diff in another tab to make sense of what
the agent is talking about.

## Outcome

When a developer fixes all findings themselves before review, the wizard
switches to **verification mode**: it surfaces only the sticky structural
triggers (new deps, critical paths, hotspots, trust-tier escalations) as
explicit Acknowledge / Needs-Fix cards. The human spends their attention
on the things that survive the fix, not on stale findings. Every finding
card — on `/reviews/{id}` and inside the wizard — also exposes the
relevant code snippet inline with one click.

## Users

- **PR authors** who fix findings themselves between the initial review
  and the human review step — they want their fixes recognised, not
  forced through a wizard that's pretending the findings still exist.
- **Reviewers** who want to focus on structural concerns (this PR added a
  new dep / touches a critical path) without re-reading findings that no
  longer exist.
- **Compliance / audit readers** who need a record of *who acknowledged
  what* on a per-trigger basis, not just a PR-level approval.

## Success signal

When a re-run produces zero open findings but at least one sticky
trigger, opening `/reviews/{id}?mode=wizard` renders a Verification
chapter that lists exactly the surviving triggers as cards. Clicking
[Acknowledge & Approve] on every card completes the wizard and the
trigger is persisted as `verified` in `finding_dismissals`. Independently,
clicking "Show code" on any finding card (review_detail or wizard) renders
the relevant hunk inline.

## Non-goals

- Changing risk-tier or trust-tier classification logic — they already
  produce the right outputs; only how their reasons are surfaced changes.
- Adding rename or move detection to fix inference — strict
  `file::category::agent` signature equality only.
- Adding fine-grained per-line signatures — file+category+agent stays
  coarse on purpose.
- Building a generic state-machine framework — the lifecycle is hard-coded
  in the helpers, not configurable.
- Changing the agent set, finding categories, or any agent prompt.
- Building cross-PR verification history. Verification records are scoped
  to a single PR.
- Rename of the `finding_dismissals` table (despite the lifecycle making
  the name a bit of a misnomer now) — left for a later cleanup pass.

## Glossary

- **Sticky trigger** — an escalation reason tied to the repo, the PR's
  paths, or guardian config (not to a specific finding). Persists across
  re-runs until the underlying condition disappears. Kinds:
  `new_dep | path_risk | hotspot | trust_tier | repo_risk | high_diff`.
- **Finding reason** — an escalation reason derived from one or more
  specific findings (e.g. "3 high-sev findings"). Clears the moment the
  findings are addressed.
- **Addressed** — umbrella term for a finding that is either `fixed`
  (signature gone on re-run) or `dismissed` (human marked it OK).
- **Fixed** — a finding signature present on a previous run is absent on
  the current run. Inferred automatically; never user-set.
- **Regressed** — a finding signature previously marked `fixed` appears
  again on a later run.
- **Verified** — a finding or sticky trigger has been explicitly
  acknowledged by a human via the wizard. Terminal state.
- **Verification mode** — wizard mode entered automatically when a PR has
  zero open findings AND at least one sticky trigger.
- **Trigger-focus mode** — wizard mode entered via deep-link
  (`?mode=wizard&focus=trigger:{kind}`) to walk the human through one
  specific trigger.
- **Synthetic signature** — for sticky-trigger verification storage:
  `sha256(pr_id::trigger_kind::trigger_source)[:16]`, reusing the
  existing `finding_dismissals` row shape.

## Reversibility

The feature adds nullable columns to `finding_dismissals` via alembic
migration `018`. Roll back with `alembic downgrade -1`. No existing data
is mutated; the new columns are all-NULL on existing rows and the
existing `status` column continues to drive the `dismissed` path.

UI changes (snippet disclosure, verification chapter) ship as additive
markup behind no feature flag — rollback is a code revert.

The decision-engine break-cleanly migration (`override_reasons` /
`trust_tier_reasons` → `sticky_triggers` / `finding_reasons`) is the
single non-reversible bit: any external API consumer reading the old
fields breaks. Per ADR-002 this is internal-only payload, so the risk
is accepted.
