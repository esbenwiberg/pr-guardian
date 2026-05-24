# Brief 03 - Hydrate intent anchors

## Task
Create intent anchor hydration for the new `intent` agent. The service gathers PR title, PR body, best-effort commit messages, linked GitHub issue or ADO work item, and referenced spec files under `specs/`, `plans/`, or similar allowlisted doc paths. It returns a bounded `IntentAnchorSet` that distinguishes strong anchors from missing or vague anchors.

## Touches
- `src/pr_guardian/discovery/intent_anchors.py`
- `src/pr_guardian/triage/work_item.py`
- `src/pr_guardian/platform/protocol.py`
- `src/pr_guardian/platform/github.py`
- `src/pr_guardian/platform/ado.py`
- `src/pr_guardian/models/anchors.py`
- `tests/test_intent_anchor_discovery.py`
- `tests/test_github_adapter.py`
- `tests/test_ado_adapter.py`

## Does Not Touch
- `src/pr_guardian/agents/intent.py`
- `src/pr_guardian/agents/context_builder.py`
- `src/pr_guardian/decision/engine.py`
- `src/pr_guardian/dashboard/review_detail.html`

## Constraints
- Work-item fetch is best-effort. Network/API failures produce an anchor warning, not a failed review.
- Referenced spec files are fetched only when the path is mentioned in PR text and matches an allowlisted docs/specs/plans pattern.
- Cap fetched anchor text by file and total budget.
- Medium/high no-anchor scope opacity is represented as agent-level behavior; do not invent a finding against a fake file.
- Keep platform adapter method signatures stable and optional enough for fake adapters in tests.

## Test Expectations
- Add fake-adapter unit tests for title/body/work-item/spec file hydration.
- Add GitHub and ADO adapter tests for linked work-item fetch success and graceful fallback.
- Add a missing-anchor policy test for medium/high PRs.

## Wrap-up
Document any platform API endpoints used in the handover. Do not wire the new anchors into agent context here; brief 04 owns that.
