---
title: "Clean stale hotspot documentation"
touches:
  - docs/plan/01b-discovery.md
  - docs/plan/03-triage.md
  - docs/plan/04-ai-agents.md
  - docs/plan/08-implementation.md
  - docs/plan/09-operations.md
  - docs/plan/pr-guardian-design.md
  - src/pr_guardian/triage/hotspots.py
  - tests/test_hotspot_docs.py
does_not_touch:
  - src/pr_guardian/platform/
  - src/pr_guardian/dashboard/
  - specs/
---

## Task

Remove stale hotspot documentation that describes nightly/precomputed hotspot
refreshes, `.pr-guardian/hotspots.json`, and percentile-based hotspot rules.
Replace it with the on-demand model and default thresholds from this spec.

## Touches

- `docs/plan/01b-discovery.md` - replace precomputed DB/nightly hotspot lookup
  text with on-demand path-history evaluation.
- `docs/plan/03-triage.md` - replace nightly and 80th-percentile hotspot
  discussion with 90-day threshold semantics.
- `docs/plan/04-ai-agents.md` - update hotspot agent trigger wording away from
  percentile score.
- `docs/plan/08-implementation.md` - remove scheduled hotspot command examples.
- `docs/plan/09-operations.md` - remove hotspot refresh schedule guidance.
- `docs/plan/pr-guardian-design.md` - align system design sections with the
  on-demand evaluator and in-memory TTL cache.
- `src/pr_guardian/triage/hotspots.py` - update module/function docstrings left
  behind after Brief 02.
- `tests/test_hotspot_docs.py` - docs regression test.

## Does not touch

- `src/pr_guardian/platform/` - platform history implementation is Brief 02.
- `src/pr_guardian/dashboard/` - UI rendering is Brief 04.
- `specs/` - this brief updates durable docs, not the planning spec.

## Constraints

Docs must match the user decisions: no nightly job, no warning banner, no DB
hotspot cache, no `.pr-guardian/hotspots.json`, no 80th-percentile hotspot
definition. The docs should describe on-demand path-history evaluation, 90-day
window, default thresholds `8` and `0.3`, process-local 24 hour TTL cache,
reasoned per-path exemptions, and fail-closed lookup failures when
`respect_hotspots` is true.

## Test expectations

`tests/test_hotspot_docs.py` should scan the targeted hotspot sections/files for
banned stale phrases and for required on-demand wording. Keep the test targeted
so unrelated uses of words such as "nightly" for mutation testing do not fail.

## Wrap-up

Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run build and tests; both must still pass.
3. Commit and push.
