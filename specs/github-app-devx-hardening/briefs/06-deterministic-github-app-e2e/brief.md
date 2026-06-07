---
title: "Add deterministic GitHub App sandbox E2E harness"
depends_on:
  - 01-github-app-connection-data-model
  - 02-github-installation-token-adapter
  - 03-github-app-setup-and-merge-gates
  - 04-review-postback-guidance-and-approvals
  - 05-chatops-mention-reactions-and-rereview
touches:
  - src/pr_guardian/llm/fake.py
  - src/pr_guardian/llm/factory.py
  - scripts/github-app-e2e.sh
  - tests/test_fake_llm_provider.py
  - docs/github-app-e2e.md
does_not_touch:
  - src/pr_guardian/platform/ado.py
---

# Brief 06 - Add Deterministic GitHub App Sandbox E2E Harness

## Task

Give coding agents a single opt-in command that validates the full GitHub App
flow against `esbenwiberg/pr-guardian-e2e` without depending on real LLM output
or live webhook delivery.

## Requirements

- Add deterministic fake LLM provider:
  - configured explicitly for E2E/dev
  - emits stable pass/finding JSON based on fixture markers in the PR diff
  - works for first review and re-review
  - never used unless configured
- Add `scripts/github-app-e2e.sh`:
  - `--check` mode validates only local prerequisites, environment shape, and
    actionable setup messages; it must not call GitHub or require network
  - full run checks `gh auth` or `GH_TOKEN` can access
    `esbenwiberg/pr-guardian-e2e`
  - requires `GUARDIAN_E2E_GITHUB_APP_ID`
  - requires `GUARDIAN_E2E_GITHUB_PRIVATE_KEY` or key file
  - discovers installation ID from the sandbox repo
  - generates a local webhook secret for signed replay
  - starts Guardian locally on an available port
  - configures the sandbox repo and branch protection with full reset authority
  - creates a test branch and PR
  - links repo/Profile/GitHub App Connection through Guardian APIs
  - replays signed GitHub webhooks
  - comments `@guardian` as the sandbox actor
  - waits for statuses/comments/reactions/review output
  - cleans up branch/PR unless `GUARDIAN_E2E_KEEP=1`
- Live webhook mode is optional when `GUARDIAN_PUBLIC_URL` is set.
- Default `python -m pytest` remains hermetic and does not call GitHub.

## Required Facts

- `fact-fake-llm-provider-deterministic`
- `fact-github-app-e2e-script-check-is-local`

See `contract.yaml` for executable scenarios.

## Manual Validation

The full live sandbox run is intentionally not a pod-required fact because it
needs network access, `gh` credentials, GitHub App credentials, and permission to
mutate `esbenwiberg/pr-guardian-e2e`.

After the locked-down series lands, an operator or privileged agent should run:

```bash
bash scripts/github-app-e2e.sh
```

That manual run should validate branch protection reset, PR creation, signed
webhook replay, statuses, inline comments, sticky guidance, `eyes` reaction,
re-review, and configured formal approval.

## Constraints

- Do not piggyback on Codex, Claude desktop, or OAuth session internals for
  Guardian's LLM calls.
- Do not require real Anthropic/OpenAI keys for default E2E.
- Do not run the sandbox E2E in the default test suite.
- Do not make live GitHub access a pod-required fact.
- Do not use `gh` as Guardian's runtime GitHub API identity.
