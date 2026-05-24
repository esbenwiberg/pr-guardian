# Architecture

PR Guardian is a hosted review service. A PR webhook arrives, a pipeline runs,
a verdict is posted. This document is the load-bearing context an agent (or
new contributor) needs before changing behavior.

## Pipeline

```
webhook ─▶ discovery ─▶ mechanical gates ┐
                                         ├─▶ decision ─▶ verdict + comments
                       ─▶ triage ─▶ agents┘
```

Each stage is a clear layer with one job. The decision engine is the only
place verdicts are minted; everything else produces findings.

| Stage | Module | Responsibility |
|---|---|---|
| Webhook ingress | `api/` | Receive + auth GitHub / Azure DevOps payloads |
| Discovery | `discovery/` | Parse diff, detect languages, load repo config, build security surface |
| Mechanical gates | `mechanical/` | Deterministic checks: semgrep, gitleaks, dep risk, PII, migration safety |
| Triage | `triage/` | Classify risk (`trivial` / `low` / `medium` / `high`) and pick agents |
| Agents | `agents/` | 6 LLM specialists, parallel, each returns findings + certainty |
| Decision | `decision/` | Weighted scoring + certainty validation → `APR` / `REV` / `BLK` |
| Output | `api/`, `platform/` | Post comments / inline annotations / approvals back to the PR |

The orchestrator in `core/orchestrator.py` wires these together. It is the
single place that knows the pipeline order.

## The agents

| Agent | What it owns |
|---|---|
| `security_privacy` | Vulns, auth flaws, data exposure, PII handling |
| `performance` | N+1 queries, unbounded loops, missing indexes, leaks |
| `architecture_intent` | Design + coupling + PR intent vs actual changes |
| `code_quality_observability` | Readability, logging, error handling, dead code |
| `hotspot` | Files with high churn / bug history — extra scrutiny |
| `test_quality` | Coverage gaps, flaky patterns, missing edges |
| `scan_validator` | Validates mechanical scan findings (separate from review agents) |
| `validator` | Cross-checks other agents' certainty claims |

Prompts live in `prompts/<agent>/`. Code stubs live in `src/pr_guardian/agents/`.

## Invariants (do not break)

1. **Guardian never auto-merges.** Authors click merge. Verdict is advisory at
   worst, blocking at best.
2. **Findings need evidence.** A finding may only claim `detected` certainty if
   it includes a file/line/quote citation. `suspected` and `uncertain` are
   first-class — fudging is a bug.
3. **Mechanical gates are deterministic.** No LLM calls in `mechanical/`. If
   you need a model, you're in the wrong layer.
4. **The decision engine is the only verdict source.** Agents return findings;
   they do not decide. Mechanical hard-fails block, agent findings get scored.
5. **Migrations are append-only.** Never mutate a merged migration; add a new
   numbered one. State is reconstructed by replay.
6. **Postgres is optional.** The app must boot and run a review in degraded
   in-memory mode. Don't add a hard DB dependency to a code path that wasn't
   already DB-only.

## Boundaries — who may call whom

- `api/` may call `core/`, `persistence/`, `platform/`, `auth/`.
- `core/` may call any pipeline stage but never `api/` or `dashboard/`.
- `agents/` may call `llm/`, `models/`, `prompts/` (via the renderer).
  `agents/base.py` touches `persistence/` for prompt overrides; new agent
  code should otherwise route storage access through the orchestrator.
- `mechanical/` may not call `llm/`. (See invariant 3 — enforced by
  import-linter.)
- `decision/` reads `AgentResult` + `MechanicalResult`. It is pure logic over
  data — no IO.

Architecture fitness tests (import-linter) enforce the layers that can be
statically checked — see `[tool.importlinter]` in `pyproject.toml`.

## Storage shape

`persistence/storage.py` is the SQLAlchemy async layer. Reviews, findings,
dismissals, lifecycle, dashboards, scans, PATs, exclusion rules each have a
table. See `alembic/versions/` — migration numbers tell the history of how the
schema grew. Read the last 3 migrations before touching the schema.

## Frontend

The dashboard is server-rendered Jinja in `src/pr_guardian/dashboard/`. Tailwind
generates `static/styles.css` via `npm run build:css`. No SPA, no client-side
routing. Adding a "page" means: route in `api/`, template, link in nav.

## ADRs

Decisions with binding rationale live in `docs/decisions/`:

- ADR-001 — inline-comment-mode tristate
- ADR-002 — sticky-trigger split
- ADR-003 — finding-lifecycle state machine
- ADR-004 — fix-by-inference
- ADR-005 — final auto-approval gate
- ADR-006 — split verifier agent identity

Read the ADR before changing the area it covers. New decisions get a new ADR.

## What this codebase is not

- Not a CI runner. We don't replace pytest/build; we review.
- Not a static-analysis tool. Mechanical gates wrap existing scanners; the
  agents are the novel work.
- Not an autonomous merge bot. The whole project rests on humans staying in
  the loop for non-trivial PRs.
