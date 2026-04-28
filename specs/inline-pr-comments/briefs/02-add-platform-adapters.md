---
title: "Add inline comment methods to platform adapters"
depends_on: []
acceptance_criteria:
  - { type: none, test: "python -m pytest", pass: "all tests pass", fail: "any failure" }
  - { type: none, test: "python -c \"from pr_guardian.platform.protocol import PlatformAdapter; import inspect; assert 'post_inline_comments' in [m for m in dir(PlatformAdapter)]; print('ok')\"", pass: "prints ok", fail: "AttributeError" }
  - { type: none, test: "python -c \"from pr_guardian.platform.github import GitHubAdapter; from pr_guardian.platform.ado import ADOAdapter; print('ok')\"", pass: "prints ok", fail: "ImportError" }
touches:
  - src/pr_guardian/platform/protocol.py
  - src/pr_guardian/platform/github.py
  - src/pr_guardian/platform/ado.py
does_not_touch:
  - src/pr_guardian/core/orchestrator.py
  - src/pr_guardian/persistence/
  - src/pr_guardian/config/
  - src/pr_guardian/dashboard/
---

## Task

Extend the platform abstraction layer with inline comment capabilities for both GitHub and Azure DevOps:

1. Add abstract methods `post_inline_comments()` and `delete_inline_comments()` to `PlatformAdapter` in `protocol.py`.
2. Implement both methods in `GitHubAdapter` (`github.py`).
3. Implement both methods in `ADOAdapter` (`ado.py`).

**Grouping rule**: findings are pre-grouped by caller before being passed here. The adapters receive findings already filtered to MEDIUM+ and grouped so that multiple findings at the same file+line arrive together. Each group maps to one comment.

**Lines outside diff**: GitHub's review comment API rejects anchoring to lines not in the diff — catch the 422 and silently skip that finding group. ADO behaves similarly. Do not raise; return IDs only for successfully posted comments.

**GitHub implementation**:
- Use `POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews` with a `comments` array and `"event": "COMMENT"`.
- Each element: `{"path": file, "line": line, "body": body}`.
- The review response contains `comments[].id` — collect these as the returned IDs.
- Delete via `DELETE /repos/{owner}/{repo}/pulls/comments/{comment_id}`.

**ADO implementation**:
- Use `POST /{org}/{project}/_apis/git/repositories/{repo}/pullRequests/{pr_id}/threads` with `threadContext`.
- `threadContext`: `{"filePath": "/<file>", "rightFileStart": {"line": line, "offset": 1}, "rightFileEnd": {"line": line, "offset": 200}}`.
- The response `id` is the thread ID — collect as returned IDs.
- ADO does not support thread deletion. "Delete" means `PATCH` the thread to `"status": 4` (byDesign) and append a reply comment: `"This comment was superseded by a re-review."`.

## Touches

- `src/pr_guardian/platform/protocol.py` — add two abstract methods per the contract in `design.md` → Contracts.
- `src/pr_guardian/platform/github.py` — implement `post_inline_comments()` and `delete_inline_comments()`.
- `src/pr_guardian/platform/ado.py` — implement `post_inline_comments()` and `delete_inline_comments()`.

## Does not touch

- `src/pr_guardian/core/orchestrator.py` — wiring is brief 03's responsibility.
- `src/pr_guardian/persistence/` — storage calls are brief 03's responsibility.
- `src/pr_guardian/config/` — already handled by brief 01.
- `src/pr_guardian/dashboard/` — UI is brief 04's responsibility.

## Constraints

Honor the method signatures from `design.md` → Contracts exactly:

```python
async def post_inline_comments(
    self,
    pr: PlatformPR,
    findings: list[Finding],
    *,
    threshold: str = "MEDIUM",
) -> list[str]: ...

async def delete_inline_comments(
    self,
    pr: PlatformPR,
    comment_ids: list[str],
) -> None: ...
```

Follow the HTTP request patterns in `design.md` → Reference reading:
- `github.py:84` for the request helper style.
- `ado.py:290` for the thread creation style.

Add unit tests that mock the HTTP layer and assert:
- `post_inline_comments` returns IDs for successfully posted comments.
- `post_inline_comments` silently skips findings that trigger a 422 (line not in diff).
- `delete_inline_comments` calls the correct endpoint per comment ID.

## Risks / pitfalls

- GitHub's `POST /pulls/{id}/reviews` with `"event": "COMMENT"` creates a *submitted* review, not a pending one — do not use `"event": "PENDING"` as it won't be visible until submitted.
- ADO thread `status` values: 1=active, 2=fixed, 3=wontFix, 4=byDesign, 5=pending. Use 4 (byDesign) for superseded comments, not 2 (fixed), to avoid confusing the PR author.
- ADO file paths in `threadContext.filePath` must start with `/` (e.g. `/src/foo.py`).

## Wrap-up
Before finishing:
1. Run `/simplify` and address its findings.
2. Re-run `python -m pytest`; must pass.
3. Commit and push.
