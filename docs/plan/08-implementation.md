# PR Guardian — Implementation

## Package Structure

```
pr-guardian/
├── pyproject.toml
├── Dockerfile                   # Multi-stage: tools + Python app
├── docker-compose.dev.yml       # Local dev environment
│
├── src/
│   └── pr_guardian/
│       ├── __init__.py
│       ├── main.py              # FastAPI app entry point
│       ├── cli.py               # CLI entry point (all commands)
│       │
│       ├── api/                 # HTTP layer
│       │   ├── webhooks.py      # POST /api/webhooks/{platform}
│       │   ├── dashboard.py     # GET /dashboard/*
│       │   ├── config_api.py    # GET/PUT /api/config/{repo}
│       │   ├── health_api.py    # GET /api/health
│       │   ├── feedback_api.py  # GET /api/feedback
│       │   └── metrics_api.py   # GET /api/metrics
│       │
│       ├── platform/            # Platform adapters (ADO + GitHub)
│       │   ├── protocol.py      # PlatformAdapter Protocol
│       │   ├── ado.py           # Azure DevOps adapter
│       │   ├── github.py        # GitHub adapter
│       │   ├── models.py        # PlatformPR, normalized types
│       │   └── factory.py       # create_adapter(webhook) → PlatformAdapter
│       │
│       ├── core/                # Review orchestration (platform-agnostic)
│       │   ├── orchestrator.py  # Main review pipeline
│       │   ├── reviewer.py      # Coordinates stages 1-4
│       │   └── queue.py         # In-process asyncio task manager (dedup + cancel)
│       │
│       ├── languages/           # Language detection + registry
│       │   ├── detector.py
│       │   ├── registry.py
│       │   └── tool_configs/    # Per-language tool configurations
│       │       ├── python.yml
│       │       ├── typescript.yml
│       │       ├── csharp.yml
│       │       ├── go.yml
│       │       ├── sql.yml
│       │       ├── terraform.yml
│       │       └── dockerfile.yml
│       │
│       ├── mechanical/          # Stage 1: deterministic checks
│       │   ├── runner.py        # Run all applicable tools (language-conditional)
│       │   ├── semgrep.py
│       │   ├── gitleaks.py
│       │   ├── pii_scanner.py
│       │   ├── api_contracts.py
│       │   ├── migration_safety.py
│       │   ├── deps.py
│       │   └── results.py       # Unified mechanical result type
│       │
│       ├── discovery/            # Stage 0: context gathering
│       │   ├── blast_radius.py  # Transitive risk: changed file → consumers → propagated risk
│       │   ├── change_profile.py # Semantic classification: what kind of change + implied agents
│       │   ├── dep_graph.py     # Dependency graph loader (pre-computed DB + repo config fallback)
│       │   └── file_roles.py    # File role classification (production, test, docs, etc.)
│       │
│       ├── triage/              # Stage 2: classification
│       │   ├── classifier.py    # Risk tier logic (semantic, NOT line-count based)
│       │   ├── hotspots.py      # Git history analysis
│       │   ├── surface_map.py   # Security/perf file mapping
│       │   ├── path_risk.py     # Repo config path-level risk weights
│       │   └── work_item.py     # ADO work item linking
│       │
│       ├── agents/              # Stage 3: AI review agents
│       │   ├── base.py          # Base agent class
│       │   ├── prompt_composer.py
│       │   ├── context_builder.py
│       │   ├── security_privacy.py
│       │   ├── performance.py
│       │   ├── architecture_intent.py
│       │   ├── code_quality_obs.py
│       │   ├── test_quality.py
│       │   └── hotspot.py
│       │
│       ├── decision/            # Stage 4: decision engine
│       │   ├── engine.py        # Scoring + rules
│       │   └── actions.py       # Platform-agnostic decision actions
│       │
│       ├── llm/                 # LLM provider abstraction
│       │   ├── protocol.py      # LLMClient Protocol (~30 lines)
│       │   ├── anthropic.py     # Anthropic provider (~50 lines)
│       │   ├── openai_compat.py # OpenAI-compatible (~50 lines)
│       │   ├── azure_foundry.py # Azure AI Foundry (~50 lines)
│       │   └── factory.py       # create_client(config) (~20 lines)
│       │
│       ├── persistence/         # Database layer
│       │   ├── models.py        # SQLAlchemy / SQLModel models
│       │   ├── repository.py    # Data access
│       │   └── migrations/      # Alembic migrations
│       │
│       ├── feedback/            # Feedback loop
│       │   ├── logger.py
│       │   ├── analyzer.py
│       │   ├── tuner.py
│       │   └── reporter.py
│       │
│       ├── health/              # Codebase health (scheduled)
│       │   ├── checker.py
│       │   ├── trends.py
│       │   ├── mutation.py
│       │   └── alerts.py
│       │
│       ├── dashboard/           # Dashboard UI (HTMX + Jinja2)
│       │   ├── templates/       # Jinja2 templates with HTMX for interactivity
│       │   └── static/          # CSS (Tailwind/Pico), minimal JS
│       │
│       ├── config/
│       │   ├── schema.py
│       │   ├── loader.py
│       │   └── defaults.yml
│       │
│       └── models/              # Shared domain models
│           ├── pr.py
│           ├── context.py      # ReviewContext (Stage 0 output, consumed by all stages)
│           ├── languages.py
│           ├── findings.py
│           ├── feedback.py
│           └── output.py
│
├── prompts/                     # Agent system prompts (per-agent, per-language)
│   ├── security_privacy/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   ├── csharp.md
│   │   ├── go.md
│   │   ├── sql.md
│   │   ├── terraform.md
│   │   └── dockerfile.md
│   ├── performance/
│   ├── architecture_intent/
│   ├── code_quality_observability/
│   ├── test_quality/
│   ├── hotspot/
│   │   └── base.md
│   └── cross_language.md
│
├── tests/
│   ├── test_triage.py
│   ├── test_decision.py
│   ├── test_language_detection.py
│   ├── test_pii_scanner.py
│   ├── test_api_contracts.py
│   ├── test_feedback.py
│   ├── test_agents/
│   └── test_llm/
│
└── infra/
    ├── azure/                   # Cloud profile
    │   ├── container-app.bicep
    │   ├── database.bicep
    │   ├── registry.bicep
    │   └── keyvault.bicep
    ├── docker-compose/          # On-prem profile
    │   └── docker-compose.yml
    └── k8s/                     # Kubernetes profile
        ├── deployment.yml
        ├── service.yml
        ├── ingress.yml
        └── configmap.yml
```

---

## CLI Interface

```bash
# ─── Per-PR Pipeline Commands ───

# Detect languages in diff
pr-guardian detect-languages \
  --diff-target develop \
  --output languages.json

# Mechanical checks (individual)
pr-guardian scan-pii \
  --diff-target develop \
  --config review.yml \
  --output pii-results.json

pr-guardian check-api-contracts \
  --diff-target develop \
  --output api-contract-results.json

# Triage
pr-guardian triage \
  --config review.yml \
  --pr-id 12345 \
  --source-branch feature/add-login \
  --target-branch develop \
  --mechanical-results ./results/ \
  --output triage-result.json

# Run a single agent
pr-guardian review \
  --agent security_privacy \
  --config review.yml \
  --diff-target develop \
  --languages python,typescript,sql \
  --output security-privacy-result.json

# Decision + feedback
pr-guardian decide \
  --config review.yml \
  --risk-tier medium \
  --artifacts-dir ./agent-results/ \
  --pr-id 12345 \
  --ado-org https://dev.azure.com/myorg \
  --ado-project MyProject \
  --log-feedback=true

# ─── Complementary Commands ───

# Hotspots (scheduled, not per-PR)
pr-guardian hotspots --days 90 --output .pr-guardian/hotspots.json

# Health check (weekly)
pr-guardian health-check --config review.yml --output health-report.json
pr-guardian health-report --format html --input health-report.json --output health-report.html

# Feedback analysis (weekly)
pr-guardian feedback-analyze --feedback-dir feedback/ --days 7 --output weekly-feedback.json
pr-guardian feedback-recommend --analysis weekly-feedback.json --output threshold-recommendations.yml

# ─── Developer Utility ───

pr-guardian validate --config review.yml
pr-guardian dry-run --config review.yml --diff-target develop
pr-guardian estimate --config review.yml --diff-target develop
```

---

## Data Flow

```
Webhook → Dedup → Cancel-if-stale → In-process asyncio queue → Worker

  1. Webhook payload → deduplicate(repo, pr_id, head_sha)
  2. Cancel any in-flight review for same PR
  3. Platform adapter normalizes → PlatformPR
  4. Clone repo → /tmp/review-{pr-id}/
  5. Stage 0: Discovery → ReviewContext
     (parse diff, detect languages, load config, load hotspots,
      build security surface, compute blast radius, build change profile)
  6. Stage 1: Mechanical checks (language-conditional) → MechanicalResults[]
  7. Stage 2: Triage → RiskTier + AgentSet
  8. Stage 3: AI agents (parallel async) → AgentResult[]
  9. Stage 4: Decision engine (derives scores from findings) → Decision
  10. Platform adapter → PR comment / approve (never auto-merge)
  11. Persist to DB → reviews, findings, metrics, feedback
  12. Clean up temp directory

All in one process. No external message queues. No pipeline artifacts.
In-process asyncio handles concurrency, dedup, and cancellation.
Database is the persistent record of everything.
```

---

## Platform API Usage

```
Azure DevOps REST API:
  GET  /git/repositories/{id}/pullRequests/{id}     → PR metadata
  GET  /git/repositories/{id}/items?path=...         → Fetch config
  POST /git/pullRequests/{id}/threads                → Post comment
  POST /git/pullRequests/{id}/reviewers              → Approve (+10 vote)
  POST /git/pullRequests/{id}/labels                 → Add labels
  POST /git/pullRequests/{id}/statuses               → Set status check
  GET  /wit/workitems/{id}                           → Fetch linked work item

GitHub REST API:
  GET  /repos/{owner}/{repo}/pulls/{number}          → PR metadata
  GET  /repos/{owner}/{repo}/contents/{path}         → Fetch config
  POST /repos/{owner}/{repo}/pulls/{number}/reviews  → Post review
  POST /repos/{owner}/{repo}/issues/{number}/labels  → Add labels
  POST /repos/{owner}/{repo}/statuses/{sha}          → Set commit status
```

---

## Per-Repo Configuration

```yaml
# config/defaults.yml

# ─── LLM Provider Registry ───
llm:
  default_provider: anthropic

  providers:
    anthropic:
      type: anthropic
      api_key_env: ANTHROPIC_API_KEY
      default_model: claude-sonnet-4-6
      models: [claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5]

    azure-foundry:
      type: azure-openai
      endpoint_env: AZURE_OPENAI_ENDPOINT
      api_key_env: AZURE_OPENAI_KEY
      default_model: gpt-4o
      models: [gpt-4o, gpt-4o-mini]

    local-ollama:
      type: openai-compatible
      base_url: http://gpu-server.internal:11434/v1
      api_key: not-needed
      default_model: llama3.3:70b
      models: [llama3.3:70b, qwen2.5-coder:32b]

  max_tokens: 4096
  temperature: 0.1
  timeout_seconds: 120

  agent_overrides:
    security_privacy:
      model: claude-opus-4-6
    code_quality_observability:
      model: claude-haiku-4-5

# ─── Review Defaults ───
repo_risk_class: standard

# Reviewer assignment (human review escalation)
human_review:
  reviewer_group: "Developers"   # team/group name to assign as required reviewer
  # That's it. No CODEOWNERS, no git blame routing.
  # The team decides internally who picks it up.

thresholds:
  auto_approve_max_score: 4.0
  human_review_min_score: 4.0
  hard_block_score: 8.0

weights:
  security_privacy: 3.0
  test_quality: 2.5
  architecture_intent: 2.0
  performance: 1.5
  hotspot: 1.5
  code_quality_observability: 1.0

certainty_validation:
  detected_min_signals: 2
  suspected_min_signals: 1

triage:
  # Line counts are NOT used for tier classification.
  # Tiers are driven by change_profile (what changed) + blast_radius.
  # These thresholds are only used as context hints for agent timeout/depth.
  agent_context_thresholds:
    compact: 100     # lines — agents get shorter context window
    standard: 500    # lines — agents get standard context window
    deep: 500        # lines above this → agents get extended context + timeout

auto_approve:
  enabled: true
  allowed_target_branches: ["develop", "feature/*"]
  blocked_target_branches: ["release/*"]
  require_all_checks_pass: true
  # NOTE: auto-approve targets are configurable per repo. The 50-70% auto-approve
  # target applies to the allowed branches. PRs to blocked branches always require
  # human review regardless of risk tier or agent verdict.

agents:
  max_context_tokens: 32000
  timeout_seconds: 120

intent_verification:
  enabled: true
  work_item_source: auto       # auto-detected from platform (ado → work items, github → issues)
  require_linked_work_item: false

privacy:
  data_classification_file: "data-classification.yml"
  compliance_frameworks: ["gdpr"]

test_quality:
  min_assertion_quality_score: 0.5
  max_untested_path_ratio: 0.5

feedback:
  enabled: true
  log_all_decisions: true
  override_tracking: true
  weekly_report: true
```
