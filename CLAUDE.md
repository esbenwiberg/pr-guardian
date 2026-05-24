# PR Guardian — agent guide

Automated PR review pipeline. Auto-approves low-risk PRs, escalates the rest to
a human, hard-blocks the dangerous ones. Hosted service — not a CI step.

## Commands

| What | Command |
|---|---|
| Install dev env | `pip install -e ".[dev]"` |
| Run tests | `python -m pytest` |
| Lint | `ruff check .` |
| Format | `ruff format .` |
| Typecheck | `mypy src` |
| Dep audit | `pip-audit --strict` |
| Refresh lockfile | `uv lock` |
| Build dashboard CSS (one-shot) | `npm run build:css` |
| Watch dashboard CSS | `npm run dev:css` |
| Start app locally | `bash scripts/agent-serve.sh` |
| Seed demo data | `python scripts/dev_seed.py` (auto-runs when Postgres is up) |
| Apply DB migrations | `alembic upgrade head` |
| New DB migration | `alembic revision -m "msg" --autogenerate` |
| Health check | `GET /api/health` |

Pre-commit hooks (gitleaks, ruff, large-file checks) live in
`.pre-commit-config.yaml`. Install with `pre-commit install` once per clone.

## Layout

```
src/pr_guardian/
  api/            FastAPI routers — webhooks, dashboard, agent IO, scans
  core/           Orchestrator — wires discovery → mechanical → triage → agents → decision
  discovery/      Diff parsing, language detection, repo config, security surface
  mechanical/     Deterministic gates: semgrep, gitleaks, dep checks, PII scanner
  triage/         Risk classifier — picks which agents run
  agents/         The 6 AI specialist reviewers (see Architecture)
  decision/       Weighted scoring + certainty validation → APR/REV/BLK verdict
  llm/            Provider wrappers (Anthropic, OpenAI), prompt rendering
  models/         Pydantic models for context, findings, output, config
  persistence/    SQLAlchemy storage layer (async, Postgres or in-memory)
  platform/       GitHub + Azure DevOps adapters
  config/         YAML defaults, schema validation
  dashboard/      Jinja templates + Tailwind-built static assets
  auth/           Admin session + API key handling
  wizard/         Capability clustering for review wizard
  cli.py          `pr-guardian` entry point
  main.py         FastAPI app factory
prompts/          Per-agent system + user prompt templates (one dir per agent)
specs/            Feature specs — read these before changing behavior
plans/            One-shot rollout plans (often consumed after merge)
docs/decisions/   Architecture Decision Records (ADRs)
alembic/          DB migrations (numbered 001_… upward)
tests/            Pytest suite, 500+ tests, asyncio_mode = auto
scripts/          Dev/ops helpers — agent-serve.sh, deploy-app.sh, dev_seed.py
infra/            Deployment manifests (compose, etc.)
```

## How a review actually flows

```
PR webhook → discovery → mechanical gates (parallel)
                       → triage (classify risk)
                       → agents (parallel, only those triage picked)
                       → decision engine → POST verdict + comments
```

Each agent gets the same `ReviewContext`, returns an `AgentResult` with
`verdict + findings + certainty`. Agents must show evidence — they cannot claim
`detected` without a citation. See `docs/decisions/` for the rules.

## Conventions

- **Python 3.12.** Line length 99 (ruff). asyncio for IO.
- **Pydantic v2** models — boundary types live in `models/`.
- **No `any` / loose typing.** Prefer `unknown` + refinement; document
  unavoidable `# type: ignore` comments.
- **Prompts live in `prompts/<agent>/`** as templates. Don't inline LLM
  prompts in Python — render them.
- **Migrations are append-only and numbered.** Never edit a merged
  migration; add a new one.
- **Commits follow Conventional Commits** (`feat: …`, `fix: …`, `chore: …`).
  Commit-msg hook is recommended.
- **PR Guardian never auto-merges.** Authors still click merge.

## Key runtime notes

- App listens on `$PORT` (default 8000). Use `bash scripts/agent-serve.sh`
  for the seeded local loop.
- **Postgres is optional.** Without it the app boots in degraded "no-DB" mode
  and many features become read-only.
- `GUARDIAN_DEV_ADMIN=1` bypasses admin auth — dev only.
- At least one of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` must be set for agent
  calls. Mechanical gates work without either.
- `GITHUB_TOKEN` + `GITHUB_WEBHOOK_SECRET` enable platform mode; ADO is the
  alternative (`ADO_PAT`, `ADO_ORG_URL`).

## Where to read first

- `README.md` — product framing and pipeline overview.
- `ARCHITECTURE.md` — system shape, invariants, decision boundaries.
- `docs/decisions/` — ADRs explaining *why* the codebase looks the way it does.
- `specs/<feature>/` — current and recent feature specs, the design source of truth.

## Don'ts

- Don't add fallbacks or "happy path" guards for impossible inputs at internal
  boundaries — validate only at system edges (webhook, API, LLM responses).
- Don't expand `core/` or `platform/` as junk drawers — find or name a concept.
- Don't bypass the certainty system — agents that fudge certainty undermine the
  decision engine.
- Don't ship a finding without an evidence anchor (file + line + quote).
