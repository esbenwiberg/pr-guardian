---
title: "Wire GitHub adapter to installation tokens"
depends_on:
  - 01-github-app-connection-data-model
touches:
  - pyproject.toml
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/factory.py
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/core/readiness.py
  - src/pr_guardian/core/pr_sync.py
  - tests/test_github_app_auth.py
  - tests/test_github_adapter.py
does_not_touch:
  - src/pr_guardian/dashboard/profiles.html
  - src/pr_guardian/api/profiles.py
---

# Brief 02 - Wire GitHub Adapter to Installation Tokens

## Task

Make all GitHub runtime API calls use GitHub App installation tokens resolved
from stored Connections. Remove `GITHUB_TOKEN` and PAT fallback from GitHub
adapter creation paths.

## Requirements

- Add a GitHub App auth helper that:
  - creates RS256 app JWTs with `iat`, `exp`, and `iss`
  - exchanges app JWTs for installation access tokens
  - caches installation tokens in memory by installation ID
  - refreshes before expiry
  - never assumes installation token format or length
- Add any required dependency for JWT signing if `cryptography` alone is not
  sufficient.
- Update `GitHubAdapter` to accept an installation-token provider or resolved
  installation credentials instead of a static PAT string.
- Use the authorization scheme required for GitHub App installation tokens.
- Update adapter/factory resolution so GitHub callers must provide or resolve a
  GitHub App Connection.
- Remove runtime fallback to `os.environ["GITHUB_TOKEN"]`.
- Update readiness, manual review, re-review, scan, and broad sync GitHub paths
  to resolve App Connections.
- Keep ADO adapter creation unchanged.

## Required Facts

- `fact-github-app-jwt-and-token-cache`
- `fact-github-adapter-uses-installation-bearer`
- `fact-github-runtime-has-no-github-token-fallback`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not store installation tokens in the database.
- Do not make token refresh global process state that tests cannot isolate.
- Do not change ADO auth.
- Do not fetch installation tokens for non-GitHub platforms.
