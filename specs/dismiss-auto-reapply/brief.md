---
title: "Auto-apply comment dismissals live via a no-LLM re-decide"
touches:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/core/github_chatops.py
  - tests/test_dismiss_auto_reapply.py
does_not_touch:
  - src/pr_guardian/decision/
  - src/pr_guardian/persistence/storage.py
  - src/pr_guardian/platform/ado.py
require_sidecars: []
---

## Task

When a human replies to a Guardian inline finding comment with
`@guardian dismiss <status>: <reason>`, Guardian must **immediately** re-score
the PR and refresh the `guardian/review` status — without a manual
`@guardian re-review` and without running the LLM agents. Today the dismissal is
only recorded; the check stays red until someone re-reviews.

## Why

The dismissal-recording flow is fully built (GitHub) but useless as UX until the
verdict updates live. A human dismissal is an override — the LLM does not need to
re-confirm anything — so the re-apply should be a pure decision recompute, not a
full re-review.

## Research summary

Read `specs/dismiss-auto-reapply/research.md` and `plan.md` first (committed on
this branch).

- Dismiss handler: `src/pr_guardian/core/github_chatops.py:373-512`
  (`handle_github_review_comment_reply`). Records via `storage.upsert_dismissal`
  (line 475); acks "on the next `@guardian re-review`" (495-497); explicitly does
  NOT re-decide. It already builds a connection-scoped adapter
  (`_fresh_adapter_for_review`, line 438) and loads the originating `review`.
- Re-review path `run_re_review` (`orchestrator.py:939`) already contains the
  reusable core:
  - dismissed-signature filter — `1116-1149`
  - all-dismissed → `AUTO_APPROVE` short-circuit — `1164-1192`
  - agent re-evaluation (Step 3, the LLM step) — `1230-1263` **(skip this)**
  - shared decision: `combined_score` + replayed structural inputs
    (trust tier, sticky triggers, repo risk, risk tier, target branch) +
    `resolve_decision` — `1346-1368`
- `_post_results` (`orchestrator.py:1668-1764`) posts `guardian/review` status,
  re-asserts `guardian/readiness=success`, posts comments; `_save_result`
  (`1516`) persists. Status posts to `pr.head_commit_sha` (`github.py:326`).
- `GitHubAdapter.fetch_pr(repo, pr_id)` (`github.py:350`) returns a `PlatformPR`
  with the **live** `head_commit_sha` (`366`).
- Findings are stamped `f.primary_agent = ar.agent_name` before posting
  (`orchestrator.py:1856-1868`), so dismissal signatures always match.

## Plan

Reuse the re-review decision path minus agents:

1. Extract the decide-and-post tail of `run_re_review` (build surviving
   `AgentResult`s, the shared decision block `1346-1368`, the all-dismissed
   AUTO_APPROVE case `1164`, `_post_results`, `_save_result`) into a helper,
   e.g. `_finalize_from_findings(...)`. `run_re_review` calls it after Step 3 —
   its behavior must stay identical (verify `test_re_review_decision.py` still
   passes).
2. Add `re_decide_after_dismissal(pr, adapter, *, storage, config, base_url)`:
   loads the stored review, applies the dismissed-signature filter (same logic
   as `1116-1149`), keeps surviving findings **as-is** (no `re_evaluate`), calls
   the shared tail. No discovery, no agents, no LLM.
3. In `handle_github_review_comment_reply`, after the `upsert_dismissal` loop
   (`github_chatops.py:484`), when `dismissed > 0` and an adapter exists:
   `pr = await adapter.fetch_pr(repo, pr_id)` then call
   `re_decide_after_dismissal(...)`. If the adapter is None (build failed),
   record the dismissal as today and skip the re-decide.
4. Rewrite the ack (`github_chatops.py:494-497`) to report the recomputed
   verdict — e.g. "re-scored: now ✅ auto-approve" or
   "re-scored: still needs review — N finding(s) remain".

## Touches

- `src/pr_guardian/core/orchestrator.py` — extract `_finalize_from_findings`;
  add `re_decide_after_dismissal`.
- `src/pr_guardian/core/github_chatops.py` — call the re-decide after recording;
  fetch the live PR; new ack copy.
- `tests/test_dismiss_auto_reapply.py` — new test module (see Test expectations).

## Does not touch

- `src/pr_guardian/decision/` — the engine is already correct and IO-free.
- `src/pr_guardian/persistence/storage.py` — dismissal storage is already built.
- `src/pr_guardian/platform/ado.py` — ADO is out of scope (no comment ingestion).

## Constraints

- **Post status to the LIVE head** returned by `fetch_pr`, never the review's
  stored `head_commit_sha`. History: finalize once posted success to a stale
  stored SHA and the check never went green.
- **Reuse `_post_results` / `_post_result_status` unchanged** so the existing
  write-dedup + self-status guard applies. History: a status-webhook
  self-trigger loop hit GitHub's 1000-per-context cap (422). Do not add a fresh
  status write.
- **No LLM / no agent `re_evaluate` in the re-decide path.** A dismissal is a
  human override; re-running agents is out of scope and wasteful.
- **Do not diverge from the decision engine** — recompute the verdict only via
  the extracted shared tail, not a hand-rolled copy.
- Keep the dismiss idempotency intact (`claim_chatops_command(command="dismiss")`
  already de-dupes redelivered webhooks).
- Python 3.12, line length 99, Pydantic v2, asyncio. `ruff format` + `ruff check`
  + `mypy src` must pass. No loose typing; document any unavoidable
  `# type: ignore`.
- Respect layer invariants: `core/` must not import `api/`/`dashboard/`;
  `decision/` stays IO-free.

## Skills to reference

- `/verify` — drive the dismiss→re-decide path end-to-end before committing
  (the change has real runtime behavior beyond the unit tests).

## Test expectations

Create `tests/test_dismiss_auto_reapply.py` (async, `asyncio_mode = auto`,
in-memory DB per the testing patterns), covering:

- **flip-to-approve** — review with a single low finding; dismiss it; assert the
  re-decided/stored verdict is `Decision.AUTO_APPROVE`.
- **partial-dismiss-lowers-score** — review with two findings; dismiss one;
  assert verdict still requires review **and** the new `combined_score` is
  strictly lower than the pre-dismiss score (differentiation).
- **re-decide-skips-llm** — spy/fake agent + LLM provider; assert no
  `re_evaluate` / provider call happens during the re-decide.
- **status-targets-live-head** — fake adapter whose `fetch_pr` returns a SHA
  different from the stored review's `head_commit_sha`; assert the status write
  targets the live SHA.

Also confirm `tests/test_re_review_decision.py` and
`tests/test_github_dismiss_command.py` still pass (no behavior change to
`run_re_review` or dismissal recording).

## Risks / pitfalls

- Copy-pasting the decision block instead of extracting it → filter/score drift.
  Extract in checkpoint 1.
- Missing adapter (`_fresh_adapter_for_review` failed) → skip the re-decide but
  still record the dismissal.

## Wrap-up

1. Run any profile finish prompt if configured.
2. `python -m pytest`, `ruff check .`, `ruff format --check .`, `mypy src` — all
   green.
3. Commit (Conventional Commits: `feat(dismiss): …`) and push.
