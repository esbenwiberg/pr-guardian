# Specs

This folder contains feature contracts for work that is planned, in progress,
or already implemented. Each active spec should answer four questions:

1. Description: who the feature is for and what behavior changes.
2. Design: data shape, API surface, important flows, and ownership boundaries.
3. Acceptance: concrete Given/When/Then criteria.
4. Test traceability: the test file or command that proves each criterion.

Status convention:

- `accepted`: implemented or actively being implemented; acceptance criteria
  should map to executable tests.
- `proposed`: design candidate; tests may be named as planned work but are not
  expected to exist yet.
- `archived`: historical context only; do not use as an implementation source
  without re-validating against current code.

Prefer `contract.yaml` for machine-readable acceptance criteria. When a feature
only has markdown briefs, include a `## Acceptance Criteria` section and a
`## Test Traceability` section in the brief.
