---
title: "Add Profile, Connection, repo-link, and readiness data model"
depends_on: []
touches:
  - alembic/versions/
  - src/pr_guardian/persistence/models.py
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/persistence/crypto.py
  - tests/test_profiles_storage.py
  - tests/test_readiness_storage.py
does_not_touch:
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/dashboard/
---

# Brief 01 - Add Profile, Connection, Repo-Link, and Readiness Data Model

## Task

Create the durable persistence layer for Guardian-owned Profiles, named
Connections, exact repo links, Profile Managers, Profile audit history,
readiness candidates, candidate transition history, and Profile/Connection
provenance snapshots on reviews, scans, and synced PRs.

This brief is data and storage only. Do not route webhooks, render UI, or wire
execution paths yet.

## Requirements

- Add an append-only Alembic migration after the current latest migration.
- Add ORM models and storage helpers for:
  - Profiles
  - Connections
  - Repo links
  - Profile Managers
  - Profile audit events
  - Readiness candidates
  - Candidate transitions
- Add review/scan/synced-PR provenance columns:
  - `profile_id`
  - `profile_snapshot`
  - `connection_id`
  - `connection_snapshot`
  - `repo_link_id`
  - `candidate_id` where applicable
  - source fields needed to distinguish automatic, manual, manual bypass,
    override, API, repo review, and scan paths
- Migrate existing GitHub PAT rows into unified Connections.
- Remove the active `github_pats` table/API dependency from the model layer.
- Remove `ReviewRow.pat_name`; preserve historical readability through
  snapshots where possible.
- Add or reuse a centralized token encryption helper. Never expose full token
  values from ORM/storage return types.
- Seed or create the system default/noop Profile as non-deletable.
- Make archive/delete behavior reference-safe:
  - Profiles and Connections can be archived only when no active repo link
    depends on them, unless the repo link is moved, paused, or disabled first.
  - Candidate and transition history is retained indefinitely.

## Data Contract

Use the contracts in `specs/readiness-gated-profiles/design.md`:

- Profile
- Connection
- Repo Link
- Candidate State
- Review/scan/sync provenance fields

Candidate state values are exactly:

```text
waiting
blocked
reviewing
reviewed
superseded
error
```

Readiness reasons and snapshots must be stored separately from state.

## Required Facts

- `fact-profile-link-candidate-persistence`
- `fact-connection-archive-protection`
- `fact-existing-pats-migrate-to-connections`

See `contract.yaml` for executable scenarios.

## Constraints

- Migrations are append-only. Do not edit merged migrations.
- Store secrets encrypted or by secret reference; API/storage DTOs return only a
  token prefix and health metadata.
- Keep historical review and scan rows readable even after live Profiles or
  Connections are edited or archived.
- Do not put product defaults in migration code beyond what is needed for
  bootstrap rows.

## Wrap-Up

- Run the focused storage tests named in `contract.yaml`.
- Confirm the new migration is the only migration file added.
- Leave API routes and UI untouched for later briefs.
