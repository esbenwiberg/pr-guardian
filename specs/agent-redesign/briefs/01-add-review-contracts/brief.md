---
title: "Add review contracts and split taxonomy"
touches:
  - src/pr_guardian/models/findings.py
  - src/pr_guardian/config/schema.py
  - src/pr_guardian/decision/engine.py
  - src/pr_guardian/decision/actions.py
  - tests/test_decision.py
  - tests/test_triage.py
  - tests/test_agent_contracts.py
does_not_touch:
  - src/pr_guardian/agents/
  - src/pr_guardian/dashboard/
  - src/pr_guardian/persistence/
  - alembic/
---

## Task

Introduce the shared contracts used by the redesign: `Finding.quote`,
`AgentResult.status`, `AgentResult.status_reason`, split agent names `intent`
and `architecture`, and default weights of `1.0` each. Remove the old
`architecture_intent` default weight and label instead of preserving
compatibility.

## Context

The current combined agent name appears in the default weights, PR summary
labels, triage `ALL_AGENTS`, and `ChangeProfile.implied_agents`. This brief
owns the shared shape and scoring semantics so later briefs can focus on
agent-specific behavior.

## Constraints

- No migration or backwards compatibility for old `architecture_intent` config
  or prompt override names.
- `status="skipped"` is explicit and non-scoring; it is not equivalent to
  `verdict="pass"`.
- Skipped agent results still exist in `ReviewResult.agent_results` so they can
  be persisted and displayed later.
- Do not implement agent prompts or dashboard rendering in this brief.

## Test expectations

- Cover skipped-agent scoring and matrix behavior.
- Cover split agent names, default weights, and display labels.
- Cover that `Finding.quote` exists on the shared dataclass.

## Wrap-up

Record the exact `AgentResult.status` and `Finding.quote` field names in the
handover for later briefs.
