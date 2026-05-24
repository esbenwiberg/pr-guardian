---
description: Scaffold a new review agent end-to-end (prompts, class, triage wiring, tests).
---

Adding a new specialist review agent ("foo") touches several places. Walk
through them in this order and stop after each step to confirm.

Ask first if not provided:
- What does the agent review? (one sentence)
- What certainty signals can it rely on? (file/line citations? cross-file
  patterns? runtime evidence?)
- Which risk tiers should trigger it? (`trivial` / `low` / `medium` / `high`)

Then:

1. **Prompts** — create `prompts/foo/` with `system.md` and `user.md`. Use a
   neighboring agent (e.g. `prompts/security_privacy/`) as the shape
   reference. Prompts must demand evidence anchors.

2. **Agent class** — add `src/pr_guardian/agents/foo.py`. Subclass `BaseAgent`.
   Implement `run(context) -> AgentResult`. Use `llm.render_prompt("foo/...")`
   — never inline prompts in Python.

3. **Triage wiring** — register the agent in `triage/` so the risk classifier
   knows when to schedule it. Decide: always-on, or only above a tier?

4. **Decision weighting** — open `decision/` and decide whether the new agent's
   findings need bespoke weighting. Default is the standard weight; only
   override with a rationale.

5. **Tests** — new file in `tests/test_foo_agent.py`. Minimum coverage:
   - happy path produces a finding with evidence anchor
   - missing evidence ⇒ certainty drops to `uncertain`
   - storage round-trip preserves all fields
   - the validator agent accepts the produced certainty

6. **Docs** — add the agent to the table in `ARCHITECTURE.md` and to
   `README.md`'s agent table.

7. **ADR if appropriate** — if the new agent introduces a new boundary or
   reuses an existing one in a novel way, write an ADR in `docs/decisions/`.

8. **Smoke** — run `/smoke` before committing.

Anti-patterns to flag if you spot them:
- Agent makes claims at `detected` without a citation
- Agent calls `persistence/` directly (must go via orchestrator)
- Agent imports from `api/` or `mechanical/` (wrong layer)
