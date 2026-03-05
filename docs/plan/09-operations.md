# PR Guardian — Operations

## Rollout Strategy

### Phase 0: Foundation (1 week)
- Set up pr-guardian repo with package structure
- Implement language detection + triage engine (no agents yet)
- Deploy mechanical checks (semgrep, gitleaks)
- Run on 1 pilot repo, shadow mode only

### Phase 1: Shadow Mode — Mechanical Only (2 weeks)
- All new mechanical checks live on pilot repo
- Reports findings as PR comments
- Does NOT block or auto-approve
- Humans review all PRs normally
- Collect data: false positive rate for mechanical checks
- Tune semgrep rules, fitness tests based on noise level

### Phase 2: Shadow Mode — Full Pipeline (2 weeks)
- Add all 6 AI agents
- Pipeline recommends decision (would-auto-approve / would-escalate)
- Still doesn't act on it
- Humans compare their judgment to the pipeline
- Tune agent prompts based on disagreements
- Start feedback logging

### Phase 3: Auto-approve Trivial (2 weeks)
- Only TRIVIAL PRs auto-approved on standard repos
- Everything else still goes to humans but with agent reports
- Build trust incrementally
- Roll out to additional repos (still shadow mode on new repos)

### Phase 4: Auto-approve Low (2 weeks)
- LOW tier auto-approves on standard repos if all agents pass
- MEDIUM and HIGH still go to humans
- Monitor false negative rate closely
- Weekly feedback analysis starts

### Phase 5: Auto-approve Low + Medium (ongoing)
- MEDIUM tier auto-approves on standard repos if agents pass and score < threshold
- HIGH tier always goes to human
- Elevated repos: only trivial + low auto-approve
- Critical repos: never auto-approve
- Monitor and tune continuously
- Roll out to all repos

### Phase 6: Full Operation + Health Dashboard
- All tiers operate as designed per repo risk class
- Codebase Health Dashboard deployed
- Mutation testing running nightly
- Feedback loop fully operational
- Weekly metrics review
- Monthly threshold tuning
- Quarterly agent prompt refinement

---

## Complementary Systems

### Codebase Health Dashboard

**Schedule**: Weekly automated run + monthly deep analysis

**Metrics tracked over time**:
- Complexity trends per module
- Test coverage trends
- Dependency freshness (how many deps >6 months behind?)
- Hotspot evolution
- Architecture boundary violations trend
- Duplication trends
- Code ownership concentration
- Tech debt ratio

**Alerts** (threshold-based):
- Test coverage dropped >5% in 30 days → alert tech lead
- Cyclomatic complexity in any module >80th percentile → flag for refactoring
- >10 dependencies with known CVEs → alert security team
- Any module with 0% code ownership (nobody changed it in 6 months) → orphan risk

### Hotspot Computation (Scheduled)

**Schedule**: Nightly (lightweight git analysis)
**Runner**: Background task inside the Guardian service using APScheduler (or
equivalent). Configured via `config/defaults.yml`:

```yaml
scheduled_tasks:
  hotspot_refresh:
    cron: "0 2 * * *"        # 2 AM daily
    timeout_minutes: 30
  health_check:
    cron: "0 3 * * 0"        # 3 AM Sundays
    timeout_minutes: 60
  mutation_testing:
    cron: "0 4 * * 0"        # 4 AM Sundays (weekly)
    timeout_minutes: 120
```

The scheduler runs in the same process. No external cron or job runner needed.
Stores results in PostgreSQL (`hotspots` table), keyed by repo + date.

---

### Mutation Testing (Scheduled)

**Schedule**: Nightly or weekly (too slow for per-PR)

**Tools**: mutmut (Python), Stryker (JS/TS), Stryker.NET (C#), go-mutesting (Go), PITest (Java)

**How it works**:
1. Inject small mutations into code (change `>` to `>=`, flip a boolean)
2. Run test suite against each mutation
3. If tests still pass → tests don't cover that logic
4. Report mutation survival rate per module

**Integration with PR Guardian**:
- Mutation scores stored per-module in `.pr-guardian/mutation-scores.json`
- If PR touches files in a module with low mutation score (<60%), test quality agent gets extra context
- Health dashboard tracks mutation scores over time

### Feedback Loop

**What gets logged** (every PR):
```json
{
  "pr_id": 12345,
  "repo": "api-service",
  "repo_risk_class": "standard",
  "risk_tier": "medium",
  "agents_run": ["security_privacy", "code_quality_obs", "test_quality"],
  "agent_verdicts": { ... },
  "certainty_downgrades": 1,
  "combined_score": 3.1,
  "guardian_decision": "auto_approve",
  "human_outcome": null,
  "human_override": false,
  "post_merge_incidents": []
}
```

**Weekly analysis**:
- How many PRs auto-approved vs escalated?
- How many human overrides?
- Which agents are most/least accurate?
- Which repos have highest false positive rates?
- What types of findings do humans consistently dismiss? (→ tune prompts)
- What types of bugs slipped through? (→ add new checks)

**Prompt refinement process**:
1. Collect the 10 biggest disagreements of the week
2. For each: what did agent say, what did human do, what was right answer?
3. Adjust agent prompts to reduce false positives / increase true positives
4. Run shadow mode for adjusted prompts for a week before deploying

**Threshold auto-tuning**:
- Auto-approve rate <30% → thresholds too tight
- Post-merge incident rate >2% → thresholds too loose
- Always require human approval before applying threshold changes

**Feedback Storage**:
- PostgreSQL from day one (same database as reviews, findings, and metrics)
- All feedback data lives in the `feedback` and `overrides` tables
- Dashboard queries feedback directly — no file sync needed

---

## Metrics to Track

### Per-PR Metrics (real-time)

| Metric | Why |
|--------|-----|
| PRs auto-approved vs escalated (ratio) | Efficiency |
| Time to merge (auto-approved vs human-reviewed) | Speed |
| Agent cost per PR (tokens x price) | Cost |
| Pipeline duration per stage | Bottleneck identification |
| Certainty downgrades per PR | Agent calibration |

### Quality Metrics (weekly)

| Metric | Why |
|--------|-----|
| False negatives (bugs after auto-approve) | Safety |
| False positives (unnecessary escalations) | Noise |
| Agent agreement rate with humans | Calibration |
| Override rate by agent | Which agents need prompt tuning? |
| Override rate by repo | Which repos need config tuning? |
| Hotspot prediction accuracy | Are flagged files actually buggy? |
| Intent verification accuracy | Does work item linking help? |

### Trend Metrics (monthly)

| Metric | Why |
|--------|-----|
| Test mutation survival rate | Test quality trend |
| Architecture violation trend | Design drift |
| Complexity trend per module | Complexity creep |
| Coverage trend per module | Coverage drift |
| Dependency freshness | Maintenance health |
| PR author mix (dev/non-dev/agent) | Code production shift |
| Review effort saved (hours estimated) | ROI |

---

## Cost Estimation

Per PR (worst case — HIGH tier, all 6 agents):
- 6 agent calls x ~10K tokens input x ~2K tokens output
- SaaS (Claude Sonnet): ~$0.20-0.40 per PR
- Average across all tiers: ~$0.06-0.12 per PR

Total for 100 PRs/week:

| Profile | Cost/month |
|---------|------------|
| Cloud + SaaS LLM | ~$60-95 |
| Cloud + Azure AI Foundry | ~$55-90 |
| On-prem + SaaS LLM | ~$40-60 (LLM only) |
| Full on-prem + local LLM | ~$0 (hardware only) |

Pipeline agents consumed: **ZERO**

Compare to: 1 senior dev spending 30 min reviewing a PR that could have been
auto-approved = ~$50/hour x 0.5h x 60 PRs saved/week = ~$6,000/month saved.

**ROI: ~100x return on investment.**
