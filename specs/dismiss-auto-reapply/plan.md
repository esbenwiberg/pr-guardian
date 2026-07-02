# Plan — dismiss-auto-reapply

## Overview

Make a comment-driven finding dismissal apply **immediately**. Today
`handle_github_review_comment_reply` records the dismissal and stops; the PR
check stays red until a human types `@guardian re-review`. Close the gap with a
**no-LLM re-decide** that reuses the existing re-review decision path minus the
agent re-evaluation step.

## Current understanding (evidence)

- Dismissal recording is complete for GitHub — `github_chatops.py:373-512`.
- The re-review path already filters dismissed signatures
  (`orchestrator.py:1116-1149`), short-circuits to AUTO_APPROVE when everything
  is dismissed (`1164`), and computes the verdict through the shared
  `resolve_decision` with replayed structural inputs (`1346-1368`).
- The *only* thing between that path and a no-LLM re-decide is Step 3, the agent
  `re_evaluate` calls (`1230-1263`).
- Results post through `_post_results` (`1668`), which targets
  `pr.head_commit_sha`; `fetch_pr` (`github.py:350`) yields the live head.

## Desired end state

Replying to a Guardian inline finding comment with
`@guardian dismiss false_positive: <reason>` records the dismissal **and**
re-scores the PR in place: the `guardian/review` status and stored review
reflect the new verdict with no `@guardian re-review` needed and no LLM spend.
The ack reports the new verdict.

## What we are not doing

- Native GitHub "Resolve conversation" detection (needs GraphQL — not used
  anywhere today).
- Top-level `@guardian dismiss <file>:<line>` comments (needs finding-reference
  parsing; the inline reply gives the mapping for free).
- ADO parity (no inbound comment ingestion; no `id_to_findings` persistence).
- Relaxing the low/medium non-security dismiss gate.
- Re-running agents / LLM re-evaluation on dismiss.

## Implementation approach

1. **Extract the shared decide-and-post tail of `run_re_review`** into a helper,
   e.g. `_finalize_from_findings(...)`, covering: build the surviving
   `AgentResult` list (findings kept **as-is**, no re-eval), the shared decision
   block (`combined_score` + replayed structural inputs + `resolve_decision`,
   currently `1346-1368`), the all-dismissed AUTO_APPROVE case (`1164`),
   `_post_results`, and `_save_result`. `run_re_review` calls this helper after
   Step 3 so behavior is unchanged. This satisfies the existing "re-review and
   full review can never diverge" intent and prevents a second copy of the
   filter/score logic.
2. **Add a re-decide entry point** `re_decide_after_dismissal(pr, adapter, ...)`
   that runs Step 2's dismissed-signature filter over the stored review, keeps
   surviving findings unchanged, and calls the shared tail. No discovery, no
   agents, no LLM.
3. **Wire it into the dismiss handler.** After the `upsert_dismissal` loop in
   `handle_github_review_comment_reply` (`github_chatops.py:484`), when at least
   one dismissal was recorded: `pr = await adapter.fetch_pr(repo, pr_id)` (live
   head), then invoke the re-decide with the existing connection-scoped adapter
   and the stored review.
4. **Rewrite the ack** to report the recomputed verdict (e.g. "re-scored: now ✅
   auto-approve" / "still needs review: N finding(s) remain") instead of "on the
   next `@guardian re-review`."
5. **Guard the status re-post.** Reuse `_post_results` / `_post_result_status`
   unchanged so the existing write-dedup + self-status guard applies, and post
   to the **live** head fetched in step 3 — never the stored head SHA.

## Checkpoints

1. Extract `_finalize_from_findings` from `run_re_review`; re-review still green.
2. Add `re_decide_after_dismissal` (filter → survivors as-is → shared tail).
3. Wire into `handle_github_review_comment_reply`; fetch live PR; new ack copy.
4. Tests + validation.

## Test strategy (discriminating facts)

- **flip-to-approve:** a review with one low finding; dismiss it →
  `Decision.AUTO_APPROVE`. Broken impl (no re-decide) leaves it REVIEW.
- **partial-dismiss-lowers-score:** a review with two findings; dismiss one →
  verdict still REVIEW **and** `combined_score` strictly **lower** than the
  pre-dismiss score. This is the differentiation fact — a broken impl that
  ignores the dismissal produces the *same* score. Shape-only "still REVIEW"
  is insufficient on its own.
- **re-decide-skips-llm:** the re-decide path invokes **no** agent
  `re_evaluate` / LLM provider call. Broken impl (auto-kick full re-review)
  would invoke agents. Assert with a spy/fake that records agent invocation.
- **status-targets-live-head:** status is posted to the head SHA returned by
  `adapter.fetch_pr` (live), not the review's stored `head_commit_sha`. Use a
  fake adapter whose `fetch_pr` returns a *different* SHA than the stored review
  and assert the status write targets the live one. Broken impl (stale-SHA bug)
  posts to the stored SHA.

No browser fact: the observable outcomes are a GitHub commit status and an ack
comment, not a dashboard view; the dashboard reflection is covered transitively
by `_save_result` in the unit facts. Live ack wording is a `human_review` item.

## Risks / pitfalls

- **Status-webhook loop / stale SHA** (see research "known blast-radius"). Fully
  mitigated by reusing `_post_results` and posting to the live head.
- **Filter/score drift** if the tail is copy-pasted instead of extracted —
  avoided by checkpoint 1.
- **Idempotency:** the dismiss command is already de-duped via
  `claim_chatops_command(command="dismiss")` (`github_chatops.py:405`), so a
  redelivered webhook won't double-fire the re-decide.
- **No surviving-adapter case:** if `_fresh_adapter_for_review` fails
  (`github_chatops.py:439`) the ack is skipped today; the re-decide must be
  skipped too (no adapter → cannot fetch PR or post status). Record the
  dismissal regardless, as today.
