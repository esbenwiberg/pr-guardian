# PR Guardian API Reference

Base URL: `http://localhost:8000/api`

---

## Reviews

### Trigger a Review

```
POST /api/review
```

Start a review for a PR by URL. Returns immediately — the review runs in the background.

**Request:**
```json
{
  "pr_url": "https://github.com/owner/repo/pull/123",
  "dry_run": false,
  "post_comment": false
}
```

**Response:**
```json
{
  "status": "queued",
  "pr_id": "123",
  "repo": "owner/repo",
  "platform": "github"
}
```

---

## Dashboard

### Stats

```
GET /api/dashboard/stats
```

Aggregate statistics: total reviews, decision counts, risk tier distribution, severity counts, avg score, avg cost, top repos.

### List Reviews

```
GET /api/dashboard/reviews?limit=50&offset=0&repo=owner/repo&decision=human_review&author=octocat
```

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 50 | Max results (1–200) |
| `offset` | int | 0 | Pagination offset |
| `repo` | string | — | Filter by repository |
| `decision` | string | — | Filter: `auto_approve`, `human_review`, `reject`, `hard_block` |
| `author` | string | — | Filter by PR author |

### My Reviews

```
GET /api/dashboard/my-reviews?author=octocat&limit=10&decision=human_review
```

Convenience endpoint for fetching a specific author's reviews.

| Param | Type | Default | Description |
|---|---|---|---|
| `author` | string | **required** | PR author username |
| `limit` | int | 10 | Max results (1–100) |
| `decision` | string | — | Filter by decision |

### Review Detail

```
GET /api/dashboard/reviews/{review_id}
```

Full review with all agent results, findings (enriched with dismissal status), mechanical results, and prior dismissals.

### Active Reviews

```
GET /api/dashboard/active
```

Returns reviews currently in progress (not yet finished).

### Cancel Review

```
DELETE /api/dashboard/reviews/{review_id}
```

Cancel a stuck review, marking it as errored.

---

## Finding Dismissals

### Dismiss a Finding

```
POST /api/dashboard/findings/{finding_id}/dismiss
```

**Request:**
```json
{
  "status": "false_positive",
  "comment": "Not applicable in this context"
}
```

**Status values:** `by_design`, `false_positive`, `acknowledged`, `will_fix`

**Response:**
```json
{
  "id": "dismissal-uuid",
  "signature": "a1b2c3d4e5f6g7h8"
}
```

### Batch Dismiss

```
POST /api/dashboard/findings/batch-dismiss
```

Dismiss multiple findings in one request.

**Request:**
```json
{
  "finding_ids": ["uuid-1", "uuid-2", "uuid-3"],
  "status": "false_positive",
  "comment": "Batch dismissed by agent"
}
```

**Response:**
```json
{
  "dismissed": 3,
  "not_found": [],
  "signatures": ["sig1", "sig2", "sig3"]
}
```

### Remove Dismissal

```
DELETE /api/dashboard/dismissals/{dismissal_id}
```

Un-dismiss a previously dismissed finding.

---

## Re-Review

### Trigger Re-Review

```
POST /api/dashboard/reviews/{review_id}/re-review
```

Re-runs the full review pipeline with dismissal context injected. Agents see previously dismissed findings and won't re-flag them unless new code changes make them relevant.

**Response:**
```json
{
  "status": "queued",
  "pr_id": "123",
  "dismissal_count": 5
}
```

---

## Real-Time Events

### SSE Stream

```
GET /api/dashboard/events
```

Server-Sent Events stream for real-time review progress. Events include stage transitions and completion.

---

## Scans

### Recent Changes Scan

```
POST /api/scan/recent
```

**Request:**
```json
{
  "repo": "owner/repo",
  "platform": "github",
  "time_window_days": 7,
  "since": null
}
```

### Maintenance Scan

```
POST /api/scan/maintenance
```

**Request:**
```json
{
  "repo": "owner/repo",
  "platform": "github",
  "staleness_months": 6,
  "max_files": 50
}
```

### List Scans

```
GET /api/dashboard/scans?limit=50&offset=0&repo=owner/repo&scan_type=recent_changes
```

### Scan Detail

```
GET /api/dashboard/scans/{scan_id}
```

### Scan Stats

```
GET /api/dashboard/scan-stats
```

---

## Prompts

### List Prompts

```
GET /api/dashboard/prompts
```

All agent prompts with override status, defaults, and shared system sections.

### Update Prompt

```
PUT /api/dashboard/prompts/{agent_name}
```

**Request:**
```json
{
  "content": "Your custom prompt content here..."
}
```

### Reset Prompt

```
DELETE /api/dashboard/prompts/{agent_name}
```

Reverts to the file default.

---

## Settings

### Get Settings

```
GET /api/dashboard/settings
```

Current LLM provider config (API keys are masked).

### Update Settings

```
PUT /api/dashboard/settings
```

**Request:**
```json
{
  "active_provider": "anthropic",
  "anthropic_api_key": "sk-ant-..."
}
```

---

## Health

```
GET /api/health
```

---

## Webhooks

### GitHub

```
POST /api/webhooks/github
```

Receives GitHub `pull_request` events. Requires webhook secret for signature verification.

### Azure DevOps

```
POST /api/webhooks/ado
```

Receives Azure DevOps pull request events.

---

## Agent Workflow Example

A typical agent workflow using the API:

```bash
# 1. Fetch reviews for a user
curl "http://localhost:8000/api/dashboard/my-reviews?author=octocat"

# 2. Get review detail with findings
curl "http://localhost:8000/api/dashboard/reviews/{review_id}"

# 3. Batch dismiss false positives
curl -X POST "http://localhost:8000/api/dashboard/findings/batch-dismiss" \
  -H "Content-Type: application/json" \
  -d '{"finding_ids": ["id1", "id2"], "status": "false_positive", "comment": "Agent dismissed"}'

# 4. Re-review with dismissal context
curl -X POST "http://localhost:8000/api/dashboard/reviews/{review_id}/re-review"
```
