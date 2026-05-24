# Brief 02 - Discover architecture anchors

## Task
Build the runtime architecture anchor discovery service from `plans/architecture-anchor-discovery.md`. It reads through a repo-content abstraction backed by `PlatformAdapter.list_repo_files()` and `fetch_file_content()`, classifies candidate files into rule, convention, or structural-hint anchors, scopes anchors to changed paths, and returns one mode per changed path group: full verifier, narrow local-pattern, or skip.

## Touches
- `src/pr_guardian/discovery/architecture_anchors.py`
- `src/pr_guardian/discovery/repo_content.py`
- `src/pr_guardian/models/anchors.py`
- `tests/test_architecture_anchor_discovery.py`
- `plans/architecture-anchor-discovery.md`
- `README.md`

## Does Not Touch
- `src/pr_guardian/core/orchestrator.py`
- `src/pr_guardian/agents/architecture.py`
- `src/pr_guardian/dashboard/review_detail.html`
- `src/pr_guardian/decision/engine.py`

## Constraints
- Explicit `architecture_docs` from config always wins.
- Filter `AGENTS.md` and `CLAUDE.md` by architecture-relevant section, not whole file.
- Demote a doc when more than half of referenced folder names do not exist.
- Structural hints alone can only reach narrow local-pattern mode.
- Sibling fallback requires at least three agreeing siblings.
- Keep discovery bounded: cap fetched docs and snippet size, and record truncation in the anchor set.
- Treat missing configured docs as warnings, not failed reviews.

## Test Expectations
- Add deterministic unit tests with a fake repo-content provider.
- Cover explicit docs, ADRs, structural-only repos, empty repos, path scopes, stale docs, and build-only `AGENTS.md`.
- Do not call live platform APIs in tests.

## Wrap-up
Update `plans/architecture-anchor-discovery.md` only if the implementation deliberately adjusts the seed algorithm. Leave orchestration to brief 05.
