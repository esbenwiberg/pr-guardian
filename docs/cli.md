# PR Guardian CLI Reference

## Installation

```bash
pip install pr-guardian
```

All commands are available under `pr-guardian` (or `python -m pr_guardian.cli`).

---

## Service

### `serve`

Start the PR Guardian HTTP service (webhook listener + dashboard).

```bash
pr-guardian serve [--host 0.0.0.0] [--port 8000]
```

---

## Configuration

### `validate`

Validate a `review.yml` configuration file.

```bash
pr-guardian validate [--config review.yml]
```

### `detect-languages`

Detect languages in a set of files.

```bash
pr-guardian detect-languages [--diff-target main] [--output result.json] file1.py file2.ts
# or pipe from stdin
git diff --name-only main | pr-guardian detect-languages
```

### `dry-run`

Run triage classification without invoking AI agents. Useful for testing risk classification rules.

```bash
pr-guardian dry-run [--config .] [--diff-target main] file1.py file2.ts
```

---

## Reviews

### `reviews`

List recent reviews.

```bash
pr-guardian reviews [--limit 20] [--repo owner/repo] [--decision human_review] [--json-output]
```

| Option | Default | Description |
|---|---|---|
| `--limit` | 20 | Max reviews to show |
| `--repo` | — | Filter by repository |
| `--decision` | — | Filter: `auto_approve`, `human_review`, `reject`, `hard_block` |
| `--json-output` | — | Output raw JSON |

### `review <review_id>`

Show full review detail with all findings, dismissal status, and agent verdicts.

```bash
pr-guardian review abc12345-...
pr-guardian review abc12345-... --json-output
```

### `my-reviews <author>`

Show recent reviews for a specific PR author.

```bash
pr-guardian my-reviews octocat
pr-guardian my-reviews octocat --limit 5 --decision human_review
pr-guardian my-reviews octocat --json-output
```

| Option | Default | Description |
|---|---|---|
| `--limit` | 10 | Max reviews to show |
| `--decision` | — | Filter by decision |
| `--json-output` | — | Output raw JSON |

---

## Finding Management

### `dismiss <finding_id>`

Dismiss a single finding by its UUID.

```bash
pr-guardian dismiss <finding-uuid> --status false_positive
pr-guardian dismiss <finding-uuid> --status by_design --comment "Intentional for backward compat"
```

| Option | Required | Values |
|---|---|---|
| `--status` | Yes | `by_design`, `false_positive`, `acknowledged`, `will_fix` |
| `--comment` | No | Free-text explanation |

### `batch-dismiss <review_id>`

Dismiss multiple findings from a review in one command.

```bash
# Dismiss all findings in a review
pr-guardian batch-dismiss <review-uuid> --status false_positive

# Dismiss specific findings
pr-guardian batch-dismiss <review-uuid> --status acknowledged \
  --finding-ids "uuid1,uuid2,uuid3"

# Dismiss only low-severity findings
pr-guardian batch-dismiss <review-uuid> --status false_positive --severity low

# Dismiss low + medium severity
pr-guardian batch-dismiss <review-uuid> --status false_positive --severity medium
```

| Option | Required | Description |
|---|---|---|
| `--status` | Yes | `by_design`, `false_positive`, `acknowledged`, `will_fix` |
| `--comment` | No | Free-text explanation |
| `--finding-ids` | No | Comma-separated UUIDs (default: all findings) |
| `--severity` | No | Dismiss findings at this severity or lower (`low`, `medium`, `high`, `critical`) |

### `re-review <review_id>`

Trigger a re-review of the same PR. Active dismissals are injected as context — agents won't re-flag dismissed findings unless new code changes make them relevant.

```bash
pr-guardian re-review <review-uuid>
pr-guardian re-review <review-uuid> --no-comment   # don't post PR comment
```

---

## Scans

### `scan-recent`

Run a recent-changes scan on merged code.

```bash
pr-guardian scan-recent --repo owner/repo [--platform github] [--days 7] [--since 2024-01-01]
```

### `scan-maintenance`

Run a maintenance scan to find stale files needing attention.

```bash
pr-guardian scan-maintenance --repo owner/repo [--platform github] [--staleness 6] [--max-files 50]
```

---

## Agent Workflow Example

A typical agent workflow using the CLI:

```bash
# 1. Pull your reviews
pr-guardian my-reviews octocat --json-output | jq '.[] | select(.decision == "human_review")'

# 2. Inspect findings for a specific review
pr-guardian review <review-id>

# 3. Dismiss false positives
pr-guardian dismiss <finding-id> --status false_positive --comment "Not applicable in this context"

# 4. Batch dismiss low-severity noise
pr-guardian batch-dismiss <review-id> --status acknowledged --severity low

# 5. Re-review with dismissal context
pr-guardian re-review <review-id>
```
