---
title: "Clean docs and remove GitHub PAT runtime references"
depends_on:
  - 01-github-app-connection-data-model
  - 02-github-installation-token-adapter
  - 03-github-app-setup-and-merge-gates
  - 04-review-postback-guidance-and-approvals
  - 05-chatops-mention-reactions-and-rereview
  - 06-deterministic-github-app-e2e
touches:
  - README.md
  - CLAUDE.md
  - docs/api.md
  - docs/cli.md
  - docs/github-app-setup.md
  - docs/github-app-e2e.md
  - tests/test_no_github_pat_runtime.py
does_not_touch:
  - src/pr_guardian/platform/ado.py
---

# Brief 07 - Clean Docs and Remove GitHub PAT Runtime References

## Task

Make the repo's docs, quickstart, and static checks match the new GitHub App-only
runtime model.

## Requirements

- Update setup docs:
  - required GitHub App permissions
  - App ID/private key setup
  - installing the App on repos/orgs
  - linking a repo in `/profiles`
  - merge gate enforcement and required `guardian/review`
  - `@guardian` ChatOps
  - sticky guidance comment behavior
  - deterministic E2E harness
- Update `CLAUDE.md` runtime notes:
  - remove `GITHUB_TOKEN` as Guardian runtime setup
  - document GitHub App credentials
  - document `scripts/github-app-e2e.sh` as opt-in and sandbox-only
- Update API/CLI docs to remove GitHub PAT language for product paths.
- Keep ADO PAT docs intact.
- Add a focused test/static check that prevents GitHub runtime fallback from
  returning.

## Required Facts

- `fact-docs-describe-github-app-only-runtime`
- `fact-no-github-token-runtime-fallback`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not rewrite historical `docs/plan/` documents unless they are actively
  linked from current setup docs.
- Do not remove ADO PAT documentation.
- Do not claim live webhooks are required for local E2E.
