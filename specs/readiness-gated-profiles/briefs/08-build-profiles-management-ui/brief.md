---
title: "Build /profiles management UI"
depends_on:
  - 02-add-profile-management-api
touches:
  - src/pr_guardian/api/dashboard_page.py
  - src/pr_guardian/dashboard/profiles.html
  - src/pr_guardian/dashboard/static/sidebar.js
  - src/pr_guardian/dashboard/static/command-palette.js
  - tests/browser/profiles_management.spec.mjs
  - tests/test_profile_sidebar_auth.py
does_not_touch:
  - src/pr_guardian/api/settings.py
  - src/pr_guardian/core/orchestrator.py
---

# Brief 08 - Build /profiles Management UI

## Task

Build the top-level `/profiles` page from the approved wireframe. The page is
available to admins and Profile Managers, and it manages Profiles, repo links,
Connections, audit history, and Profile Managers with structured controls.

Do not put this under `/settings`.

## Approved Wireframe

```text
/profiles

[ Profiles                                      ]  [New Profile] [Link Repository]

[ Profiles ] [ Repositories ] [ Connections ] [ Audit ] [ Managers* ]

left list            main editor/list              inspector
Default / noop       Profile: Default / noop       Usage
Standard Service     Name, description             0 linked repos
High Risk Service    Review policy                 Side effects
                     Readiness                     Recent audit

Repositories tab:
platform | org/project | repo | profile | connection | auto-review | paused | last readiness

Connections tab:
name | platform | org/url | token prefix | sync_enabled | linked repos | updated | actions

Audit tab:
time | actor | action | target | before/after

Managers tab, admin-only:
email | added by | date | remove
```

## Requirements

- Add `/profiles` dashboard route.
- Add sidebar/nav visibility:
  - admins see Settings and Profiles
  - Profile Managers see Profiles but not Settings
  - ordinary signed-in users see neither
- Add command-palette entry if the app has an active command palette.
- Build tabs:
  - Profiles
  - Repositories
  - Connections
  - Audit
  - Managers, admin-only
- Profile editor:
  - structured controls only
  - no YAML/JSON editor
  - show default/noop as system-owned and non-deletable
  - allow editing default/noop settings
  - show side-effect switches and readiness settings clearly
- Repositories tab:
  - exact repo identity
  - Profile selector
  - Connection selector
  - auto-review toggle
  - paused toggle
  - last readiness summary
- Connections tab:
  - create/edit/archive
  - validate/health state
  - token prefix only, no raw token display after save
  - sync_enabled toggle gated by health
  - explicit import-from-env affordance when env secrets are present
- Audit tab:
  - recent field-level diffs
  - actor/time/action/target
  - secret redaction
- Managers tab:
  - admins can add/remove Profile Managers by UPN/email
  - Profile Managers cannot manage the manager list

## Required Facts

- `fact-profile-manager-creates-connection-profile-link`
- `fact-profile-nav-permissions`
- `fact-profile-ui-redacts-secrets-and-uses-structured-controls`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not expose raw tokens in DOM, logs, or browser fixtures.
- Do not add Profile management into `/settings`.
- Do not use YAML/JSON text editing for Profile policy in v1.
- Do not allow Profile Managers to manage API keys, LLM settings, admins, or
  `/settings`.

## Wrap-Up

- Add seeded demo data for Profiles, Connections, and repo links if the browser
  tests need stable fixtures.
- Capture browser-test screenshots under the test's evidence directory.
