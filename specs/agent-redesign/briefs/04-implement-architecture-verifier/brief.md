---
title: "Implement architecture anchor discovery"
touches:
  - src/pr_guardian/agents/architecture.py
  - src/pr_guardian/agents/architecture_anchors.py
  - prompts/architecture/base.md
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/config/schema.py
  - src/pr_guardian/triage/classifier.py
  - tests/test_architecture_anchors.py
  - tests/test_architecture_agent.py
does_not_touch:
  - src/pr_guardian/dashboard/
  - src/pr_guardian/persistence/
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
---

## Task

Create the standalone `architecture` verifier. It discovers anchors
cheapest-first, scopes anchors by path for monorepos, and chooses full
verifier, narrow local-pattern, or skipped mode. No anchor means an explicit
skipped agent result, not a finding.

## Context

The current `ArchitectureIntentAgent` is a thin subclass of `BaseAgent`; the
new architecture agent needs runtime anchor discovery before it decides whether
to call an LLM. `plans/architecture-anchor-discovery.md` defines the rank,
weight, and mode rules.

## Constraints

- `review.yml` `architecture_docs` wins when present.
- `architecture.mode_override` may force `auto`, `full_verifier`,
  `narrow_local_pattern`, or `skip`.
- Path-scoped anchors matter for multi-ecosystem monorepos.
- Local-pattern mode emits only low/suspected quote-grounded findings.
- Sibling-only or no-signal mode skips and reports status.
- No anchor caching in v1.

## Test expectations

- Cover rank/mode selection.
- Cover `AGENTS.md` architecture-section filtering.
- Cover path-scoped anchors.
- Cover no-anchor skipped status.
- Cover local-pattern severity/certainty and no global architecture claims.

## Wrap-up

Handover the anchor data shape and status reason consumed by brief 05.
