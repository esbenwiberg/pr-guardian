# Handover: decent-ladybug (Brief 03 — Orchestrator Inline Comment Wiring)

## What was built

- **`build_inline_comment_body(findings)`** in `decision/actions.py` — formats co-located findings into a single inline comment body using the spec format: `**[SEVERITY] Category**\nDescription\n\n> Suggestion`. Multiple findings separated by `---`.
- **`SEVERITY_ORDER`** in `decision/actions.py` — lowercase-keyed (`{"low": 0, ...}`) to match `Severity.value` directly without string conversion at comparison time.
- **`_post_results()` updated** in `core/orchestrator.py`:
  - New kwargs: `comment_mode`, `review_id`, `storage`, `original_review_id`.
  - `"none"` → skips comment, still applies labels and platform actions.
  - `"summary"` → existing behaviour (unchanged).
  - `"inline"` → delegates to `_post_inline_and_summary()`.
- **`_post_inline_and_summary()`** — new private helper: filters findings by threshold, deletes stale inline comments (re-review path via `original_review_id`), posts inline via adapter, then posts summary last.
- **`_apply_platform_actions()`** — extracted from `_post_results` to eliminate duplication across the three mode branches.
- **`_MECH_SEVERITY_MAP`** — maps mechanical tool severity strings (`"error"`, `"warning"`, `"info"`, etc.) to `Severity` enum values for inline filtering.
- **`storage.create_review_record()`** — now accepts `comment_mode: str = "none"` kwarg and persists it on `ReviewRow`.
- **`run_review()`** — new `comment_mode: str = "summary"` parameter threaded through `_run_pipeline()`.
- **Re-review path** reads `comment_mode` and `review_id` from `original_review` dict; also saves the new review record with the inherited `comment_mode`.
- **4 new unit tests** in `tests/test_inline_comments.py` covering severity filtering, summary ordering, mode isolation, and delete-before-repost.

## Deviations from brief

- `SEVERITY_ORDER` keys are lowercase (matching `Severity.value`) rather than uppercase. The brief shows uppercase in the design doc, but lowercase avoids per-finding `.upper()` calls at runtime. Callers normalize threshold strings with `.lower()` instead of `.upper()`.
- `"none"` mode still applies labels and platform actions (approval/status/reviewers) — it only skips the PR comment. The brief says "no platform comment" so this interpretation preserves useful behaviour.
- `build_inline_comment_body` is added to `actions.py` as specified but is NOT called by `_post_results()` — the adapter uses its own `platform/_utils.inline_comment_body` for formatting. `build_inline_comment_body` is a public API for external callers (e.g. dashboard, API).

## Interfaces downstream pods must know about

- `SEVERITY_ORDER: dict[str, int]` in `pr_guardian.decision.actions` — lowercase keys. Use as `SEVERITY_ORDER.get(finding.severity.value, 0)` (no `.upper()` needed).
- `build_inline_comment_body(findings: list[Finding]) -> str` — public formatting helper in `decision.actions`.
- `run_review(..., comment_mode: str = "summary")` — new optional kwarg.
- `storage.create_review_record(pr, *, comment_mode: str = "none")` — new kwarg.
- `_post_results(adapter, pr, result, config, *, base_url, comment_mode, review_id, storage, original_review_id)` — private but stable; don't add more kwargs without restructuring.
- The re-review path reads `original_review["comment_mode"]` and `original_review["review_id"]` from the dict passed to `run_re_review()`. The API layer (brief 04) must include these fields when serialising a review for re-review.

## Files owned — do not modify without good reason

- `src/pr_guardian/core/orchestrator.py` — `_post_results`, `_post_inline_and_summary`, `_apply_platform_actions`, and the `comment_mode` threading. Changing signatures breaks all callers.
- `src/pr_guardian/decision/actions.py` — `SEVERITY_ORDER` key case (lowercase). If you change to uppercase, update all callsites in the orchestrator too.

## Discovered constraints / landmines

- **`SEVERITY_ORDER` is lowercase-keyed** — unlike the uppercase canonical notation in `design.md`. All comparison sites use `severity.value` directly (no `.upper()`). The threshold from config is normalized via `.lower()`.
- **`build_inline_comment_body` is not used by the adapter** — both `actions.py` and `platform/_utils.py` have separate formatters with slightly different Markdown. They serve different purposes (dashboard display vs. PR comment body). Don't merge them without coordinating with brief 02's adapter code.
- **`original_review["review_id"]` must be present for delete-before-repost to fire** — if the API omits it, old inline comments won't be cleaned up. The orchestrator logs a warning but silently proceeds.
- **Mechanical findings use tool-level severity** (from `MechanicalResult.severity`), not per-finding severity. If the mechanical runner starts emitting per-finding severity in the `findings` dicts, the orchestrator will need updating.
