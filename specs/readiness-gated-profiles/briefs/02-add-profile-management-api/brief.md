---
title: "Add Profile and Connection management APIs"
depends_on:
  - 01-add-profile-readiness-data
touches:
  - src/pr_guardian/api/profiles.py
  - src/pr_guardian/auth/identity.py
  - src/pr_guardian/auth/dependencies.py
  - src/pr_guardian/api/health_api.py
  - src/pr_guardian/main.py
  - tests/test_profiles_api.py
  - tests/test_connection_health.py
does_not_touch:
  - src/pr_guardian/dashboard/
  - src/pr_guardian/core/orchestrator.py
---

# Brief 02 - Add Profile and Connection Management APIs

## Task

Expose signed-in API routes for structured CRUD over Profiles, Connections,
repo links, Profile Managers, and audit history.

Admins and Profile Managers can manage Profiles, Connections, and repo links.
Only admins can manage the Profile Manager list, admin list, API keys, LLM
settings, and `/settings`.

## Requirements

- Add `Identity.can_manage_profiles`.
- Update `/api/me` to expose `can_manage_profiles`.
- Add Profile Manager storage/API around UPN/email.
- Add API routes under a new router such as `src/pr_guardian/api/profiles.py`:
  - Profiles CRUD
  - Connections CRUD/archive/validate
  - repo links CRUD/pause/enable auto-review
  - Profile Manager list/add/remove
  - Profile and connection audit history
- Add field-level audit events for Profile, Connection, and repo-link changes.
- Audit diffs must redact secrets and never store raw token values.
- Save Connections first, validate immediately, and persist health.
- Require healthy Connections for:
  - repo link creation/update
  - enabling `sync_enabled`
- Allow sync-disabled Connections for repo links, manual reviews, and scans.
- Show explicit env import availability for `GITHUB_TOKEN`, `ADO_PAT`, and
  `ADO_ORG_URL`; do not silently persist env secrets.
- Keep Profile payloads structured. Do not accept arbitrary YAML/JSON blobs as
  the v1 management API.

## Permission Matrix

```text
Action                                      Admin  Profile Manager  Signed-in  API key
manage Profiles                            yes    yes              no         no
manage Connections                         yes    yes              no         no
manage repo links                          yes    yes              no         no
enable automated platform approval         yes    yes              no         no
manage Profile Managers                    yes    no               no         no
manage /settings, API keys, LLM settings   yes    no               no         no
manual Start Review Now                    yes    yes              yes        linked repos only
readiness override                         yes    yes              no         no
human finalization                         yes    yes              yes        no
```

## Required Facts

- `fact-profile-manager-can-link-repo`
- `fact-audit-field-diffs-redact-secrets`
- `fact-unhealthy-connection-blocks-sync-and-link`

See `contract.yaml` for executable scenarios.

## Constraints

- API responses must not expose raw token values.
- Do not implement the web UI in this brief.
- Do not let API keys administer Profiles, Connections, repo links, or
  readiness overrides.
- Connection validation failures should leave the Connection saved with
  unhealthy status and an actionable health message.

## Wrap-Up

- Register the new router in `main.py`.
- Update auth/identity tests so `can_manage_profiles` is visible through
  `/api/me`.
- Keep UI routes stubbed for brief 08.
