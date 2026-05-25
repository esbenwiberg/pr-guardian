# Agent redesign

## Status

Proposed. This is not the live review-agent contract as of 2026-05-25. The
current production-shaped agent set still uses `architecture_intent`; ADR-006
documents the proposed future split into `intent` and `architecture`.

Current executable coverage for the live agent contract starts in
`tests/test_agents_contracts.py`. The planned tests named in this spec remain
future acceptance checks for the proposed split.

## Problem

Guardian's review agents currently blend two different jobs into
`architecture_intent`, and the shared prompt contract lets agents describe
findings without a first-class quote that proves the issue is grounded in an
added diff line. Architecture review also has no honest way to say "I had no
architecture context to review against", so a skipped architecture review can
look the same as a clean pass.

## Outcome

Guardian reviews are verifier-grounded: specialist findings are quote-backed,
`intent` and `architecture` are separate agents, and skipped architecture review
is explicit in the dashboard.

## Users

PR authors get clearer, less subjective findings. Human reviewers get a better
triage surface because PR-level scope opacity, architecture skips, and
quote-grounded code findings are visually distinct. Guardian maintainers get
separate contracts for intent verification and architecture verification.

## Success signal

A medium/high PR can run the split agent set and the dashboard shows
quote-grounded findings plus an explicit "Architecture skipped - no architecture
context found" state, while inline PR comments remain compact and quote-free.

## Non-goals

- No migration or backwards compatibility for the old `architecture_intent`
  config key, prompt override name, label, or weight.
- No GitHub issue or Azure DevOps work item fetching in v1 intent review.
- No scan-agent prompt rewrite for recent-change or maintenance scans.
- No caching for architecture anchor discovery.
- No new advisory-channel model beyond findings and explicit agent status.
- No quote strips in PR inline comments.

## Glossary

- **Verifier-grounded finding** - a finding that cites an exact source anchor
  before making a claim. For normal code findings, that anchor is an added diff
  line stored as `quote`.
- **Quote** - a first-class persisted field on `Finding`. For normal findings it
  must match a visible `+` line in the finding's diff file. For the special
  `intent` scope-opacity finding, it describes the missing or vague PR
  title/body anchor.
- **Intent anchor** - author-stated scope from the PR title/body or a referenced
  `specs/...` markdown file. A useful v1 anchor is either a fetchable spec file
  or at least 80 non-template characters with a concrete behavior/scope claim.
- **Scope opacity** - a medium/suspected PR-level `intent` finding for a
  medium/high PR whose title/body/spec references do not provide a useful
  intent anchor.
- **Architecture anchor** - repo-stated architecture ground truth such as
  `review.yml` `architecture_docs`, accepted ADRs, architecture/conventions
  files, architecture-relevant `AGENTS.md` sections, or structural hints.
- **Full verifier mode** - architecture mode that can flag deviations from
  written rules or high-confidence architecture docs.
- **Narrow local-pattern mode** - architecture mode that compares changed files
  to nearby siblings and relevant low-weight conventions only. It emits only
  low/suspected findings.
- **Skipped agent status** - `AgentResult.status == "skipped"` with a
  `status_reason`, used when an agent did not run because its required anchor
  was absent.
- **Clean break** - this app is not live yet, so old `architecture_intent`
  config and stored rows do not need migration or rendering compatibility.

## Reversibility

This feature changes model and storage shapes, but the app is not live. If the
change is wrong, rollback is a code revert plus resetting any local/dev database
created with the new fields. No production data migration or compatibility
bridge is required.
