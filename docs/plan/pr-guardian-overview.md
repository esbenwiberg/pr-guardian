# PR Guardian — Technical Overview

**Automated PR review pipeline that auto-approves low-risk PRs and escalates the rest to humans.**

> "Humans should only review what machines can't decide."

---

## How It Works

Every PR flows through four stages. Each stage is faster and cheaper than the next — most PRs exit early.

```
 PR Created (webhook)
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 1: MECHANICAL GATES  (<2 min, deterministic)                  │
│                                                                      │
│  semgrep ─── gitleaks ─── dependency audit ─── architecture rules    │
│  API contracts ─── migration safety ─── PII scanner ─── SonarCloud  │
│                                                                      │
│  Hard fail? ──── YES ───► BLOCK PR (no further stages run)           │
└──────────┬───────────────────────────────────────────────────────────┘
           │ pass
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 2: TRIAGE  (deterministic classification)                     │
│                                                                      │
│  Inputs: diff size, files touched, languages, module count,          │
│          security surface, repo risk class, hotspot map              │
│                                                                      │
│  ┌──────────┬──────────┬──────────┬──────────┐                       │
│  │ TRIVIAL  │   LOW    │  MEDIUM  │   HIGH   │                       │
│  │ docs,cfg │ <50 LOC  │ 50-300   │ >300 LOC │                       │
│  │ comments │ 1 module │ 1-5 mod  │ >5 mod   │                       │
│  └────┬─────┴────┬─────┴────┬─────┴────┬─────┘                       │
│       │          │          │          │                              │
│       │ skip     │ 1 agent  │ 3-4      │ all 6                       │
│       ▼          ▼          ▼          ▼                              │
└──────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 3: AI AGENT REVIEW  (parallel, LLM-powered)                   │
│                                                                      │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐                     │
│   │  Security  │  │ Perf &     │  │ Arch &     │                     │
│   │  & Privacy │  │ Resources  │  │ Intent     │                     │
│   │  (wt: 3.0) │  │ (wt: 1.5) │  │ (wt: 2.0) │                     │
│   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘                     │
│   ┌─────┴──────┐  ┌─────┴──────┐  ┌─────┴──────┐                     │
│   │   Code     │  │   Test     │  │  Hotspot   │                     │
│   │  Quality   │  │  Quality   │  │  Focus     │                     │
│   │  (wt: 1.0) │  │  (wt: 2.5) │  │ (wt: 1.5) │                     │
│   └─────┬──────┘  └─────┬──────┘  └─────┴──────┘                     │
│         │               │                │                           │
│         └───────────────┴────────────────┘                           │
│                         │                                            │
│              Structured findings:                                    │
│              severity  = low | medium | high | critical              │
│              certainty = detected | suspected | uncertain            │
│              + evidence (CWE, pattern, cross-refs)                   │
└──────────┬───────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STAGE 4: DECISION ENGINE  (deterministic rules, no AI)              │
│                                                                      │
│  1. Validate certainty claims (downgrade if evidence insufficient)   │
│  2. Score: severity × certainty_weight, weighted by agent            │
│  3. Apply decision matrix:                                           │
│                                                                      │
│        Score < 4.0 ──────► AUTO-APPROVE eligible                     │
│        Score 4.0–8.0 ────► HUMAN REVIEW (tag reviewers)              │
│        Score ≥ 8.0 ──────► HARD BLOCK                                │
│                                                                      │
│  Override triggers (always escalate):                                │
│   • Any "detected" finding ≥ medium severity                        │
│   • ≥ 3 "suspected" findings    • New external dependency           │
│   • Intent mismatch with work item  • Author's first PR             │
└──────────────────────────────────────────────────────────────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
  APPROVE      ESCALATE
  (vote +      (tag reviewers,
   comment)     add label,
     │          full report)
     ▼            ▼
  Author       Human
  merges       reviews
```

---

## Service Architecture

PR Guardian runs as a **hosted container service** — not inside CI pipelines. It consumes zero pipeline agents and supports multiple platforms with the same image.

```
  Azure DevOps          GitHub
       │                   │
       │  webhook          │  webhook
       ▼                   ▼
  ┌─────────────────────────────────┐
  │        WEBHOOK RECEIVER         │
  │  ┌───────────┐ ┌─────────────┐  │
  │  │ADO Adapter│ │GitHub Adapter│  │
  │  └─────┬─────┘ └──────┬──────┘  │
  │        └───────┬───────┘         │
  │                ▼                 │
  │         PlatformPR (common)      │
  │                │                 │
  │     ┌──────────▼──────────┐      │
  │     │  REVIEW ORCHESTRATOR│      │   ┌─────────────┐
  │     │                     │◄─────┼──►│ LLM Provider│
  │     │  Stage 1 → 2 → 3 → 4      │   │             │
  │     │                     │      │   │ • Anthropic  │
  │     └──────────┬──────────┘      │   │ • Azure AI   │
  │                │                 │   │ • Ollama     │
  │     ┌──────────▼──────────┐      │   └─────────────┘
  │     │   FEEDBACK LOOP     │      │
  │     │  log → analyze →    │      │   ┌─────────────┐
  │     │  tune thresholds    │◄─────┼──►│ PostgreSQL  │
  │     └─────────────────────┘      │   │ findings,   │
  │                                  │   │ feedback,   │
  │     ┌─────────────────────┐      │   │ hotspots,   │
  │     │     DASHBOARD       │      │   │ metrics     │
  │     │  trends, metrics,   │      │   └─────────────┘
  │     │  false-pos rates    │      │
  │     └─────────────────────┘      │
  └──────────────────────────────────┘

  Same Docker image everywhere:
  ┌────────────────────────────────────────────┐
  │  Cloud          Hybrid          On-prem    │
  │  Azure CA +     Azure CA +      Docker +   │
  │  Anthropic API  Azure AI        Ollama     │
  └────────────────────────────────────────────┘
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| **Auto-approve, never auto-merge** | Author always clicks merge. Guardian is a safety net, not a gatekeeper. |
| **Certainty enums, not confidence floats** | `detected` / `suspected` / `uncertain` with structured evidence. Decision engine validates claims — agents can't bluff. |
| **Repo risk classes** (standard / elevated / critical) | Static per-repo knob controls how aggressive auto-approve is. Critical repos never auto-approve. |
| **Service-hosted, not CI-native** | Zero pipeline agents consumed. Multi-platform. Persistent feedback loop. Always warm. |
| **Language-composable prompts** | Agent prompts assembled at runtime from `base + Σ(language sections)`. Adding a language = adding a prompt file, not rewriting agents. |

## Expected Impact

| Metric | Target |
|--------|--------|
| PRs auto-approved | ~65% (standard repos) |
| Human review effort saved | 70–80% |
| Time to auto-approve | <5 minutes |
| False negative rate | <2% |
| Cost (100 PRs/week) | ~$60–95/month |
