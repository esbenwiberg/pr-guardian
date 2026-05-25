# ADR-006: Split verifier agent identity with legacy re-review support

## Status
Proposed — 2026-05-25. Not implemented. Fresh reviews still use the
`architecture_intent` agent identity; `intent` and `architecture` do not yet
exist as live review agents.

## Context
PR Guardian currently uses one `architecture_intent` agent for two verifier jobs:

1. Verify that the diff matches the PR author's stated intent.
2. Verify that the diff respects the repo's stated architecture.

The seed plans split this into two agents, `intent` and `architecture`, because they need different anchors and different missing-anchor behavior. `intent` can treat a missing claim as a problem on medium/high changes. `architecture` should skip when there is no architecture ground truth, because otherwise it produces subjective review noise.

Agent names also participate in durable behavior. ADR-004 defines finding signatures as `file::category::agent`; changing agent names changes future finding signatures. The dashboard and README currently expose `architecture_intent` in labels, prompt override lists, and sample config.

## Decision
Fresh reviews use two new verifier identities:

- `intent`
- `architecture`

`architecture_intent` is retained only as a legacy identity for historical re-review and existing prompt overrides. Fresh triage no longer selects it.

Add new config weights:

- `weights.intent`
- `weights.architecture`

Keep `weights.architecture_intent` as a deprecated input alias. When a config only sets the legacy key, copy that value to both new weights. When a new key is present, that new key wins.

Represent architecture skip as:

```python
AgentResult(
    agent_name="architecture",
    verdict=Verdict.PASS,
    findings=[],
    verdict_explanation="no architecture context found - agent skipped",
)
```

Do not add a new `skipped` verdict enum or DB column. The existing persisted `verdict_explanation` field carries the status note, and the review detail UI renders pass explanations as `Review note`.

## Consequences

**Easier:** Fresh review behavior has clear agent roles. Architecture can no-op cheaply and visibly when no anchor exists. Existing DB schema can store the skip note.

**Harder:** New findings from `architecture` and `intent` have new agent names, so their signatures differ from historical `architecture_intent` findings. Prompt overrides are not automatically split; operators must review old combined overrides manually.

**Committed to:** `architecture_intent` must remain resolvable for focused re-review until historical reviews can age out or a migration is explicitly designed. The skip status text is a stable UI/test contract: `no architecture context found - agent skipped`.

## Alternatives Rejected

- **Keep one combined agent and add better prompt instructions.** Rejected because the two verifier jobs need different anchors and different missing-anchor semantics.
- **Rename `architecture_intent` in place.** Rejected because it would strand historical re-review groups and make signature changes implicit.
- **Add a `skipped` verdict.** Rejected for this feature because it requires schema and decision-engine changes without adding useful scoring behavior; a pass note is enough and keeps the change smaller.
- **Break `weights.architecture_intent` immediately.** Rejected because README documents it as repo config, so a short compatibility alias protects existing users while preserving the cleaner new keys.
