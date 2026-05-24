# Architecture Intent Split - Design

## Decisions Locked

| Topic | Decision |
|---|---|
| Scope | Full split of `architecture_intent` into `intent` and `architecture`. |
| Architecture skip | Visible in the review detail UI as a pass "Review note". |
| Config compatibility | Add `weights.architecture` and `weights.intent`; keep `weights.architecture_intent` as deprecated input alias when new keys are absent. |
| Legacy support | Keep `ArchitectureIntentAgent` and prompt for old re-review groups; fresh triage does not select it. |
| Skip representation | Use `AgentResult(verdict=pass, verdict_explanation="no architecture context found - agent skipped")`, not a new verdict enum or DB column. |
| Anchor reads | Pre-hydrate anchors through platform adapter repo file APIs; agents receive scoped context, not tool access. |

## Blast Radius

- `src/pr_guardian/config/schema.py` and `src/pr_guardian/config/defaults.yml` gain split weights and architecture config.
- `src/pr_guardian/models/context.py` gains optional architecture and intent anchor sets.
- `src/pr_guardian/models/anchors.py` is added for shared anchor contracts.
- `src/pr_guardian/discovery/architecture_anchors.py` and `src/pr_guardian/discovery/intent_anchors.py` are added.
- `src/pr_guardian/platform/protocol.py`, `src/pr_guardian/platform/github.py`, and `src/pr_guardian/platform/ado.py` gain linked work-item fetch support.
- `src/pr_guardian/agents/architecture.py`, `src/pr_guardian/agents/intent.py`, and new prompt folders are added.
- `src/pr_guardian/agents/context_builder.py` injects scoped anchor blocks only for the matching agents.
- `src/pr_guardian/core/orchestrator.py` hydrates anchors before agent fan-out.
- `src/pr_guardian/triage/classifier.py` and `src/pr_guardian/discovery/change_profile.py` select the split agents.
- `src/pr_guardian/decision/engine.py`, `src/pr_guardian/decision/actions.py`, and `src/pr_guardian/persistence/storage.py` recognize split agents and retain legacy support.
- `src/pr_guardian/dashboard/review_detail.html` renders pass explanations as visible review notes.
- `README.md` updates the documented agent set and config sample.

## Seams and Brief Ordering

1. Brief 01 owns shared contracts and config. All later briefs depend on the types and config surface.
2. Briefs 02 and 03 run in parallel after brief 01. They own architecture and intent anchor discovery independently.
3. Brief 04 depends on both discovery briefs and owns agent classes, prompts, and scoped context injection.
4. Brief 05 depends on brief 04 and owns orchestration, triage, scoring, labels, prompt registry, and legacy re-review wiring.
5. Brief 06 depends on brief 05 and owns the visible review-detail skip state and browser proof.

## Contracts

### Config Contract
Owner: brief 01.

`GuardianConfig.weights` exposes:
- `security_privacy`
- `test_quality`
- `architecture`
- `intent`
- deprecated `architecture_intent`
- `performance`
- `hotspot`
- `code_quality_observability`

If only `architecture_intent` is configured, its value is copied to `architecture` and `intent`. If either new key is present, that new key wins.

`GuardianConfig` exposes:

```yaml
architecture_docs:
  - docs/architecture.md
  - docs/adr/
architecture:
  mode_override: auto
  path_scopes:
    "apps/api/**": [docs/adr/, apps/api/ARCHITECTURE.md]
```

### Anchor Model Contract
Owner: brief 01.

`ReviewContext` carries:
- `architecture_anchors: ArchitectureAnchorSet`
- `intent_anchors: IntentAnchorSet`

The anchor model must be serializable with plain primitives. It includes mode, docs/snippets, warnings, path scopes, source rank, anchor class, and bounded text.

### Architecture Discovery Contract
Owner: brief 02.

Discovery returns per-path or per-scope mode:
- `full_verifier`
- `narrow_local_pattern`
- `skip`

Explicit `architecture_docs` always wins. ADRs with accepted status and machine-enforced architecture configs can support full verifier mode. Structural hints alone only support narrow local-pattern mode. Nothing usable returns skip.

### Intent Discovery Contract
Owner: brief 03.

Intent hydration returns PR claim anchors from:
- PR title.
- PR body.
- Best-effort commit messages.
- Linked GitHub issue or ADO work item.
- Referenced spec files under allowlisted docs/spec/plans paths.

Network or API failures degrade to warnings, not review failure.

### Scoped Context Contract
Owner: brief 04.

`build_agent_context(context, agent_name, ...)` injects:
- `<architecture_anchors>` only when `agent_name == "architecture"`.
- `<intent_anchors>` only when `agent_name == "intent"`.

Security, performance, code quality, test quality, hotspot, and legacy `architecture_intent` stay diff-only unless explicitly changed by a future ADR.

### Agent Identity Contract
Owner: brief 05, ADR-005.

Fresh reviews select `architecture` and `intent`, never `architecture_intent`. `AGENT_REGISTRY` still includes `architecture_intent` so historical findings can be re-evaluated by their original agent identity.

### Visible Skip Contract
Owner: brief 06.

A pass agent result may carry `verdict_explanation`. In review detail, pass explanations render as `Review note`. Warn and flag-human explanations keep `Review focus`.

## UX Flows

Affected screen: existing review detail page at `/reviews/{id}`.

Approved by user: yes, in the plan-feature conversation: "make it visible" and later "both".

```
Review detail
┌─────────────────────────────────────────────────────────────┐
│ Agent findings                                              │
│                                                             │
│ Architecture        [pass]                     0 findings   │
│ ─────────────────────────────────────────────────────────── │
│ Review note: no architecture context found - agent skipped  │
│                                                             │
│ Intent              [warn]                     1 finding    │
│ Review focus: PR body claims X, diff only does Y.           │
└─────────────────────────────────────────────────────────────┘
```

No new screen is introduced. The existing agent card gains one visible note row for pass explanations.

## Reference Reading

- `plans/architecture-anchor-discovery.md` - anchor taxonomy, precedence, thresholds, config surface.
- `plans/agent-redesign.md` - full split and verifier framing.
- `docs/plan/04-ai-agents.md` - current combined architecture and intent behavior.
- `docs/plan/07-architecture.md` - platform adapter and architecture notes.
- `docs/decisions/ADR-002-sticky-trigger-split.md` - break-cleanly precedent for internal payloads.
- `docs/decisions/ADR-004-fix-by-inference.md` - agent name participates in finding signatures.
- `AGENTS.md` - repo commands and runtime assumptions.
- `README.md` - documented current six-agent set and `weights.architecture_intent` config.
- `src/pr_guardian/core/orchestrator.py` - review pipeline and agent fan-out.
- `src/pr_guardian/agents/context_builder.py` - user prompt construction.
- `src/pr_guardian/config/schema.py` - config model.
- `src/pr_guardian/triage/classifier.py` - agent selection.
- `src/pr_guardian/discovery/change_profile.py` - implied agents.
- `src/pr_guardian/decision/engine.py` - score weights.
- `src/pr_guardian/dashboard/review_detail.html` - visible agent result surface.

## Required Facts by Brief

- Brief 01 validates config and shared context contracts.
- Brief 02 validates architecture discovery modes, precedence, path scopes, AGENTS filtering, and stale demotion.
- Brief 03 validates intent anchor extraction, platform work-item fetch fallback, and missing-anchor behavior.
- Brief 04 validates scoped anchor context, no-LLM architecture skip, and pass status notes.
- Brief 05 validates split-agent triage, implied architecture selection, scoring, and legacy re-review.
- Brief 06 validates UI-visible architecture skip with a browser test and payload preservation.

## Decisions and ADRs

- Introduce `docs/decisions/ADR-006-split-verifier-agent-identity.md`.
