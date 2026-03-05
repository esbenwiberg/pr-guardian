# PR Guardian — Technical Architecture

## Design Principles

1. **Service-hosted** — runs as a containerized service, triggered by webhooks
2. **Platform-agnostic** — supports Azure DevOps AND GitHub from the same service
3. **LLM-agnostic** — swap between Claude, Azure AI Foundry, Ollama, vLLM via config (per-repo)
4. **Hosting-agnostic** — same Docker image runs on Azure Container App, on-prem Docker, or any k8s cluster. Application code must not import any cloud-provider hosting SDK.
5. **Zero pipeline agents consumed** — all review work runs on our infra, not CI agents
6. **Persistent** — feedback, metrics, dashboards, and learning all in one place
7. **Config-driven** — per-repo behavior without code changes

---

## Why Service-Hosted (Not Pipeline-Native)

| Concern | Pipeline-Native | Service-Hosted |
|---------|----------------|----------------|
| Pipeline agent usage | 6-16 jobs per PR | **Zero** |
| Multi-platform (ADO + GitHub) | No — platform-locked | **Yes** — webhook adapter |
| Dashboard + metrics | Can't serve UI | **Built-in** |
| Feedback loop | Awkward (git commits) | **Native** (database) |
| Cross-repo aggregation | Very hard | **Natural** |
| Cold start | pip install per job | **Always warm** |
| Infra to manage | None (ADO handles it) | Container host |
| Deployment flexibility | Locked to CI platform | **Any Docker host** |

---

## High-Level Architecture

```
┌──────────────────┐      ┌──────────────────┐
│  Azure DevOps    │      │  GitHub           │
│  Service Hook    │      │  Webhook          │
└───────┬──────────┘      └───────┬───────────┘
        └────────────┬────────────┘
                     │  HTTPS POST
                     ▼
┌─────────────────────────────────────────────────────────┐
│  PR GUARDIAN SERVICE (containerized)                     │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  WEBHOOK RECEIVER + PLATFORM ADAPTER               │  │
│  │  ADO adapter / GitHub adapter → PlatformPR         │  │
│  └───────────────────────┬───────────────────────────┘  │
│                          │                               │
│  ┌───────────────────────▼───────────────────────────┐  │
│  │  REVIEW ORCHESTRATOR                               │  │
│  │  0. Discovery (diff, languages, config, hotspots)  │  │
│  │  1. Mechanical gates  2. Triage                    │  │
│  │  3. AI agents         4. Decision engine           │  │
│  │  5. Post results      6. Log feedback              │  │
│  └───────────────────────┬───────────────────────────┘  │
│                          │                               │
│  ┌───────────────────────▼───────────────────────────┐  │
│  │  PLATFORM ACTIONS (writes back)                    │  │
│  │  ADO: POST /pr/threads, /pr/reviewers, /pr/labels  │  │
│  │  GitHub: POST /pulls/comments, /pulls/reviews      │  │
│  │  (No merge — author merges manually)               │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  Note: all orchestration is in-process (asyncio).     │  │
│  No external message queues. core/queue.py is a thin  │  │
│  asyncio task manager with cancellation support.      │  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  PERSISTENT LAYER                                  │  │
│  │  Feedback store, metrics, override tracking,       │  │
│  │  hotspot cache, config cache, health snapshots     │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  DASHBOARD + API                                   │  │
│  │  /dashboard, /repos, /feedback, /api/health,       │  │
│  │  /api/config, /api/webhooks                        │  │
│  └───────────────────────────────────────────────────┘  │
└────────┬──────────────────────────────┬─────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────┐            ┌─────────────────────┐
│  LLM Provider   │            │  PostgreSQL          │
│  (configurable) │            │  reviews, findings,  │
│                 │            │  feedback, metrics,  │
│  - Anthropic    │            │  hotspots, configs,  │
│  - Azure OpenAI │            │  health_snaps,       │
│  - Ollama/vLLM  │            │  overrides           │
└─────────────────┘            └─────────────────────┘
```

---

## Platform Adapter Pattern

```
                    ┌─────────────────────────┐
                    │   PlatformAdapter       │
                    │   (Protocol/Interface)   │
                    │                         │
                    │   fetch_diff(pr) → Diff  │
                    │   post_comment(pr, msg)  │
                    │   approve_pr(pr)         │
                    │   add_label(pr, label)   │
                    │   get_work_item(pr)      │  ← ADO work items + GitHub Issues
                    │   list_reviewers(pr)     │
                    │   get_ci_status(pr)      │  ← external check results
                    └────────┬────────────────┘
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
           ┌──────────────┐  ┌──────────────┐
           │ ADO Adapter  │  │ GitHub       │
           │ ADO REST API │  │ Adapter      │
           │ PAT or OAuth │  │ GitHub API   │
           │              │  │ App token    │
           └──────────────┘  └──────────────┘
```

### Webhook Payload Normalization

Both platforms normalize to:

```python
PlatformPR(
    platform="ado" | "github",
    pr_id="123" | "456",
    repo="api-service",
    source_branch="feature/add-login",
    target_branch="develop",
    author="alice",
    # ... plus platform-specific metadata for API callbacks
)
```

---

## Mechanical Checks: In-Process

In the service model, mechanical checks run **inside the service container**,
not in CI pipelines:

```
Service container image includes:
├── Python 3.12 + pr-guardian package
├── semgrep (pip install)
├── gitleaks (binary)
├── sqlfluff (pip install)
├── hadolint (binary)
├── oasdiff (binary)
├── squawk (binary)
├── shellcheck (binary)
└── Node.js + dependency-cruiser (for JS/TS repos)
```

Language-specific tools that need the full project (e.g., `dotnet build`,
`tsc --noEmit`) **still run in the repo's own CI pipeline**. PR Guardian runs
alongside the build pipeline, not replacing it.

**Image size**: Multi-stage Docker build. Final image ~800MB-1.2GB due to
bundled tools. Use `slim` variants where available. Azure Container App
scale-to-zero is fine — cold start is ~3-5s (container pull is cached after
first deploy).

---

## Code Access: Shallow Clone

```
Option A: Shallow clone (recommended)
  git clone --depth=1 --branch=<source> <repo-url> /tmp/review-<pr-id>
  git fetch --depth=1 origin <target>
  git diff origin/<target>..HEAD
  # Clean up after review
```

Disk space is cheap, clone takes ~5s for most repos, and agents need full file
context anyway. Use a temp directory per review, clean up after.

---

## Deployment Profiles

| Profile | When to use | LLM | Database | Infra templates |
|---------|-------------|-----|----------|-----------------|
| **Cloud (Azure)** | Default | SaaS (Anthropic) or Azure AI Foundry | Azure DB for PostgreSQL | `infra/azure/` |
| **Hybrid** | Code can't leave tenant | Azure AI Foundry (your tenant) | Azure DB for PostgreSQL | `infra/azure/` |
| **On-prem** | Code can't leave building | Local models (Ollama/vLLM) | PostgreSQL on local server | `infra/docker-compose/` or `infra/k8s/` |

All three profiles run the **same Docker image**. Only env vars differ.

### Cloud: Azure Container App

```yaml
# infra/azure/container-app.bicep
resource: containerApp
  name: pr-guardian
  properties:
    configuration:
      ingress:
        external: true
        targetPort: 8000
    template:
      containers:
        - name: pr-guardian
          image: prguardian.azurecr.io/pr-guardian:latest
          resources:
            cpu: 1.0
            memory: 2Gi
      scale:
        minReplicas: 0          # scale to zero when idle
        maxReplicas: 5          # scale up during PR spikes
        rules:
          - name: http-scaling
            http:
              metadata:
                concurrentRequests: 3
```

### On-Prem: Docker Compose

```yaml
# infra/docker-compose/docker-compose.yml
services:
  pr-guardian:
    image: pr-guardian:latest
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://guardian:${DB_PASSWORD}@db:5432/prguardian
      # Provider credentials — providers are defined in config/defaults.yml
      # Only set env vars for providers you've registered
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}          # if using anthropic provider
      - AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}  # if using azure-foundry provider
      - AZURE_OPENAI_KEY=${AZURE_OPENAI_KEY}
    depends_on: [db]

  db:
    image: postgres:16
    volumes: [pgdata:/var/lib/postgresql/data]
```

Notes for on-prem:
- Requires reverse proxy (nginx/caddy) for HTTPS termination
- LLM GPU server must be reachable from Guardian host
- Webhook URL must be reachable from ADO/GitHub

---

## LLM Provider Abstraction

### Why Thin, Not LiteLLM

We only need 3 providers. A ~100 line abstraction with 3 implementations is
cleaner than pulling in LiteLLM's 50+ packages.

```
┌──────────────────────────────────┐
│         Agent Code               │
│  agent.review(context) →         │
│    llm.complete(system, user,    │
│      response_format=json)       │
└──────────────┬───────────────────┘
               │
     ┌─────────┼─────────┐
     ▼         ▼         ▼
┌─────────┐┌─────────┐┌──────────┐
│Anthropic││ OpenAI- ││  Azure   │
│ Client  ││ Compat  ││  AI      │
│         ││(Ollama, ││ Foundry  │
│ Claude  ││ vLLM)   ││ GPT-4o   │
└─────────┘└─────────┘└──────────┘
```

### Provider Configuration

Two layers:
1. **Service-level** — `config/defaults.yml` declares which providers exist and
   which is the default
2. **Per-repo** — `review.yml` selects a provider by name and optionally
   overrides models per agent

#### Service-Level: Provider Registry

The Guardian admin declares **all available providers** at deploy time. Each
provider gets a short name. Guardian validates credentials at startup.

```yaml
# config/defaults.yml
llm:
  default_provider: anthropic         # which provider repos get if they don't specify

  providers:
    anthropic:
      type: anthropic
      api_key_env: ANTHROPIC_API_KEY
      default_model: claude-sonnet-4-6
      models:                          # models available through this provider
        - claude-opus-4-6
        - claude-sonnet-4-6
        - claude-haiku-4-5

    azure-foundry:
      type: azure-openai
      endpoint_env: AZURE_OPENAI_ENDPOINT
      api_key_env: AZURE_OPENAI_KEY
      default_model: gpt-4o
      models:
        - gpt-4o
        - gpt-4o-mini

    local-ollama:
      type: openai-compatible
      base_url: http://gpu-server.internal:11434/v1
      api_key: not-needed
      default_model: llama3.3:70b
      models:
        - llama3.3:70b
        - qwen2.5-coder:32b

  # Shared settings (apply to all providers unless overridden)
  max_tokens: 4096
  temperature: 0.1
  timeout_seconds: 120

  # Per-agent model overrides (apply to the default provider)
  agent_overrides:
    security_privacy:
      model: claude-opus-4-6
    code_quality_observability:
      model: claude-haiku-4-5
```

Startup validates: env vars resolve, endpoints respond to a health probe.
Missing or unhealthy providers are logged as warnings and excluded from the
available set. If the `default_provider` is unhealthy, startup fails.

#### Per-Repo: Provider Selection

Repos reference a provider **by name** — they never specify raw URLs or keys.

```yaml
# review.yml — repo just picks a provider and optionally tweaks models
llm:
  provider: azure-foundry             # must match a name in service config
  agent_overrides:
    security_privacy:
      model: gpt-4o                   # must be in that provider's models list
```

If a repo references an unknown or unhealthy provider, Guardian falls back to
`default_provider` and logs a warning.

#### Resolution Order

```
1. Load service config  → provider registry + defaults
2. Load repo review.yml → provider name + agent overrides
3. Resolve:
   provider  = repo.llm.provider  ?? service.llm.default_provider
   model     = repo.agent_overrides[agent].model
              ?? service.agent_overrides[agent].model
              ?? provider.default_model
4. Validate: provider exists, model is in provider.models[]
```

---

## Authentication

### Azure DevOps
- **Webhook → Service**: Service Hook with basic auth or shared secret
- **Service → ADO API**: PAT or OAuth App (Code Read, PR Contribute, Work Items)

### GitHub
- **Webhook → Service**: HMAC signature verification
- **Service → GitHub API**: GitHub App (recommended) or PAT (Pull Requests R/W, Contents Read)

---

## Webhook Setup

### Azure DevOps
```
Project Settings → Service Hooks → Create Subscription
  Event: Pull request created / updated
  URL: <GUARDIAN_URL>/api/webhooks/ado
```

### GitHub
```
Repo Settings → Webhooks → Add webhook
  URL: <GUARDIAN_URL>/api/webhooks/github
  Events: Pull requests

OR (better): Create a GitHub App with fine-grained permissions
```

---

## Network Topology

```
┌─ Container Host ─────────────────────────────────────┐
│  pr-guardian service (FastAPI)                        │
│     │                                                │
│     ├── Inbound HTTPS (webhooks from ADO + GitHub)   │
│     ├── Outbound: clone repos (HTTPS + auth)         │
│     ├── Outbound: LLM providers (per-repo config)    │
│     ├── Outbound: platform APIs (post results back)  │
│     └── Outbound: database (PostgreSQL)              │
└──────────────────────────────────────────────────────┘
```

---

## Request Flow

```
1. Webhook arrives (POST /api/webhooks/ado or /github)
2. Deduplicate: hash(repo + pr_id + head_commit_sha) → skip if already seen
3. Platform adapter normalizes → PlatformPR
4. Concurrency check: if review in-flight for same PR → cancel it
5. Queue review job (in-process asyncio queue, return 200 immediately)
6. Background worker:
   ├─ Clone/fetch PR diff (shallow clone)
   ├─ Load repo config
   ├─ Detect languages
   ├─ Run mechanical checks (in-process subprocesses)
   ├─ Triage → risk tier + agent selection
   ├─ Run AI agents (async, parallel)
   ├─ Decision engine → auto-approve | escalate | block
   ├─ Platform adapter executes action (comment, approve, label)
   └─ Log to database (feedback, metrics, cost)

Total time: 1-3 minutes (mostly LLM latency)
```

---

## Webhook Deduplication & Concurrency

ADO and GitHub may send duplicate or rapid-fire webhooks (retries, multiple
events per push). Guardian handles this at two levels:

**Deduplication**: Each webhook is keyed by `(repo, pr_id, head_commit_sha)`.
If the same key is already processed or in-flight, the duplicate is dropped
with 200 OK (idempotent).

**Concurrency (same-PR updates)**: If a new push arrives for a PR that's
currently being reviewed, Guardian cancels the in-flight review and starts
fresh. The newest commit is the only one that matters.

```python
# In-process tracking (no external state needed)
active_reviews: dict[str, asyncio.Task] = {}  # key: "repo:pr_id"

async def enqueue_review(pr: PlatformPR, commit_sha: str):
    key = f"{pr.repo}:{pr.pr_id}"

    # Cancel in-flight review for same PR
    if key in active_reviews and not active_reviews[key].done():
        active_reviews[key].cancel()

    task = asyncio.create_task(run_review(pr))
    active_reviews[key] = task
```

---

## CI Integration

Guardian runs **alongside** CI, not after it. Both are independent required PR
status checks. Guardian does not duplicate or consume results from CI-owned
checks — build, tests, and SonarCloud are handled by the pipeline before
Guardian runs.

```
CI-owned checks (Guardian does NOT run or poll these):
  Build, unit tests, integration tests, tsc --noEmit, dotnet build, SonarCloud
  → These are required PR status checks managed by CI pipelines
  → Guardian runs in parallel as a separate PR status check

Guardian's mechanical checks (in-process):
  semgrep, gitleaks, pii-scanner, api-contracts, migration-safety, etc.
```
