# PR Guardian — Overview

> Humans should only review what machines can't decide.

PR Guardian is an automated PR review and merge pipeline. It provides a safety
net of mechanical checks + AI agent review so that low-risk, high-confidence PRs
get auto-approved without waiting for a human. Authors still click merge.

## The Problem

- Non-devs and AI agents are shipping code
- Devs are the review bottleneck — every PR waits in the queue
- Human review is inconsistent (Friday afternoon rubber stamps)
- Security/architecture expertise is locked in individual heads

## The Solution

A 4-stage pipeline that classifies, analyzes, and decides on every PR:

```
  PR Created / Updated
         │
         ▼
  ┌─────────────────────────────────────────────────────┐
  │  STAGE 0: DISCOVERY                    (<5s)        │
  │                                                     │
  │  Parse diff, detect languages, load repo config,    │
  │  load hotspots, build security surface map           │
  │                                                     │
  │  Output: ReviewContext (consumed by all stages)      │
  └───────────────────────┬─────────────────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────┐
  │  STAGE 1: MECHANICAL GATES           (<2 min)       │
  │                                                     │
  │  Language-conditional: Semgrep, Gitleaks,            │
  │  dep-cruiser, migration safety, API contract         │
  │  checks, PII scanner, fitness tests                  │
  │                                                     │
  │  Hard fail → block PR, no agents needed             │
  └───────────────────────┬─────────────────────────────┘
                          │ all pass
                          ▼
  ┌─────────────────────────────────────────────────────┐
  │  STAGE 2: TRIAGE               (deterministic)      │
  │                                                     │
  │  Classify: trivial | low | medium | high            │
  │  Based on WHAT changed (not line count)             │
  │  + blast radius + repo config path weights          │
  │  Select which agents to run (save $)                │
  └───────────────────────┬─────────────────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────┐
  │  STAGE 3: AI AGENT REVIEW        (parallel)         │
  │                                                     │
  │  ┌──────────┐ ┌──────────┐ ┌───────────────┐       │
  │  │ Security │ │  Perf    │ │ Architecture  │       │
  │  │ +Privacy │ │          │ │ +Intent       │       │
  │  └────┬─────┘ └────┬─────┘ └──────┬────────┘       │
  │  ┌────┴─────┐ ┌────┴─────┐ ┌──────┴────────┐       │
  │  │ Code     │ │ Hotspot  │ │ Test Quality  │       │
  │  │ Quality  │ │          │ │               │       │
  │  └────┬─────┘ └────┬─────┘ └──────┬────────┘       │
  │       └─────────────┼──────────────┘                │
  │                     ▼                               │
  │  Each returns: verdict + findings + certainty       │
  └───────────────────────┬─────────────────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────┐
  │  STAGE 4: DECISION ENGINE        (deterministic)    │
  │                                                     │
  │  Rules-based scoring, certainty validation          │
  │                                                     │
  │        ┌──────────┬──────────────┐                  │
  │        ▼          ▼              ▼                  │
  │   Auto-Approve  Human Review  Hard Block            │
  │   (vote + comment) (tag + brief) (block merge)      │
  │                                                     │
  │   Author merges.  Guardian NEVER auto-merges.       │
  └─────────────────────────────────────────────────────┘
```

## Key Design Decisions

```
  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │  Auto-approve ≠ auto-merge                                     │
  │  Guardian votes approve + posts summary. Author clicks merge.  │
  │                                                                │
  │  Certainty = enum, not float                                   │
  │  "detected" / "suspected" / "uncertain"                        │
  │  Decision engine validates claims against evidence.            │
  │  Agents can't say "detected" without showing their work.       │
  │                                                                │
  │  Repo risk class = static per-repo                             │
  │  standard → auto-approve allowed                               │
  │  elevated → auto-approve only for trivial                      │
  │  critical → never auto-approve                                 │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘
```

## Service Architecture

PR Guardian runs as a hosted service (not in CI pipelines). Zero pipeline
agents consumed. Accepts webhooks from ADO and GitHub. Same Docker image
runs on cloud, on-prem, or Kubernetes.

```
  ┌──────────────┐              ┌──────────────┐
  │ Azure DevOps │              │    GitHub     │
  │  (webhook)   │              │  (webhook)    │
  └──────┬───────┘              └──────┬────────┘
         │                             │
         └──────────┬──────────────────┘
                    │  HTTPS POST
                    ▼
  ┌──────────────────────────────────────────────┐
  │         PR GUARDIAN SERVICE                   │
  │                                              │
  │  Webhook ──→ Platform Adapter ──→ Normalize  │
  │                     │                        │
  │              Review Orchestrator             │
  │    ┌────────────────┼────────────────┐       │
  │    │ Mechanical     │ Triage         │       │
  │    │ (in-process)   │ (deterministic)│       │
  │    └────────────────┼────────────────┘       │
  │                     │                        │
  │              AI Agents (parallel)            │
  │                     │                        │
  │              Decision Engine                 │
  │                     │                        │
  │    ┌────────────────┼────────────────┐       │
  │    │ Post comment   │ Vote approve   │       │
  │    │ (via platform  │ or tag human   │       │
  │    │  adapter)      │ reviewer       │       │
  │    └────────────────┼────────────────┘       │
  │                     │                        │
  │              Log to Database                 │
  └──────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
  ┌──────────────┐    ┌──────────────┐
  │ LLM Provider │    │  PostgreSQL  │
  │ (per-repo    │    │  (feedback,  │
  │  config)     │    │   metrics)   │
  └──────────────┘    └──────────────┘
```

## Deployment Flexibility

```
  ┌─────────────┬─────────────────────┬───────────────────┐
  │   Profile   │   LLM               │   Hosting         │
  ├─────────────┼─────────────────────┼───────────────────┤
  │   Cloud     │ SaaS (Anthropic)    │ Azure Container   │
  │             │ or Azure AI Foundry │ App               │
  ├─────────────┼─────────────────────┼───────────────────┤
  │   Hybrid    │ Azure AI Foundry    │ Azure Container   │
  │             │ (your tenant)       │ App               │
  ├─────────────┼─────────────────────┼───────────────────┤
  │   On-prem   │ Ollama / vLLM       │ Docker Compose    │
  │             │ (local GPU)         │ or Kubernetes     │
  └─────────────┴─────────────────────┴───────────────────┘

  Same Docker image for all profiles.
  Only env vars differ.
```

## Expected Impact

```
  Before Guardian:              After Guardian:
  ┌───────────────────┐         ┌───────────────────┐
  │ 100% of PRs       │         │ ~35% of PRs       │
  │ wait for human     │   →    │ need human review  │
  │ review             │         │                   │
  │                   │         │ ~65% auto-approved │
  │ Avg wait: 1-3 days│         │ in <5 minutes      │
  └───────────────────┘         └───────────────────┘

  Human review time saved: ~70-80%
  Cost: ~$60-95/month (100 PRs/week, cloud + SaaS LLM)
  ROI: ~100x (vs senior dev review hours saved)
```

## Documents in This Series

| Doc | Contents |
|-----|----------|
| **00-overview** (this) | High-level summary with diagrams |
| **01-philosophy** | Philosophy, pipeline flow, complementary systems overview |
| **01b-discovery** | Stage 0: diff parsing, language detection, config loading, ReviewContext |
| **02-mechanical-gates** | Stage 1: all deterministic checks in detail |
| **03-triage** | Stage 2: risk classification, hotspots, security surface |
| **04-ai-agents** | Stage 3: all 6 agent specifications |
| **05-decision-engine** | Stage 4: certainty validation, scoring, decision matrix |
| **06-multi-language** | Per-language tooling, prompt composition, cross-language concerns |
| **07-architecture** | Service design, hosting, platform adapters, LLM abstraction |
| **08-implementation** | Package structure, CLI, data flow |
| **09-operations** | Rollout, metrics, cost, feedback loop, health dashboard |
| **10-assessment** | Honest numbers, open questions, component priorities |

Backup of the full consolidated document: `brain/pr-guardian-design.md`
