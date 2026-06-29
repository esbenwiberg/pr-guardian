# Nightly range review & commit-range scans

PR Guardian reviews PRs as they happen (via the GitHub App webhook). This doc
covers the *other* axis: reviewing a **commit range** — "everything since commit
X" or "everything since time T" — on a schedule. It's the heavy half of a
**thin/fat** split:

| Tier | When | What runs | Where |
|---|---|---|---|
| **Thin** | every commit / PR | lint, typecheck, unit tests | your CI (`guardian-pr-thin.yml`) + Guardian's webhook auto-review |
| **Fat** | nightly | full PR pipeline over the day's merged diff | hosted Guardian, triggered from CI (`guardian-nightly-range.yml`) |

The fast path stays fast; the expensive, full-fleet review runs once a night
against only the new commits.

## Two ways to review a range

There are two endpoints, for two different jobs:

### 1. Range review — `POST /api/review/range`

Runs the **full PR-review pipeline** (mechanical gates → triage → the six
specialist agents → decision) over the `base..head` compare diff, and produces a
real **verdict** (`auto_approve` / `human_review` / `reject` / `hard_block`).
This is "review this slice of history exactly like a PR."

```bash
curl -X POST "$GUARDIAN_BASE_URL/api/review/range" \
  -H "Authorization: Bearer $GUARDIAN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "owner/repo",
    "platform": "github",
    "since_commit": "<base sha or ref>",
    "head": "<head sha or ref>",
    "branch": "main"
  }'
# → {"status":"queued","review_id":"…"}
```

Time-based instead of commit-based? Send `"since": "2026-06-28T03:00:00Z"`
instead of `since_commit` (exactly one of the two). Guardian resolves the base
from branch history.

Poll for the verdict:

```bash
curl -H "Authorization: Bearer $GUARDIAN_API_KEY" \
  "$GUARDIAN_BASE_URL/api/dashboard/reviews/<review_id>"
# → {"decision":"auto_approve","stage":"complete","finished_at":"…", …}
```

The verdict is **informational** — there is no PR to approve or block, so
Guardian writes nothing back to the repo. Your CI job reads the decision and
decides whether to fail (see `examples/github/guardian-nightly-range.yml`).

### 2. Commit-range scan — `POST /api/scan/recent` with `base_ref`

Runs the **macro scan agents** (trend, consistency, integration-risk,
architecture-drift) over a range instead of a time window. Use this for
aggregate/portfolio findings you turn into issues, not a pass/fail verdict.

```bash
curl -X POST "$GUARDIAN_BASE_URL/api/scan/recent" \
  -H "Authorization: Bearer $GUARDIAN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"repo":"owner/repo","platform":"github","base_ref":"<base>","head_ref":"<head>"}'
```

Without `base_ref` it behaves as before — a time-window scan over the last
`time_window_days`.

**Rule of thumb:** want a gate? Use `/api/review/range`. Want a triage list of
themes across many merges? Use the scan.

## CLI equivalents (local / self-hosted runners)

```bash
pr-guardian review-range --repo owner/repo --since-commit <base> --head <head>
pr-guardian review-range --repo owner/repo --since 2026-06-28T03:00:00Z
pr-guardian scan-recent  --repo owner/repo --base <base> --head <head>
```

## Baseline tracking

The nightly workflow tags the reviewed head as `guardian/last-reviewed` and
starts the next run from there, so each night reviews only the new commits and
never re-reviews unchanged code. A failed verdict does **not** advance the tag —
the same range is re-reviewed next run rather than skipped.

## Auth

Range and scan trigger endpoints require an authenticated, write-capable caller:
a signed-in dashboard user **or** an API key with the `write` scope. Issue a key
from the admin **API keys** page and store it as the `GUARDIAN_API_KEY` Actions
secret. Read-only polling of `/api/dashboard/reviews/{id}` accepts the same key.

## Platform notes

- **GitHub:** `since_commit` / `head` accept SHAs, branch names, or tags — they
  pass through to the compare API verbatim.
- **Azure DevOps:** pass concrete commit **SHAs**; branch names are not accepted
  by ADO's compare API.

See `examples/github/` for ready-to-adapt workflows.
