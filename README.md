```
    ╔═══════════════════════════════════════════════════════════════════╗
    ║                                                                   ║
    ║     ██████╗ ██████╗                                               ║
    ║     ██╔══██╗██╔══██╗                                              ║
    ║     ██████╔╝██████╔╝                                              ║
    ║     ██╔═══╝ ██╔══██╗                                              ║
    ║     ██║     ██║  ██║                                              ║
    ║     ╚═╝     ╚═╝  ╚═╝                                              ║
    ║                                                                   ║
    ║      ██████╗ ██╗   ██╗ █████╗ ██████╗ ██████╗ ██╗ █████╗ ███╗   ██╗║
    ║     ██╔════╝ ██║   ██║██╔══██╗██╔══██╗██╔══██╗██║██╔══██╗████╗  ██║║
    ║     ██║  ███╗██║   ██║███████║██████╔╝██║  ██║██║███████║██╔██╗ ██║║
    ║     ██║   ██║██║   ██║██╔══██║██╔══██╗██║  ██║██║██╔══██║██║╚██╗██║║
    ║     ╚██████╔╝╚██████╔╝██║  ██║██║  ██║██████╔╝██║██║  ██║██║ ╚████║║
    ║      ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝║
    ║                                                                   ║
    ║        "Humans should only review what machines can't decide"     ║
    ║                                                                   ║
    ║     ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  ║
    ║     │DISCOVERY │───>│  GATES   │───>│ TRIAGE   │───>│ AI AGENTS│  ║
    ║     │  parse   │    │ semgrep  │    │ classify │    │ 6 expert │  ║
    ║     │  detect  │    │ gitleaks │    │ risk     │    │ reviewers│  ║
    ║     └──────────┘    └──────────┘    └──────────┘    └─────┬────┘  ║
    ║                                                           │       ║
    ║                      ┌────────────────────────────────────┘       ║
    ║                      ▼                                            ║
    ║               ┌─────────────┐                                     ║
    ║               │  DECISION   │                                     ║
    ║               │   ENGINE    │                                     ║
    ║               ├─────┬───┬───┤                                     ║
    ║               │ APR │REV│BLK│                                     ║
    ║               └─────┴───┴───┘                                     ║
    ║                                                                   ║
    ╚═══════════════════════════════════════════════════════════════════╝
```

**Automated PR review pipeline that auto-approves low-risk PRs and escalates the rest.**

PR Guardian runs as a hosted service — not in your CI pipeline. It receives webhooks from GitHub or Azure DevOps, runs mechanical checks + AI agent review in parallel, and posts a verdict. Authors still click merge. Guardian never auto-merges.

## Architecture

```
PR Created ──> Discovery ──> Mechanical Gates ──> Triage ──> AI Agents ──> Decision
   (<5s)         (<2 min)      (deterministic)   (parallel)     (rules-based)
```

| Stage | What happens |
|---|---|
| **Discovery** | Parse diff, detect languages, load repo config, build security surface map |
| **Mechanical Gates** | Semgrep, Gitleaks, dependency checks, migration safety, PII scanner — hard fail = block |
| **Triage** | Classify risk: `trivial` / `low` / `medium` / `high` — select which agents to run |
| **AI Agents** | 6 specialist reviewers run in parallel (see below) |
| **Decision Engine** | Weighted scoring + certainty validation → auto-approve, request human review, or hard block |

## AI Review Agents

| Agent | Focus |
|---|---|
| Security & Privacy | Vulnerabilities, auth issues, data exposure, PII handling |
| Performance | N+1 queries, unbounded loops, missing indexes, resource leaks |
| Architecture & Intent | Design patterns, coupling, PR intent vs actual changes |
| Code Quality & Observability | Readability, logging, error handling, dead code |
| Hotspot | Files with high churn / bug history — extra scrutiny |
| Test Quality | Coverage gaps, flaky patterns, missing edge cases |

Each agent returns a **verdict + findings + certainty level** (`detected` / `suspected` / `uncertain`). Agents must show their work — they can't claim "detected" without evidence.

## Setup

```bash
pip install -e ".[dev]"
```

## Running locally

For the fastest development loop, run the app directly. The helper starts
Uvicorn in the foreground, attempts to start local Postgres when `pg_isready`
is available, seeds demo data when a DB is reachable, and otherwise boots in
degraded no-DB mode.

```bash
pip install -e ".[dev]"
bash scripts/agent-serve.sh
curl http://localhost:8000/api/health
```

Dashboard pages are served from `http://localhost:8000/dashboard`.

For a persistent local stack with Postgres:

```bash
cp infra/docker-compose/.env.example infra/docker-compose/.env
docker compose -f infra/docker-compose/docker-compose.yml up
```

### Environment Variables

```bash
# LLM provider (at least one required)
export ANTHROPIC_API_KEY=sk-ant-...

# GitHub webhook signature verification
export GITHUB_WEBHOOK_SECRET=your-secret

# Azure DevOps (alternative platform)
# export ADO_PAT=...
# export ADO_ORG_URL=https://dev.azure.com/yourorg
```

> **GitHub integration**: Guardian authenticates to GitHub through a **GitHub App
> Connection**, not a `GITHUB_TOKEN`. App credentials are stored encrypted in the
> Guardian database via the `/profiles` → GitHub Apps UI. See
> [docs/github-app-setup.md](docs/github-app-setup.md) for setup instructions.

## Usage

```bash
# Start the webhook server
pr-guardian serve

# Review a PR locally (for testing)
pr-guardian review --repo owner/repo --pr 42

# Validate config
pr-guardian config check
```

## Build

The project builds with the standard PEP 517 toolchain — no custom build system.

```bash
pip install -e ".[dev]"        # editable install for development
python -m build --sdist --wheel  # produce dist/*.tar.gz and dist/*.whl
```

`pip install -e ".[dev]"` is enough for the test and lint workflow; `python -m build` is only needed when cutting a release artifact. CI runs both on every push.

## Test

```bash
python -m pytest -q            # full suite (~3s, ~500 tests)
python -m pytest tests/test_orchestrator.py -k webhook   # a slice
ruff check . && ruff format --check .   # lint + format
mypy src                       # type check
npm run check:js               # dashboard/browser JS syntax
npm run smoke:reviews-scan-preview      # browser smoke for scan preview form
bash scripts/repofit-check.sh --include executed   # agent-fitness audit
```

The suite is hermetic — no network, no docker, no Postgres required. Tests that exercise persistence use the in-memory `aiosqlite` driver. CI runs lint → typecheck → tests → build → audit on every PR; see `.github/workflows/ci.yml`.

## Deployment

PR Guardian ships as a single Docker image that runs anywhere:

| Profile | LLM | Hosting |
|---|---|---|
| **Cloud** | Anthropic API / Azure AI Foundry | Azure Container App |
| **Hybrid** | Azure AI Foundry (your tenant) | Azure Container App |
| **On-prem** | Ollama / vLLM (local GPU) | Docker Compose / K8s |

### Docker Compose (quickstart)

```bash
cp infra/docker-compose/.env.example infra/docker-compose/.env
# Edit .env with your keys
docker compose -f infra/docker-compose/docker-compose.yml up
```

### Azure

```bash
# Deploy full infra (Container App + PostgreSQL + Key Vault + ACR)
cd infra/azure && bash deploy.sh
```

## Configuration

Drop a `review.yml` in your repo root to customize behavior:

```yaml
repo_risk_class: standard          # standard | elevated | critical

auto_approve:
  enabled: true
  allowed_target_branches:
    - develop
    - "feature/*"
  blocked_target_branches:
    - "release/*"

weights:                           # tune agent influence on final score
  security_privacy: 3.0
  test_quality: 2.5
  architecture_intent: 2.0
  performance: 1.5
  hotspot: 1.5
  code_quality_observability: 1.0

thresholds:
  auto_approve_max_score: 4.0
  human_review_min_score: 4.0
  hard_block_score: 8.0
```

### Repo Risk Classes

| Class | Behavior |
|---|---|
| `standard` | Auto-approve allowed for low-risk PRs |
| `elevated` | Auto-approve only for trivial changes |
| `critical` | Never auto-approve — always requires human review |

## Decision Flow

```
  All agents report    Weighted score     Decision
  ┌───────────┐       ┌────────────┐     ┌─────────────────┐
  │ verdicts  │──────>│  ≤ 4.0     │────>│  Auto-Approve   │
  │ findings  │       │  4.0 - 8.0 │────>│  Human Review   │
  │ certainty │       │  ≥ 8.0     │────>│  Hard Block     │
  └───────────┘       └────────────┘     └─────────────────┘

  Auto-approve = vote approve + post summary comment.
  Author clicks merge. Guardian NEVER auto-merges.
```

## Project Structure

```
src/pr_guardian/
├── agents/          # 6 AI review agents + prompt composition
├── api/             # FastAPI webhooks + health endpoint
├── config/          # YAML config loading + schema
├── core/            # Orchestrator + async queue
├── decision/        # Scoring engine + action dispatch
├── discovery/       # Diff parsing, blast radius, file roles
├── languages/       # Language detection + per-language tooling
├── llm/             # LLM abstraction (Anthropic, Azure, OpenAI-compat)
├── mechanical/      # Semgrep, Gitleaks, PII, migration safety
├── models/          # Pydantic domain models
├── persistence/     # PostgreSQL via SQLAlchemy async
├── platform/        # GitHub + Azure DevOps adapters
└── triage/          # Risk classification + hotspot detection
```

## Contributing

Branch from `main`, keep PRs focused, and use Conventional Commits (`feat:`, `fix:`, `docs:`, …) so the changelog stays mechanical. The full conventions live in `CONTRIBUTING.md`; the project layout, layer boundaries, and invariants live in `ARCHITECTURE.md`. Run `python -m pytest -q && mypy src && ruff check .` before opening a PR — pre-push hooks enforce the same on `git push`.

## Supported Platforms

- **GitHub** — webhook integration via `pull_request` events
- **Azure DevOps** — webhook integration via service hooks

## LLM Providers

- **Anthropic** — Claude models (default: `claude-sonnet-4-6`)
- **Azure AI Foundry** — hosted in your Azure tenant
- **OpenAI-compatible** — Ollama, vLLM, or any OpenAI-compatible API

## License

Private — all rights reserved.
