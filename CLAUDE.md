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
| Build package | `python -m build` |
| Build dashboard CSS (one-shot) | `npm run build:css` |
| Watch dashboard CSS | `npm run dev:css` |
| Check dashboard JS syntax | `npm run check:js` |
| Browser smoke: reviews scan preview | `npm run smoke:reviews-scan-preview` |
| Start app locally | `bash scripts/agent-serve.sh` |
| Seed demo data | `python scripts/dev_seed.py` (requires a reachable DB) |
| Apply DB migrations | `alembic upgrade head` |
| New DB migration | `alembic revision -m "msg" --autogenerate` |
| Health check | `GET /api/health` |
| Agent-fitness audit | `bash scripts/repofit-check.sh --include executed` |

Pre-commit hooks (gitleaks, ruff, large-file checks) live in
`.pre-commit-config.yaml`. Install with `pre-commit install` once per clone.
Install the pre-push hook too when preparing release or quality work:
`pre-commit install --hook-type pre-push`.

## Layout

```
src/pr_guardian/
  api/            FastAPI routers — webhooks, dashboard, agent IO, scans
  core/           Orchestrator — wires discovery → mechanical → triage → agents → decision
  discovery/      Diff parsing, language detection, repo config, security surface
  mechanical/     Deterministic gates: semgrep, gitleaks, dep checks, PII scanner
  triage/         Risk classifier — picks which agents run
  agents/         Six PR-review specialist agents (see Architecture)
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
tests/            Pytest suite, asyncio_mode = auto
scripts/          Dev/ops helpers — agent-serve.sh, deploy-app.sh, dev_seed.py
infra/            Deployment manifests (compose, etc.)
.autopod/         Autopod handoff/runtime metadata; do not treat as product code
assets/           Reference images and design assets
prototypes/       Exploratory UI/code spikes, excluded from mypy
Dockerfile*       Runtime images for app and agent deployment
tailwind.config.js Dashboard CSS build config
REVIEW_INTERFACE_DESIGN.md Human-review interface design notes
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
  migration; add a new one. The chain was squashed to `001_baseline.py`
  (generated from the models, which are the schema source of truth — see
  ADR-010); new migrations layer on top from `002`.
- **Commits follow Conventional Commits** (`feat: …`, `fix: …`, `chore: …`).
  Commit-msg hook is recommended.
- **PR Guardian never auto-merges.** Authors still click merge.

## Key runtime notes

- App listens on `$PORT` (default 8000). Use `bash scripts/agent-serve.sh`
  for the local loop; it attempts to start local Postgres when `pg_isready`
  is available and otherwise falls back to no-DB mode.
- Local dashboard URL is `http://localhost:8000/dashboard` when the app is
  running on the default port.
- **Postgres is optional.** Without it the app boots in degraded "no-DB" mode
  and many features become read-only. For an explicit persistent loop, start
  Postgres separately or use Docker Compose, set the database URL, run
  `alembic upgrade head`, then run `python scripts/dev_seed.py` for seeded
  dashboard data.
- `GUARDIAN_DEV_ADMIN=1` bypasses admin auth — dev only.
- At least one of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` must be set for agent
  calls. Mechanical gates work without either.
- **Self-validation without keys/GitHub:** set `GUARDIAN_LLM_PROVIDER=fake`
  (deterministic; diff containing `GUARDIAN_E2E_FINDING` yields a finding) or
  `GUARDIAN_LLM_PROVIDER=claude-cli` (real LLM via the local `claude` CLI, no API
  key). Review a local checkout end-to-end with
  `pr-guardian review-local --repo-path . [--base <ref>]`. The `claude-cli`
  provider is **dev-only** — the factory refuses it when a real DB is configured
  (see `llm/claude_cli.py`). For reviewing real PRs/ranges/commits without keys,
  use `pr-guardian review-range` / `scan-recent --base` (still need platform
  auth for the diff). See `docs/ci-nightly-range-review.md`.
- **GitHub integration** uses a GitHub App Connection stored via the `/profiles`
  Connections UI. `GITHUB_WEBHOOK_SECRET` is required for webhook signature
  verification. Do **not** set `GITHUB_TOKEN` — Guardian no longer reads it for
  GitHub API calls. See `docs/github-app-setup.md` for full setup instructions.
- **ADO integration** uses `ADO_PAT` + `ADO_ORG_URL` from the environment.
- **Sandbox E2E**: `scripts/github-app-e2e.sh` is an opt-in harness that
  validates the full GitHub App review flow against
  `esbenwiberg/pr-guardian-e2e`. Run with `--check` to verify prerequisites.
  Do not use it against production repos.
- Node is used only for Tailwind CSS and browser smoke scripts. Run
  `npm install` if `node_modules/` is absent, then `npm run build:css` for a
  one-shot dashboard CSS rebuild.
- Browser smoke for the reviews scan preview is
  `node tests/browser/reviews_scan_preview.spec.mjs`.
- Run Repofit through `scripts/repofit-check.sh`; it activates `.venv` so the
  executed checks see the same tools as local development.

## Where to read first

- `README.md` — product framing and pipeline overview.
- `ARCHITECTURE.md` — system shape, invariants, decision boundaries.
- `docs/decisions/` — ADRs explaining *why* the codebase looks the way it does.
- `specs/<feature>/` — current and recent feature specs, the design source of truth.

## Layers & invariants

The codebase enforces three architectural rules via `import-linter` (see
`[tool.importlinter]` in `pyproject.toml`). Violations break CI.

1. **`mechanical/` must not import `llm/`.** Deterministic gates stay
   deterministic — no LLM calls from semgrep, gitleaks, dep checks, or PII
   scans. If a mechanical check needs nuance, escalate via triage, not LLM.
2. **`decision/` is IO-free.** The decision engine takes findings + config in,
   returns a verdict out. No database, no HTTP, no platform calls. This is
   what lets the scoring logic be tested in milliseconds and reasoned about
   independent of integration state.
3. **`core/` must not import `api/` or `dashboard/`.** The orchestrator drives
   the pipeline — surface layers depend on `core/`, not the other way around.
   If you find yourself wanting to call into `api/` from `core/`, the right
   move is to lift the shared concept up.

## Testing patterns

- **In-memory DB is the default for tests.** `aiosqlite` swaps in for
  Postgres via the same SQLAlchemy URL pattern. Don't mock the storage layer
  if an integration test can hit the real one.
- **Agents are tested against fixture `ReviewContext` objects** in
  `tests/fixtures/`. Add a new fixture when adding a new agent finding type.
- **Mechanical gates have golden-output tests.** When changing a gate,
  update the golden fixture rather than relaxing the assertion.
- **`asyncio_mode = auto`.** Test functions can be `async def` without a
  decorator. Use `pytest.mark.parametrize` over `for` loops in tests.

## Don'ts

- Don't add fallbacks or "happy path" guards for impossible inputs at internal
  boundaries — validate only at system edges (webhook, API, LLM responses).
- Don't expand `core/` or `platform/` as junk drawers — find or name a concept.
- Don't bypass the certainty system — agents that fudge certainty undermine the
  decision engine.
- Don't ship a finding without an evidence anchor (file + line + quote).
