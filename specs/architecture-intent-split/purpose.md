# Architecture Intent Split

## Problem
PR Guardian currently has one combined `architecture_intent` agent. That agent mixes two different jobs: checking whether a PR matches the author's stated claim, and checking whether the PR respects a repo's architecture. The combined prompt can produce subjective architecture feedback when no architecture source exists, while the intent side cannot see the full set of claim anchors it needs: PR body, linked work item, and referenced spec files.

The seed plans split those concerns into verifier-framed agents. `intent` verifies the diff against the author's stated claim. `architecture` verifies the diff against discovered repo architecture anchors, and skips when no useful anchor exists.

## Outcome
Fresh PR reviews run separate `intent` and `architecture` verifier agents. `architecture` discovers repo architecture anchors, runs only in an anchored mode, and surfaces `no architecture context found - agent skipped` in the review UI when no anchor exists. Historical `architecture_intent` findings remain re-reviewable.

## Users
- Reviewers reading PR Guardian's dashboard and PR comments.
- PR authors whose changes are being reviewed.
- Operators maintaining per-repo `review.yml`, prompt overrides, and review history.
- Pod workers implementing this feature across config, discovery, agents, orchestration, scoring, persistence, and UI.

## Success Signal
A fresh high-risk review selects `intent` and `architecture`, not `architecture_intent`. `intent` receives only intent anchors; `architecture` receives only architecture anchors or returns a pass status note without calling the LLM. The review detail page visibly renders the architecture skip note. This is validated by the split-agent triage/scoring unit facts and the browser fact `fact-visible-architecture-skip-browser` in brief 06.

## Non-goals
- Generalizing discovered anchors to security, performance, test, or hotspot agents.
- Adding an agentic repo-reading loop. Anchor reads stay pre-hydrated and scoped.
- Caching architecture discovery across reviews.
- Parsing PlantUML, Mermaid, or C4 diagrams beyond low-weight textual labels.
- Migrating existing `architecture_intent` prompt overrides into separate prompts.
- Adding a DB column or a new `skipped` verdict enum for architecture skip status.
- Rewriting the review detail page layout.
- Replacing current mechanical checks, validator behavior, or postback flow.

## Glossary
- **intent agent** - Verifier that compares the visible diff to the PR's stated claim.
- **architecture agent** - Verifier that compares the visible diff to discovered architecture anchors.
- **legacy architecture_intent agent** - Existing combined agent identity kept for historical re-review and old prompt overrides only.
- **intent anchor** - Claim text from PR title/body, commit messages, linked GitHub issue or ADO work item, or referenced spec files.
- **architecture anchor** - Repo source that states or implies architecture: `architecture_docs`, accepted ADRs, architecture docs, conventions, machine-enforced architecture tests, or structural hints.
- **rule anchor** - Imperative or machine-enforced source that can support a full verifier finding.
- **convention anchor** - Descriptive source that can support softer verifier findings.
- **structural hint** - Folder/config shape that can support only local-pattern checks.
- **full verifier mode** - Architecture can flag deviations from written rules.
- **narrow local-pattern mode** - Architecture can compare changed files to nearby patterns, but cannot make global architecture claims.
- **skip mode** - Architecture found no usable anchor and returns a visible pass note instead of findings.
- **visible skip status** - Existing `verdict_explanation` on a pass result rendered in review detail as a "Review note".

## Reversibility
This feature changes agent identity, config keys, prompt names, and finding signatures. Rollback is possible by selecting the legacy `architecture_intent` agent in triage again and leaving the legacy prompt/registry entry in place. Historical finding signatures are not rewritten. `weights.architecture_intent` remains a deprecated input alias for one release path, so existing `review.yml` files continue to parse while operators adopt `weights.architecture` and `weights.intent`.
