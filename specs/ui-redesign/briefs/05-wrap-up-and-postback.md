# Brief 05 — Wrap-up step + post-back to GitHub/ADO

## What
Build the wrap-up step of the viewer. Aggregates the reviewer's Accept/Fix/Dismiss decisions, surfaces a comment-to-author textarea with a pre-filled summary, exposes the inline-comment-mode toggle (Inline / Summary / None), and posts the decisions back to GitHub or ADO via the existing `inline-pr-comments` pipeline. After posting, navigates the reviewer to the next item in the queue.

## Why
Today, decisions made in the viewer don't go anywhere. The whole point of running Guardian + a human reviewer is to produce a posted PR review with comments. This is the closer.

## Where
- `src/pr_guardian/dashboard/review_viewer.html` — wrap-up rendered as the final wizard step or as a modal slide-over invoked by `Finish review` from Chapters/Findings modes.
- `src/pr_guardian/api/review.py` — new endpoint `POST /api/reviews/{id}/finalize` accepting:
  ```json
  {
    "decisions": { "<finding_id>": "accept" | "fix" | "dismiss" },
    "comment_to_author": "string",
    "verdict": "approve" | "request_changes" | "block",
    "comment_mode": "inline" | "summary" | "none"
  }
  ```
- Backend reuses the existing `inline-pr-comments` posting code (`src/pr_guardian/platform/`). The finalize endpoint translates the `decisions` map into the inline comment set (Fix → inline comment with the suggested fix; Accept/Dismiss → silent or audit-logged) and posts.
- `src/pr_guardian/persistence/` — extend the review record to persist `decisions` and `verdict` for audit.

## Wrap-up screen content
```
Wrap-up · PR #482

Decisions
  ✓ 4 accepted (silent)
  ✎ 2 fix requested  →  posted as inline comments
  — 1 dismissed (logged in audit)

Comment to author (final summary):
[ multiline textarea, pre-filled with a generated summary based on
  decisions — see "summary generation" below ]

Verdict:   [ ✓ Approve ]   [ ⟳ Request changes ]   [ ⊘ Block ]

Inline-comment mode for this post:  ● Inline (default)  ○ Summary only  ○ None
[ Post to GitHub → ]
```

## Summary generation
Client-side template:
```
{intro} {n_fix} concern(s) to address before merge:
- {finding[0].title}{finding[0].file_loc}
- ...
{outro}
```
Where:
- `intro` rotates: "Solid refactor overall." / "Reviewed — see notes below." / "Looks good, with the items below to resolve."
- Each `Fix` finding gets a line.
- `outro` is empty by default; user edits freely.

User can rewrite the textarea — the generator runs once on first wrap-up entry.

## Verdict
Three buttons map to:
- `approve` → GitHub: `submitReview(event: APPROVE)`; ADO: `vote: 10`
- `request_changes` → GitHub: `submitReview(event: REQUEST_CHANGES)`; ADO: `vote: -5`
- `block` → ADO: `vote: -10` (hard reject); GitHub: REQUEST_CHANGES + a system label `guardian:blocked` (GitHub has no first-class "block")

## Inline-comment mode
Reuses the existing tri-state from `inline-pr-comments` (`none` | `summary` | `inline`). The default is `inline`. The toggle on this page overrides the per-review default for *this post only*; doesn't change the system-wide setting.

## Post-back flow
1. Click "Post to GitHub →".
2. Disable buttons; show inline spinner.
3. Backend `POST /api/reviews/{id}/finalize`:
   - Persist decisions + verdict + comment.
   - Translate to platform calls (inline comments + final summary + verdict).
   - On success: respond `{posted: true, platform_url: "..."}`
   - On platform error: respond `{posted: false, error: "..."}` — UI shows retry.
4. Success: toast "Review posted to GitHub", redirect to next item in queue.
5. Failure: surface error inline, keep wrap-up state, allow retry.

## Repo-scan post-back
For `subject_type == "scan"`:
- Inline comments aren't applicable (no PR diff to anchor on).
- Wrap-up still produces a summary comment and verdict, but the verdict only persists in Guardian — no platform vote.
- A summary comment is posted as a GitHub issue (configurable) or ADO discussion thread under the repo. Future scope; for this brief, just persist Guardian-side and skip the platform post.

## Success signal
- Completing a real PR review and clicking "Post to GitHub" results in:
  - Inline comments on the right lines in GitHub.
  - A summary comment under the PR.
  - The verdict (APPROVE / REQUEST_CHANGES) on the PR.
- Decisions + comment + verdict are persisted server-side.
- Reviewer is auto-advanced to the next item in their queue.

## Non-goals
- Editing or deleting inline comments after post-back. Delete-and-repost only on re-review (already an `inline-pr-comments` non-goal).
- Multiple reviewers' decisions merged into one post. One reviewer = one finalize call.
- Email/Slack notification to the PR author. Platform-native comments suffice.
- Repo-scan summary auto-posted to platform — persist only for this brief.

## Validation
1. Open a small reviewed PR with 2 high findings.
2. Mark one "Fix", one "Accept".
3. Wrap-up shows the summary generated correctly.
4. Click "Post to GitHub" — actual inline comment appears on the right line + summary comment + REQUEST_CHANGES verdict.
5. Reviewer redirected to next queue item.
6. Force a platform error (revoke PAT scope) — see inline error + retry button; state preserved.
