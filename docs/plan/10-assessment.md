# PR Guardian — Assessment & Open Questions

## What PR Guardian Handles Well (machines replace humans)

| Category | Coverage | How |
|----------|----------|-----|
| Style / formatting | ~100% | Mechanical: linters, formatters |
| Known vulnerability patterns | ~90% | Mechanical: Semgrep, CodeQL, SCA |
| Secret exposure | ~95% | Mechanical: Gitleaks + PII scanner |
| Architecture boundary violations | ~90% | Mechanical: dep-cruiser + fitness tests |
| API breaking changes | ~95% | Mechanical: oasdiff, buf |
| Migration safety | ~85% | Mechanical: squawk + agent review |
| Common perf anti-patterns | ~80% | Agent: N+1, O(n²), unbounded queries |
| Test existence + basic quality | ~75% | Agent: assertion quality, coverage gaps |
| PII in logs/output | ~80% | Mechanical scan + agent review |
| Observability gaps | ~70% | Fitness tests + agent review |
| Cross-language contract mismatches | ~60% | Agent review (harder, mostly "suspected") |

## What Still Needs Humans (machines assist, humans decide)

| Category | Why Machines Struggle | Guardian's Role |
|----------|----------------------|-----------------|
| "Is this the right approach?" | Requires business context, product vision | Intent verification gives a hint |
| Complex business logic correctness | Requires domain knowledge | Agents flag suspicious logic |
| Subtle concurrency bugs | Static analysis can't catch most races | Agent flags obvious patterns |
| Architectural vision / direction | Requires understanding where system is going | Checks against documented decisions |
| "Should we build this at all?" | Product decision, not code decision | Outside scope |
| Novel security attack vectors | Zero-day patterns not in any ruleset | Catches known patterns |
| UX / user impact of changes | Requires understanding user workflows | Outside scope |

---

## Honest Numbers

| Metric | Target |
|--------|--------|
| PRs that skip human review (auto-approved) | 50-70% (on standard repos) |
| Reduction in human review time for remaining PRs | 40-60% |
| Total human review effort saved | 70-80% |
| False negative rate (bugs that slip through) | <2% (comparable to human review) |
| False positive rate (unnecessary escalations) | <15% (after tuning) |
| Time from PR creation to auto-approve | <5 minutes |
| Time from PR creation to merge (auto-approved) | <10 minutes (author clicks merge) |
| Time from PR creation to merge (human path) | hours (but with focused brief) |
| Certainty downgrade rate | <20% (if >20%, prompts need tuning) |

---

## What Makes This Worth Building

1. **Consistency** — Agents don't have bad days, don't get review fatigue, don't rubber-stamp PRs on Friday afternoon
2. **Speed** — 5-minute auto-approve vs 1-3 day review queue
3. **Knowledge distribution** — Security expertise, perf patterns, architecture rules encoded in prompts and checks, not locked in one person's head
4. **Non-dev enablement** — Non-devs can ship without waiting days for a dev reviewer
5. **Agent enablement** — AI coding agents can iterate faster with automated feedback
6. **Audit trail** — Every PR decision logged with full reasoning

---

## Resolved Design Decisions

These were open questions, now resolved in the design docs:

- ~~**PR re-review**~~ → New push cancels in-flight review, full re-run from scratch. See [07-architecture.md](07-architecture.md) concurrency section.
- ~~**CI integration**~~ → Guardian runs alongside CI as independent PR check. Build, tests, and SonarCloud are CI-owned. See [07-architecture.md](07-architecture.md) CI integration section.
- ~~**Webhook dedup / concurrency**~~ → Dedup by (repo, pr_id, head_sha). Cancel-and-restart for same-PR updates. See [07-architecture.md](07-architecture.md).
- ~~**Agent scoring**~~ → No LLM-generated risk_score. Decision engine derives scores deterministically from findings (severity × validated_certainty). See [05-decision-engine.md](05-decision-engine.md).
- ~~**GitHub work items**~~ → Platform adapter supports ADO work items AND GitHub Issues for intent verification.

---

## Open Questions

1. **Azure DevOps permissions**: Verify build service identity can post PR comments, approve PRs (+10 vote), add labels, read work items

2. **Context window management**: Large PRs (>500 lines) may exceed context. Strategy: chunk large diffs by module, summarize unchanged context, or force flag for human review. If an agent can't see full context (`saw_full_context: false`), the decision engine treats its silence as untrusted.

3. **Cross-repo patterns**: Should findings from one repo inform reviews in another? (e.g., security patterns from payment-service applied to other services)

4. **Escape hatch**: `[skip-guardian]` in commit message for emergencies? Who can use it? Should it require approval from a specific group?

5. **Conflicting PRs**: Two PRs that are individually fine but conflict when both merge. Can the pipeline detect this? (probably not per-PR — needs merge queue)

6. **Data classification bootstrap**: Who defines the initial `data-classification.yml`? Need a process to classify existing data fields before the privacy agent can be effective.

7. **Mutation testing baseline**: First run on existing code will likely show poor scores everywhere. Need a strategy for progressive improvement, not blocking on legacy code.

8. **Repo risk class ownership**: Who sets the initial `repo_risk_class` per repo? Code owners, security team, or governance process?

9. **Certainty calibration monitoring**: Track downgrade rate over time. If agents consistently overclaim `detected` (>20% downgrade rate), prompts need tuning. Should this trigger automated prompt adjustments or just alerts?

10. **On-prem webhook ingress**: How do ADO/GitHub webhooks reach the Guardian service? Options: VPN, Azure Relay, ngrok-style tunnel, or ADO Server with direct network access.

11. **Local model quality threshold**: When a repo uses local models (Ollama/vLLM), should the decision engine apply stricter rules to compensate for potentially lower model accuracy?

12. **Multi-LLM single review**: If Guardian serves repos with different LLM providers, it holds API keys for all providers simultaneously. Secrets management strategy for on-prem deployments (no KeyVault)?

---

## Component Summary & Priority

| Component | Type | Purpose | Priority |
|-----------|------|---------|----------|
| Language detector | Mechanical | Detect languages in diff, drive everything | P0 |
| Semgrep SAST | Mechanical | Security static analysis | P0 |
| Gitleaks | Mechanical | Secret detection | P0 |
| PII scanner | Mechanical | PII in logs/output | P1 |
| API contract checker | Mechanical | Breaking change detection | P1 |
| Migration safety | Mechanical | DB migration linting | P1 |
| Fitness tests (template) | Mechanical | Architecture + observability enforcement | P1 |
| dep-cruiser / arch rules | Mechanical | Module boundary enforcement | P1 |
| Triage engine | Logic | Risk classification + agent routing | P0 |
| Work item linker | Integration | Fetch ADO work items + GitHub Issues | P1 |
| Security + Privacy agent | AI Agent | Vuln + privacy + GDPR review | P0 |
| Performance agent | AI Agent | Perf anti-pattern review | P1 |
| Architecture + Intent agent | AI Agent | Arch drift + intent verification | P1 |
| Code Quality + Observability agent | AI Agent | Quality + logging/tracing review | P1 |
| Test Quality agent | AI Agent | Test assertion + coverage quality | P1 |
| Hotspot agent | AI Agent | High-risk file extra scrutiny | P2 |
| Decision engine | Logic | Derived scoring + auto-approve/escalate rules | P0 |
| Webhook receiver | Infra | Dedup, concurrency control, asyncio queue | P0 |
| Feedback logger | Integration | Log all decisions for learning | P1 |
| Platform adapter (ADO + GitHub) | Integration | Platform API actions + work items | P0 |
| LLM abstraction (3 providers) | Infra | Anthropic, Azure AI Foundry, OpenAI-compat | P0 |
| Prompt library (per-agent, per-lang) | Content | Agent system prompts | P0 |
| Background scheduler | Infra | Hotspot refresh, health checks, mutation testing | P1 |
| Health dashboard (HTMX) | Complementary | Weekly codebase health trends | P2 |
| Feedback analyzer | Complementary | Weekly override analysis | P2 |
| Threshold tuner | Complementary | Auto-recommend threshold changes | P3 |
| Mutation testing integration | Complementary | Scheduled test quality scoring | P3 |

**P0** = MVP (first pilot repo) — get the core loop working
**P1** = Phase 2 (full single-repo deployment) — all agents, all mechanical checks
**P2** = Phase 3 (multi-repo rollout) — complementary systems, learning loop
**P3** = Phase 4 (optimization) — auto-tuning, mutation testing, cross-repo learning
