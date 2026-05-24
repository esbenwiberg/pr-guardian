# Brief 05 - Wire orchestration, triage, scoring, and legacy support

## Task
Connect the new anchor discovery and split agents to the review pipeline. Fresh reviews hydrate repo config and anchor sets before agent fan-out, triage selects `intent` and `architecture`, scoring uses their configured weights, and `architecture_intent` remains registered for historical re-review only.

## Touches
- `src/pr_guardian/core/orchestrator.py`
- `src/pr_guardian/triage/classifier.py`
- `src/pr_guardian/discovery/change_profile.py`
- `src/pr_guardian/decision/engine.py`
- `src/pr_guardian/decision/actions.py`
- `src/pr_guardian/persistence/storage.py`
- `tests/test_triage.py`
- `tests/test_change_profile.py`
- `tests/test_decision.py`
- `tests/test_dashboard_triage_enrichment.py`

## Does Not Touch
- `src/pr_guardian/dashboard/review_detail.html`
- `prompts/architecture/base.md`
- `prompts/intent/base.md`
- `alembic/versions/`

## Constraints
- `ALL_AGENTS` contains `intent` and `architecture`, not `architecture_intent`.
- Medium/high non-trivial reviews include `intent` when intent verification is enabled; trivial/docs-only reviews still skip agents.
- Architecture-boundary implied agent is `architecture`.
- `AGENT_REGISTRY` keeps `architecture_intent` for old re-review groups.
- Pipeline log records architecture skip notes, but the UI proof is handled in brief 06.
- Do not add a new DB status column; use existing persisted `verdict_explanation`.
- Prompt override registry and PR comment labels include `intent` and `architecture`.

## Test Expectations
- Update triage tests for high-risk agent selection and low/trivial behavior.
- Update change-profile tests for architecture-boundary implied agent naming.
- Update decision tests for split weights and legacy alias behavior.
- Add a re-review registry test for legacy `architecture_intent`.

## Wrap-up
Document fresh-vs-legacy agent selection clearly in the handover. Do not change the review-detail UI in this brief.
