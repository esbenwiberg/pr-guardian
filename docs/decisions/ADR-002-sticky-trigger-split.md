# ADR-002: Sticky-trigger semantic split (break-cleanly)

## Status
Accepted — 2026-05-25. Implemented in `DecisionResult.sticky_triggers`,
`DecisionResult.finding_reasons`, dashboard payloads, and storage compatibility
for legacy `override_reasons` rows.

**Amendment — ADR-011 (2026-06-17):** `gate_agent` added to the closed kind set
to support the structural-only escalation policy. See
`docs/decisions/ADR-011-structural-only-escalation.md`.

ADR-005 is a proposed future amendment; it is not part of the live trigger
contract until accepted and implemented.

## Context
`check_overrides()` in `decision/engine.py` currently produces two flat string
arrays on `DecisionResult`: `override_reasons` and `trust_tier_reasons`. The
dashboard payload at `/api/dashboard/reviews/{id}` exposes both verbatim and
`review_detail.html` renders them as a single "override reasons" panel.

The verification-mode feature needs to distinguish two semantic classes of
reason:

- **Structural / sticky** — tied to the repo, the PR's paths, or guardian
  config (new dependency added, critical path touched, hotspot file, trust-tier
  escalated, repo risk class, HIGH diff amplifier). Persists across re-runs
  until the underlying condition disappears.
- **Finding-derived / transient** — tied to specific findings ("3 high-sev
  findings", "FLAG_HUMAN agent verdict"). Clears the moment findings are
  addressed.

The wizard needs to know: "is the only reason for human review now
finding-derived (and have those findings since been fixed)?" That question is
unanswerable from a single flat array. A back-compat union field would leave
two ways of doing the same thing and force every future consumer to choose;
worse, it would let `review_detail.html` keep reading the old flat list while
the wizard reads the new fields, splitting the source of truth.

## Decision
Split the engine output into two disjoint buckets on `DecisionResult` and the
dashboard payload:

- `sticky_triggers: list[StickyTrigger]` — structural reasons. Each entry
  carries `kind | label | source | reason`.
- `finding_reasons: list[str]` — finding-derived reasons.

**Break cleanly**: remove `override_reasons` and `trust_tier_reasons` from
`DecisionResult` and from the dashboard payload in the same brief that adds
the new fields. No transitional union field. The only existing consumer
(`review_detail.html`) migrates in the same brief.

The sticky-trigger `kind` set is closed:
`new_dep | path_risk | hotspot | trust_tier | repo_risk | high_diff | archmap_hub | gate_agent`.
ADR-005 proposes adding `config_policy` and structured
`StickyTrigger.details`; those are not accepted live contract fields yet.
Adding any further kind requires another ADR amendment, not a silent enum
extension.

## Consequences

**Easier:** verification mode becomes expressible — `finding_reasons` empty
AND `sticky_triggers` non-empty is the condition. One canonical shape for
escalation reasons; no stale flat field lying around for the next dev to
wonder about.

**Harder:** any external API consumer reading `override_reasons` or
`trust_tier_reasons` from `/api/dashboard/reviews/{id}` breaks. This is
internal-only payload — risk acceptable per user decision.

**Committed to:** the closed kind set is a contract. Future structural reasons
must either map to an existing kind or trigger an ADR update. If ADR-005 is
accepted, the closed set will expand to include `config_policy`. ADR-011
expanded the set with `gate_agent` (2026-06-17).
