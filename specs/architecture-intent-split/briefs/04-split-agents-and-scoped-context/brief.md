# Brief 04 - Split agents and scoped context

## Task
Create the `architecture` and `intent` agents. `architecture` consumes discovered architecture anchors and skips deterministically when the mode is `skip`. `intent` consumes PR/work-item/spec anchors and verifies the diff against the author's claim. Other specialist agents remain diff-only.

## Touches
- `src/pr_guardian/agents/architecture.py`
- `src/pr_guardian/agents/intent.py`
- `src/pr_guardian/agents/context_builder.py`
- `src/pr_guardian/agents/base.py`
- `prompts/architecture/base.md`
- `prompts/intent/base.md`
- `tests/test_agent_context_anchors.py`
- `tests/test_architecture_agent.py`

## Does Not Touch
- `src/pr_guardian/core/orchestrator.py`
- `src/pr_guardian/triage/classifier.py`
- `src/pr_guardian/dashboard/review_detail.html`
- `src/pr_guardian/decision/engine.py`

## Constraints
- `architecture` returns `AgentResult(pass, verdict_explanation="no architecture context found - agent skipped")` without an LLM call when discovery mode is skip.
- `intent` may flag medium/high scope opacity at the agent verdict level when no useful claim anchor exists; concrete findings still require diff-backed file evidence.
- Context builder must use explicit XML-ish blocks for anchors and must not inject those blocks for security, performance, code-quality, test, hotspot, or legacy agents.
- Keep `prompts/architecture_intent/base.md` and `ArchitectureIntentAgent` available for legacy re-review.
- Prompt wording must keep the hard diff-scope rule: findings cite files that appear in diff headers and are based on added/modified lines.
- `verdict_explanation` may be non-null on pass for intentional status notes.

## Test Expectations
- Add context-builder tests proving scoped anchor blocks are visible only to matching agents.
- Add architecture-agent tests proving skip mode does not call the LLM.
- Add parser/result tests proving pass explanations survive.

## Wrap-up
Include a note in the handover if prompt output schema changes beyond allowing pass explanations. Orchestration and scoring are deliberately deferred to brief 05.
