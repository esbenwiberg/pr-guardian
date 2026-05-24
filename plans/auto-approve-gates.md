# Auto-Approve Gate Hardening

> Make auto-approve explicit opt-in via `.pr-guardian.yml`, add a hot-path
> gate, and replace the stubbed nightly hotspot computation with on-demand
> per-file lookups so the gate works on day 1 for any repo.

## Motivation

Three problems with the current auto-approve path:

1. **Silent-default risk.** `AutoApproveConfig.enabled` defaults to `True`.
   A repo with no `.pr-guardian.yml` at all can have PRs auto-approved
   using built-in defaults. The team never declared they wanted this.
2. **No hot-path gate.** Guardian's TrustTier system blocks auto-approve
   based on path sensitivity (security-critical, infra, config). It does
   nothing about *change history*. A "normal-looking" path that's actually
   a churn-heavy bug magnet can sail through auto-approve.
3. **Hotspot computation is fundamentally wrong for Guardian's shape.**
   `triage/hotspots.py::load_hotspots` is stubbed with `TODO: implement DB
   lookup once persistence layer is wired` and a "nightly job" model. But
   Guardian is webhook-driven — repos arrive without registration. A new
   repo's first PR has no nightly data and never will until the next run.
   The existing hotspot review agent silently no-ops today as a result.

---

## Changes

### 1. Require explicit opt-in for auto-approve

| Change | Today | After |
|---|---|---|
| `AutoApproveConfig.enabled` default | `True` | `False` |
| Behavior with no `.pr-guardian.yml` | Auto-approve possible | Auto-approve impossible |
| Behavior with `.pr-guardian.yml` but no explicit `auto_approve.enabled` | Defaults applied (auto-approve possible) | Default `False` (auto-approve impossible) |
| `repo_risk_class` default | `"standard"` | No default — must be explicit when `auto_approve.enabled: true` |

Net rule: **auto-approve fires only if the repo's `.pr-guardian.yml`
explicitly sets `auto_approve.enabled: true` AND explicitly sets
`repo_risk_class`.** Anything else (weights, thresholds, branch lists)
can still default — those are tuning knobs, not consent.

**Migration:** This is a breaking change for repos that currently rely
on defaulted auto-approve. Two options:
- Hard cutover with a release note + dashboard banner ("auto-approve now
  requires explicit configuration — your repo has been moved to
  human-review-everything mode")
- Soft cutover: log a warning for one release cycle, then enforce

Open question — see below.

### 2. Hot-path gate

Add to the auto-approve fail-closed conditions: **if any file in the PR
appears in the repo's hotspot set, auto-approve is blocked**.

Config:
```yaml
auto_approve:
  respect_hotspots: true        # default true
  hotspot_threshold:
    min_commits_90d: 8          # how much churn counts as "hot"
    min_fix_ratio: 0.3          # fraction of commits that are bug fixes
```

Semantically parallel to TrustTier's `HUMAN_PRIMARY` and `MANDATORY_HUMAN`
floors — same shape ("this code needs human eyes"), different reason
(change-history vs path-sensitivity). The two compose: a file can be hot
*and* security-critical; either is enough to block.

### 3. On-demand hotspot computation

Replace the stubbed `load_hotspots(repo) -> set[str]` with per-file
`is_hotspot(repo, file_path) -> bool` queried at review time.

**Why on-demand, not nightly:**
- Guardian is webhook-driven — no registration step, no warm-up window
- A new repo's first PR must get a correct gate immediately
- A nightly model would require either a registration step (friction
  against zero-config webhook) or "first PR is always ungated" (wrong)
- Computing hotness per-file for the diff is O(diff_size), not O(repo_size)

**Algorithm sketch:**

```
def is_hotspot(repo, file_path):
    cache_key = (repo, file_path)
    if cached := lru.get(cache_key):
        return cached  # 24h TTL

    # Query last 90d of commits touching this file via platform API.
    # GitHub: GET /repos/{owner}/{repo}/commits?path={file}&since=90d
    # ADO:    equivalent commits endpoint
    commits = platform.list_commits(repo, file_path, since="90d")
    fix_commits = sum(1 for c in commits if _looks_like_fix(c.message))

    is_hot = (
        len(commits) >= config.min_commits_90d
        and (fix_commits / len(commits)) >= config.min_fix_ratio
    )
    lru.put(cache_key, is_hot)
    return is_hot
```

**Cost analysis:**
- Typical PR: <20 files = <20 API calls
- GitHub authenticated rate limit: 5000/hr (well within budget at expected volume)
- Latency: ~50ms per API call sequentially, less with GraphQL batching
- GraphQL optimization: batch into one `commits(path: $file, first: 50)`
  query per PR if needed

**Cache strategy:**
- Per-file LRU with 24h TTL — gives the "compute once per day" benefit of
  nightly without the cold-start problem
- Cache lives in the same Postgres instance Guardian already uses
- First PR to touch a file computes it; subsequent PRs hit the cache

**Side effect:** This also unblocks the existing **hotspot review agent**,
which has been a silent no-op since deployment (because
`load_hotspots` returns `set()`). After this change, the hotspot agent
actually runs on hot files in the PR.

---

## Composition: all auto-approve gates

After these changes, auto-approve fires only if **all** of:

| Gate | Source |
|---|---|
| `.pr-guardian.yml` exists with explicit `auto_approve.enabled: true` | New |
| `repo_risk_class` explicitly set | New |
| Target branch matches `allowed_target_branches` | Existing |
| CI checks pass (`require_all_checks_pass`) | Existing |
| TrustTier ≤ `SPOT_CHECK` (no `MANDATORY_HUMAN` / `HUMAN_PRIMARY` files) | Existing |
| **No file in PR ∩ hotspots** | **New** |
| Weighted finding score ≤ `auto_approve_max_score` | Existing |

---

## Out of Scope (already discussed and skipped)

- **LOC cap on auto-approve.** Considered, rejected for now.
- **Governance audit trail on model swaps.** Considered, rejected for now.
- **Author-skill tier (non-dev vs pro-dev).** New dimension orthogonal
  to TrustTier; would calibrate auto-approve thresholds per author.
  Skipped for now — revisit if defect-rate-per-author data justifies it.
- **Nightly precomputation as a cache layer behind on-demand.** Could
  grow into this later for dashboards/rankings, but the source of truth
  stays on-demand. The 24h LRU is enough for now.

---

## Open Questions

- **Migration strategy for the `enabled: True` → `False` default flip.**
  Hard cutover with banner, or soft cutover with warning period? Either
  way, current users will be surprised — needs a release note.
- **Hotspot threshold defaults (`min_commits_90d`, `min_fix_ratio`).**
  Need real-world calibration. The proposed `8 commits / 30% fix ratio`
  is a guess. First implementation should ship with conservative defaults
  and a way to inspect per-file scores in the dashboard.
- **"Looks like a fix" heuristic.** Commit-message regex (`fix|bug|hotfix|
  patch|revert`) is crude. Better signals: linked-issue type, label,
  conventional-commits `fix:` prefix. Start crude, refine.
- **Per-repo override of hotspot threshold.** Default thresholds are
  unlikely to fit every repo (a hot file in a slow-moving codebase is
  not the same as in a fast-moving one). Already covered by
  `auto_approve.hotspot_threshold` — but should also support disabling
  the gate per-path (e.g., "this file is intentionally hot, ignore it").
