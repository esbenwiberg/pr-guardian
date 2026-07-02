# Research — dismiss-auto-reapply

Factual map of the current state. No recommendations here (see `plan.md`).

## What exists today

Guardian already ingests a human dismissal of a finding via a PR comment reply,
records it durably, and excludes it from scoring — but **only on the next manual
`@guardian re-review`**. There is no live re-apply.

## Comment → dismissal recording (built, GitHub only)

- `src/pr_guardian/api/webhooks.py:142` — `pull_request_review_comment` webhook,
  only for replies (`in_reply_to_id` present), routes to
  `handle_github_review_comment_reply`.
- `src/pr_guardian/core/github_chatops.py:373-512` —
  `handle_github_review_comment_reply`:
  - parses `@guardian dismiss <status>: <reason>` (`_DISMISS_RE`, line 34),
  - maps the parent comment → finding(s) via
    `storage.find_inline_comment_by_platform_id` (line 399),
  - authorization gate `_is_authorized` (OWNER/MEMBER/COLLABORATOR or PR author),
  - eligibility gate `_is_comment_dismissable` — **low/medium severity,
    non-`security_privacy` only** (lines 43-44, 62-69),
  - `storage.upsert_dismissal(...)` per eligible finding (line 475),
  - acks with "excluded on the next `@guardian re-review`" (lines 495-497).
  - **Does not re-decide** — docstring says so explicitly (lines 388-391).
- Statuses: `false_positive`, `by_design`, `acknowledged`, `will_fix`
  (`_DISMISS_STATUSES`, line 32); default `acknowledged`.

## Dismissal persistence & signature

- `src/pr_guardian/persistence/models.py:328` — `FindingDismissalRow`
  (`signature` String(16) indexed, `status`, `comment`, `source_finding` JSON,
  `active`, resolution-tracking columns).
- `src/pr_guardian/persistence/storage.py`: `finding_signature` (2245,
  `hash16(file::category::agent_name)` — **excludes line** so it survives line
  shifts), `upsert_dismissal` (2250), `get_active_dismissals` (2315),
  `find_inline_comment_by_platform_id` (3147), `save_inline_comment_ids` (3117).
- `src/pr_guardian/platform/protocol.py:54-70` — `inline_finding_payload` stored
  per posted inline comment; mirrors dismissal `source_finding` so signatures
  match without re-derivation.
- `src/pr_guardian/core/orchestrator.py:1856-1868` — findings are stamped
  `f.primary_agent = ar.agent_name` before being posted as inline comments, so
  the payload's `agent_name` is always populated (an earlier concern about an
  empty `agent_name` producing a non-matching signature does not occur).

## Re-review machinery (the reusable core)

`src/pr_guardian/core/orchestrator.py`, `run_re_review` (line 939):

- **Step 2 (1116-1149):** collects `get_active_dismissals`, builds
  `dismissed_sigs`, and filters the original review's findings by
  `finding_signature(file, category, agent_name)`.
- **All-dismissed short-circuit (1164-1192):** if no active findings remain →
  `Decision.AUTO_APPROVE`, then `_post_results` + `_save_result`. No agents run.
- **Step 3 (1230-1263):** agent `re_evaluate(...)` — the LLM step. This is the
  only part a no-LLM re-decide must skip.
- **Shared decision (1346-1368):** recomputes finding-derived inputs
  (`combined_score`) from the surviving findings and **replays** structural
  inputs (trust tier, sticky triggers, repo risk, risk tier, target branch)
  from the original review, then calls `resolve_decision`. Inline comment:
  this exists so "re-review and full review can never diverge."

## Decision engine (IO-free)

- `src/pr_guardian/decision/engine.py:394` — `decide(...)` →
  `combined_score` + `resolve_decision`. No IO; per the architecture invariant
  `decision/` takes findings + config in, returns a verdict out.

## Posting results (canonical path)

`src/pr_guardian/core/orchestrator.py:1668-1764` — `_post_results`:

- posts `guardian/review` status via `_post_result_status` (line 1694),
  which posts to `pr.head_commit_sha` (`github.py:326`),
- re-asserts `guardian/readiness=success` (lines 1708-1717),
- posts inline/summary + guidance comments.
- `_save_result` (line 1516) persists the new `ReviewResult`.

## Fetching the live PR / adapter

- `src/pr_guardian/platform/github.py:350` — `fetch_pr(repo, pr_id)` returns a
  `PlatformPR` with the **live** `head_commit_sha` (line 366).
- `github_chatops.py:438` — the dismiss handler already builds a
  connection-scoped adapter via `_fresh_adapter_for_review(review)`.

## Known blast-radius history (memory)

- Status-webhook self-trigger loop → GitHub 1000-per-context cap → 422; fixed
  2026-06-22 with write-dedup + self-status guard. Re-posting status must go
  through the existing guard.
- Finalize once posted `guardian/review` success to a **stale stored head SHA**
  → check never went green. Any new status write must target the **live** head.

## Tests in the area

- `tests/test_github_chatops.py`, `tests/test_github_dismiss_command.py` —
  dismiss command parsing + recording.
- `tests/test_re_review_decision.py` — re-review decision computation
  (the shared tail).
- `tests/test_re_review_adapter.py` — adapter wiring for re-review.

## Platform gap

- ADO (`webhooks.py:212`, `platform/ado.py`) has **no inbound comment ingestion**
  and does not persist `id_to_findings` on threads — reply-to-dismiss and
  therefore auto-reapply are GitHub-only.
