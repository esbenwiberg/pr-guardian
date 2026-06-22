# ADR-012: Auto-approve is configuration-gated; archmap retires built-in globs; dependency changes escalate

## Status

Accepted — 2026-06-22. Amends ADR-005 (final auto-approval gate). Reuses the
`new_dep` sticky-trigger kind from ADR-002's closed set — no new kind is added.

## Context

In practice, auto-approve never fired: every PR came back `HUMAN_REVIEW`. The
cause was the hardcoded built-in trust-tier globs in
`triage/trust_classifier.py` (`_BUILTIN_RULES`). Patterns like `**/config/**`,
`**/services/**`, and `**/middleware/**` match almost any repository regardless
of language (a .NET, Go, or Python layout share none of these conventions), so
the trust-tier gate floored nearly every PR to `MANDATORY_HUMAN` before scoring
even ran. The globs were a one-size-fits-all guess masquerading as a safety net.

Separately, dependency-change escalation (`adds_dependencies`) caught manifest
adds and version bumps but missed the most common supply-chain vector —
lockfile-only churn (`package-lock.json`, `poetry.lock`, `go.sum`, …, all
classified `GENERATED`) — and dependency removals. It was also not configurable.

The backend for per-profile overrides already existed (`trust_tiers`,
`path_risk`, `security_surface` are profile keys, persisted and merged at review
time); only the editor UI was missing. We deliberately do **not** reintroduce a
repo-root `review.yml` — config lives in the profile row and is edited in-app,
so a rogue agent committing to a repo cannot change its own review policy.

## Decision

1. **Auto-approve is locked unless the repo is configured to be judged.** A
   verdict can only be `AUTO_APPROVE` when `auto_approve_unlocked` is true:

   ```
   auto_approve_unlocked = bool(config.trust_tiers.rules) or archmap_available
   archmap_available      = bool(context.archmap.files) and not context.archmap.error
   ```

   With neither explicit trust-tier rules nor archmap, every PR goes to a human.
   This replaces "on by default + dumb globs" with an intentional precondition:
   Guardian won't auto-approve a repo it hasn't been told how to judge. The
   signal is a replayable structural input on `resolve_decision`, persisted in
   the review's `override_reasons` blob so re-review yields the same verdict.
   Legacy rows (pre-gate) default to locked — the safe direction.

2. **Archmap retires the built-in globs.** When archmap topology is available
   and a profile sets no explicit `trust_tiers.rules`, `classify_trust_tier`
   applies no path globs: every file falls to `default_tier` and escalation is
   driven by the `archmap_hub` sticky trigger (real topology) instead of guessed
   path conventions. Explicit `trust_tiers.rules` still win over archmap; a
   customized `security_surface` (Layer 2) still applies and still escalates via
   the `path_risk` sticky regardless. The built-ins survive only as the
   last-resort Layer 1 fallback (reached only when the gate is already locked).

3. **Dependency-change escalation is widened and per-profile.** A new
   `DependencyPolicyConfig` (`require_human` / `include_lockfiles` /
   `include_removals`, all default true) gates the `new_dep` sticky. Detection
   now also flags lockfile-only changes (by filename) and dependency removals
   (the manifest parsers were generalized to inspect deleted lines too). Bot
   authors remain exempt earlier via `auto_approve.exempt_authors`, so this
   gates human-authored package changes — low noise, strong supply-chain
   posture.

4. **The glob editors are exposed in the Profile UI.** `trust_tiers`,
   `path_risk`, and `security_surface` get in-app editors, seeded from a new
   `GET /api/profiles/config-defaults` endpoint (single source of truth for the
   built-ins). An auto-approve status banner shows operators whether a profile
   is locked or enabled.

## Consequences

- **Fleet-wide swing.** Repos with archmap available begin producing real
  auto-approves for clean PRs; unconfigured, archmap-less profiles stay fully
  human. Worth watching the first batch after rollout.
- **`default_tier` now matters** for archmap-unlocked repos (it governs files
  that match no rule); `spot_check` (the default) is auto-approve-eligible.
- **Re-review of an archmap-only unlock** replays the stored bool because
  archmap is a runtime signal not recomputable at re-review time.
- No new `StickyTriggerKind` — dependency lockfile/removal escalations reuse
  `new_dep`, so ADR-002's closed set is unchanged.
