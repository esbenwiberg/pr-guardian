---
title: "Harden prompts and quote validation"
touches:
  - src/pr_guardian/agents/base.py
  - src/pr_guardian/agents/context_builder.py
  - prompts/security_privacy/base.md
  - prompts/performance/base.md
  - prompts/code_quality_observability/base.md
  - prompts/test_quality/base.md
  - prompts/hotspot/base.md
  - prompts/validator/base.md
  - tests/test_agent_quote_validation.py
  - tests/test_inline_comments.py
does_not_touch:
  - src/pr_guardian/persistence/
  - src/pr_guardian/dashboard/
  - src/pr_guardian/platform/
---

## Task

Update the shared review-agent schema so findings include `quote`. Normal
findings must cite an exact visible added diff line in their file. The parser
must drop findings that miss this contract before they reach decision, storage,
or UI. Build agent context with clearer metadata/diff blocks and include PR
body metadata for intent verification.

## Context

The current `AGENT_OUTPUT_SCHEMA` already tells agents to use added lines only,
but it does not require a machine-checkable quote. Inline comment rendering
already ignores `line=None`; that behavior is preserved and used by the intent
scope-opacity exception.

## Constraints

- Context lines remain read-only.
- Normal findings require `file`, `line`, and `quote`.
- Quote must match a visible added line in that file's diff.
- The only PR-level exception is the `intent` scope-opacity category, using
  `line: null` and a quote describing the missing or vague PR anchor.
- Quote stays out of PR inline comments.
- Preserve invalid/truncated JSON repair behavior.

## Test expectations

- Add tests for quote-required, quote-mismatch, non-added-line rejection, and
  the intent PR-level exception.
- Add/adjust inline comment tests to prove quote is not included in PR inline
  comment bodies.

## Wrap-up

Record any parser helper names and the exact scope-opacity category string for
briefs 03 and 05.
