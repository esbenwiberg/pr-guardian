---
title: "Build GitHub App setup UI and additive merge gate enforcement"
depends_on:
  - 01-github-app-connection-data-model
  - 02-github-installation-token-adapter
touches:
  - src/pr_guardian/api/profiles.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/dashboard/profiles.html
  - tests/test_github_branch_protection.py
  - tests/test_profiles_api.py
  - tests/browser/profiles_management.spec.mjs
does_not_touch:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/core/github_chatops.py
---

# Brief 03 - Build GitHub App Setup UI and Additive Merge Gate Enforcement

## Task

Revamp the existing `/profiles` setup flow so operators can add GitHub App
Connections, link repositories, and have Guardian enforce the `guardian/review`
required check without destructive repo-setting changes.

## Requirements

- Replace the Connections tab's GitHub token form with a GitHub Apps setup flow:
  - name
  - app ID
  - private key
  - validate
  - installation discovery for the repo/account
  - permission and health summary
- Keep ADO Connection setup available and clearly separate.
- Add or rename the GitHub area to `GitHub Apps`.
- Add repo-link fields for:
  - GitHub App Connection
  - auto-review enabled
  - require `guardian/review` check enabled by default
- On linked GitHub repos with auto-review enabled, enforce merge blocking:
  - add `guardian/review` as a required check on the default target branch
  - preserve all existing branch protection/ruleset requirements
  - surface enforced, missing, warning, or unsupported state
  - fail clearly when Administration write permission is missing
- Provide `Validate` and `Fix gate` actions from the UI/API.
- The sandbox E2E reset behavior belongs to Brief 06, not this product UI.

## Required Facts

- `fact-required-check-added-additively`
- `fact-repo-link-enforces-gate-before-auto-review`
- `fact-github-app-setup-browser-flow`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not remove or rewrite existing required checks.
- Do not loosen reviews, signed commits, conversations, bypasses, or admin
  enforcement settings.
- Do not offer GitHub token import.
- Do not require live webhook setup for this UI.
