---
title: "Replace review.yml config loading with Profile resolution"
depends_on:
  - 01-add-profile-readiness-data
touches:
  - src/pr_guardian/config/schema.py
  - src/pr_guardian/config/loader.py
  - src/pr_guardian/config/defaults.yml
  - src/pr_guardian/cli.py
  - docs/cli.md
  - tests/test_profile_config_resolver.py
  - tests/test_cli.py
does_not_touch:
  - src/pr_guardian/llm/
  - src/pr_guardian/api/webhooks.py
  - src/pr_guardian/dashboard/
---

# Brief 03 - Replace review.yml Config Loading with Profile Resolution

## Task

Make Guardian-owned Profiles the source of review and scan policy. Remove
runtime `review.yml` support from product paths and add a resolver that returns
GuardianConfig-compatible policy for linked and unlinked execution paths.

## Requirements

- Add a Profile resolver that can resolve:
  - linked repo Profile and Connection
  - unlinked default/noop Profile
  - start-time snapshots for reviews and scans
- Keep `llm.*` and executor runtime knobs outside Profiles.
- Prune dormant old config fields from Profile payloads:
  - `intent_verification`
  - `privacy`
  - `feedback`
  - `test_quality`
  - `triage.agent_context_thresholds`
  - `thresholds.human_review_min_score`
- Include active product fields named in `design.md` under "Profile".
- Preserve global/admin-only LLM settings via the existing global settings path.
- Remove runtime use of `load_repo_config(repo_path)` from product review and
  scan execution paths.
- Remove or replace CLI `validate --config` behavior so the CLI no longer
  teaches repo-file config.
- Keep local dry-run behavior, but make it use default/noop Profile.
- Update docs that instruct users to create or edit `review.yml`.

## Resolver Semantics

```text
linked repo review/scan/repo review:
  repo link -> active Profile + active Connection

unlinked signed-in manual review:
  default/noop Profile + selected/inferred Connection

unlinked scan:
  default/noop Profile + selected/inferred Connection

local dry-run:
  default/noop Profile + local/default platform-less context
```

Profile edits apply to future evaluations. In-flight reviews and scans keep
their start snapshot.

## Required Facts

- `fact-linked-and-default-profile-resolution`
- `fact-review-yml-runtime-path-removed`
- `fact-dormant-fields-pruned`

See `contract.yaml` for executable scenarios.

## Constraints

- Do not move LLM settings into Profiles.
- Do not keep hidden compatibility behavior that silently reads repo
  `review.yml`.
- Avoid changing agent prompt or decision semantics beyond reading Profile
  policy from the new source.

## Wrap-Up

- Update CLI/help docs affected by repo-file config removal.
- Keep old planning docs as historical context; product docs should point to
  Profiles.
