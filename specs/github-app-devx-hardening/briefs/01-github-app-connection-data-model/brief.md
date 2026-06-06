---
title: "Add GitHub App Connection data model"
depends_on: []
touches:
  - alembic/versions/<new>.py
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/persistence/crypto.py
  - src/pr_guardian/api/profiles.py
  - tests/test_github_app_connection_storage.py
  - tests/test_profiles_api.py
does_not_touch:
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/dashboard/profiles.html
---

# Brief 01 - Add GitHub App Connection Data Model

## Task

Replace GitHub PAT-shaped Connection storage with GitHub App Connection storage.
This brief owns the database, storage DTOs, API payload validation, redaction,
and audit behavior. Runtime token minting belongs to Brief 02.

## Requirements

- Add an append-only migration for GitHub App Connection fields:
  - `auth_kind` with value `github_app` for GitHub rows
  - `app_id`
  - `app_slug`
  - `installation_id`
  - `installation_account`
  - `installation_target_type`
  - encrypted private key storage
  - private key fingerprint
  - permissions snapshot
  - merge gate summary fields if useful for list rendering
- Remove GitHub PAT/token fields from GitHub create/update API payloads.
- Keep ADO Connection payloads working with their existing PAT shape.
- Store GitHub private keys encrypted using the existing Fernet helper.
- Never return raw private keys, encrypted private keys, JWTs, installation
  tokens, or token-like material from storage/API DTOs.
- Redact GitHub App secret changes in Profile audit diffs.
- Remove `/api/profiles/connections/env-imports` entries for `GITHUB_TOKEN`.
  ADO env imports can remain.
- Existing GitHub PAT rows do not need compatibility runtime behavior. Migration
  may mark old GitHub token rows archived/unhealthy or require operators to add
  new GitHub App Connections.

## Required Facts

- `fact-github-app-connection-redacts-private-key`
- `fact-github-token-import-removed`
- `fact-ado-connections-stay-pat-shaped`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not add a new secret-management system. Reuse `persistence/crypto.py`.
- Do not expose a token prefix for GitHub App installation tokens.
- Do not break ADO Connection creation, validation, or sync settings.
- Migrations are append-only. Do not edit migration 019.
