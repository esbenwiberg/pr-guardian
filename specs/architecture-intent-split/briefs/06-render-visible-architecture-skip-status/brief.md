# Brief 06 - Render visible architecture skip status

## Task
Update the review detail surface so an architecture skip is visible to reviewers. A pass verdict with a non-empty `verdict_explanation` renders as a compact "Review note" under the agent header, including the exact text `no architecture context found - agent skipped`. Add labels for `architecture` and `intent`.

## Touches
- `src/pr_guardian/dashboard/review_detail.html`
- `src/pr_guardian/persistence/storage.py`
- `tests/test_dashboard_triage_enrichment.py`
- `tests/browser/review_detail_skip_status.spec.mjs`
- `README.md`

## Does Not Touch
- `src/pr_guardian/agents/architecture.py`
- `src/pr_guardian/core/orchestrator.py`
- `alembic/versions/`
- `src/pr_guardian/decision/engine.py`

## Constraints
- No new UI page and no layout rewrite. This is a small note in the existing agent card.
- Show pass explanations as "Review note"; warn/flag explanations can keep "Review focus".
- Browser proof must run with mocked API data and no network dependency beyond the local test server created by the script.
- Keep the exact skip text stable for tests and user recognition.
- Keep the zero-findings/pass state obvious in the agent card.

## Test Expectations
- Add a Playwright-based browser script using the repo's `playwright` dependency.
- Mock `/api/dashboard/reviews/{id}` response or serve a tiny local fixture so the test is deterministic.
- Add a dashboard serialization test showing pass explanations survive into the payload.

## Wrap-up
Include the browser screenshot or trace path in the evidence if the test script creates one. This is the feature's user-visible proof.
