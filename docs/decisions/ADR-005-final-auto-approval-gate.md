# ADR-005: Final auto-approval gate and config policy trigger

## Status
Proposed

## Context

Auto-approval currently depends on defaults and on whichever path reaches
`Decision.AUTO_APPROVE`. Two re-review branches can return auto-approval when
all original findings are dismissed or all re-evaluated findings are resolved.
Those branches do not re-run target-branch config consent, hotspot checks, or
root config edit policy before platform side effects call `approve_pr()`.

ADR-002 also made the sticky-trigger kind set closed. The new auto-approve
policy needs a structural reason that is neither path risk nor finding-derived:
missing/invalid/incomplete auto-approve consent and current-path root
`review.yml` edits. Reusing `path_risk` would hide config-policy semantics from
the wizard, audit payload, and future reviewers.

## Decision

Add one final auto-approval gate. Any automated path that would approve a PR
must first become a candidate auto-approval, pass through this gate, and only
then reach platform side effects. The gate evaluates target-branch config
consent, current-path root `review.yml` edits, respected hotspot hits, hotspot
lookup failures, and existing structural triggers.

Add `config_policy` to `StickyTriggerKind`. Add
`details: dict[str, Any] = field(default_factory=dict)` to `StickyTrigger` so
hotspot and config-policy explanations can cross the decision, storage, API, and
UI boundary without template-specific inference.

The root config edit policy is intentionally narrow in v1: block only when the
current diff path is exactly `review.yml`. Do not inspect `old_path` for
rename-away/delete cases until a later feature chooses to broaden the policy.

Manual human verdict approval remains outside this invariant because it is not
auto-approval.

## Consequences

Easier: all automated approval paths share one invariant, so new gates cannot be
accidentally skipped by re-review shortcuts.

Harder: re-review orchestration needs to carry enough context to run the same
final gate that initial reviews run, even when the agent work is skipped or
minimal.

Committed to: `config_policy` is a first-class structural trigger kind and
`StickyTrigger.details` is the durable cross-boundary explanation contract.
Future structural trigger kinds still require an ADR update.
