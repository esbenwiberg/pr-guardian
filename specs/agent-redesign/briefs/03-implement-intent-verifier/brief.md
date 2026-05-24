---
title: "Implement intent verifier"
touches:
  - src/pr_guardian/agents/intent.py
  - src/pr_guardian/agents/intent_anchors.py
  - prompts/intent/base.md
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/triage/classifier.py
  - src/pr_guardian/agents/context_builder.py
  - tests/test_intent_agent.py
  - tests/test_triage.py
does_not_touch:
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
  - src/pr_guardian/dashboard/
  - src/pr_guardian/persistence/
---

## Task

Create the `intent` verifier. It runs for medium/high risk PRs only. Its useful
anchor heuristic accepts a fetchable `specs/...` markdown reference or at least
80 non-template characters in title/body that make a concrete behavior/scope
claim. Missing anchors on medium/high PRs produce a medium/suspected PR-level
scope-opacity finding with `line: null`.

## Context

`PlatformPR.body` already exists and platform adapters already expose PR body
hydration. Platform adapters also expose file-content reads that can fetch
referenced spec files. Work item and issue APIs are intentionally out of scope
for v1.

## Constraints

- `intent` is scheduled for medium/high PRs only.
- Low PRs never run `intent` in v1.
- Work items/issues are not v1 anchors and must not be fetched.
- Referenced `specs/...` markdown files are read through existing platform file
  content methods.
- Missing-anchor behavior uses the configured file/line thresholds under
  `intent_verification` and the user-confirmed default:
  `changed_files >= 5` or `lines_changed >= 150`.
- The PR-level scope-opacity finding is visible in Guardian UI and excluded
  from inline comments by existing `line is None` behavior.

## Test expectations

- Cover the practical anchor heuristic.
- Cover medium/high scheduling and low-tier non-scheduling.
- Cover no-workitem behavior.
- Cover medium/suspected severity/certainty for scope opacity.

## Wrap-up

Handover the exact category string used for scope opacity and the helper shape
used by the architecture/dashboard briefs.
