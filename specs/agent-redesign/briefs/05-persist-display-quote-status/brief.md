---
title: "Persist and display quote and skipped status"
touches:
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/api/dashboard.py
  - src/pr_guardian/dashboard/review_detail.html
  - src/pr_guardian/dashboard/human_review.html
  - src/pr_guardian/dashboard/human_wizard.html
  - pyproject.toml
  - tests/test_storage_agent_contracts.py
  - tests/test_dashboard_quote_status.py
  - tests/browser/test_review_detail_quote_status.py
does_not_touch:
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
  - alembic/
---

## Task

Persist and display the new contracts. Add storage fields for finding `quote`,
agent `status`, and agent `status_reason`. Return them from dashboard APIs. In
review detail and human-review surfaces, render a compact monospaced diff quote
strip below location and render skipped architecture as "Architecture skipped -
no architecture context found".

## Context

`FindingRow` currently stores severity, certainty, category, file, line,
description, suggestion, and CWE. `AgentResultRow` currently stores verdict and
verdict explanation only. The app is not live, so this brief may update the
models directly without alembic migration or old-row compatibility.

## Constraints

- Do not add a migration.
- Quote stays out of PR inline comments.
- PR-level intent scope-opacity findings with `line: null` still display in
  Guardian UI.
- Existing dismiss/re-review controls keep working.
- Browser-test proof is required because this brief changes visible UI.

## Test expectations

- Add storage/API roundtrip coverage for quote/status fields.
- Add dashboard API coverage showing triage enrichment still works when quote
  and status are present.
- Add a durable browser test that opens the review detail page and verifies the
  quote strip plus skipped architecture state are visible.

## Wrap-up

Include browser-test evidence paths in the handover if the test saves
screenshots, traces, or logs.
