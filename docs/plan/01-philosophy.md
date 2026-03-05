# PR Guardian — Philosophy & Pipeline

## Philosophy

> Humans should only review what machines can't decide.

Non-devs are shipping code. Devs are the bottleneck. PR Guardian provides an
automated safety net: mechanical checks + AI agent review. For low-risk,
high-confidence PRs, Guardian auto-approves — the author still clicks merge.
Humans only see what agents escalate.

**Realistic target**: Humans review ~30-40% of PRs instead of 100%. For the PRs
they do review, they spend half the time because agents pre-filter and highlight
specific concerns. That gives humans back ~70-80% of their review time.

**Key design decisions**:
- **Auto-approve, not auto-merge** — Guardian votes approve + posts summary. Author merges.
- **Certainty enums, not confidence floats** — Agents classify findings as `detected` / `suspected` / `uncertain` with structured evidence. The decision engine validates claims against evidence — agents can't say "detected" without showing their work.
- **Repo risk class** — Static per-repo classification (`standard` / `elevated` / `critical`) controls how aggressively Guardian can auto-approve.

---

## Architecture Overview

```
PR Created / Updated
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 1: MECHANICAL GATES (deterministic, fast, <2min)  │
│                                                          │
│  Existing:          New:                    Gap fills:    │
│  ├─ Build           ├─ Semgrep (SAST)       ├─ API break │
│  ├─ Tests           ├─ Gitleaks (secrets)   │  change    │
│                     ├─ dep-cruiser (arch)   ├─ Migration │
│                     ├─ Socket/Snyk (SCA)    │  safety    │
│                     ├─ bundle-size/limit     ├─ Observ.  │
│                     ├─ fitness tests        │  fitness   │
│                     └─ lang-specific tools   └─ PII scan │
│                                                          │
│  ANY HARD FAIL → Block PR, no agents needed              │
└──────────────────────┬───────────────────────────────────┘
                       │ all pass
                       ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 2: TRIAGE (classify PR before spending agent $)   │
│                                                          │
│  Inputs:                                                 │
│  ├─ Diff size, files touched, languages detected         │
│  ├─ Hotspot score, security surface, arch surface        │
│  ├─ Author context, linked work item                     │
│                                                          │
│  Output: risk_tier = trivial | low | medium | high       │
│          agent_set = which agents to run                  │
│          language_map = languages present in diff         │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 3: AI AGENT REVIEW (parallel, specialized)        │
│                                                          │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │  SECURITY   │ │ PERFORMANCE │ │  ARCHITECTURE     │  │
│  │  + PRIVACY  │ │ AGENT       │ │  + INTENT VERIFY  │  │
│  └──────┬──────┘ └──────┬──────┘ └────────┬──────────┘  │
│  ┌──────┴──────┐ ┌──────┴──────┐ ┌────────┴─────────┐  │
│  │  CODE       │ │  HOTSPOT    │ │  TEST QUALITY     │  │
│  │  QUALITY    │ │  AGENT      │ │  AGENT            │  │
│  │  + OBSERVE  │ │             │ │                   │  │
│  └──────┬──────┘ └──────┬──────┘ └────────┬──────────┘  │
│         └───────────────┼─────────────────┘              │
│                         ▼                                │
│  Each agent returns: verdict, findings, certainty,       │
│  evidence_basis, languages_reviewed                      │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 4: DECISION ENGINE (deterministic rules)          │
│                                                          │
│         ┌────────┬────────────┐                          │
│         ▼        ▼            ▼                          │
│    Auto-Approve  Human Review  Hard Block                │
│                                                          │
│  Note: auto-approve = vote approve + comment.            │
│  Author still clicks merge. No auto-merge.               │
└──────────────────────────────────────────────────────────┘
```

---

## Complementary Systems (Non-Per-PR)

These run alongside the per-PR pipeline on different schedules:

```
═══════════════════════════════════════════════════════════
  COMPLEMENTARY SYSTEMS (not per-PR, runs alongside)
═══════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────┐
│  CODEBASE HEALTH DASHBOARD (scheduled: weekly/monthly)   │
│                                                          │
│  ├─ Complexity trends over time                          │
│  ├─ Test coverage trends                                 │
│  ├─ Dependency freshness                                 │
│  ├─ Hotspot evolution                                    │
│  ├─ Architecture boundary violations trend               │
│  ├─ Duplication trends                                   │
│  └─ Mutation testing scores (scheduled, not per-PR)      │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  FEEDBACK LOOP (continuous)                              │
│                                                          │
│  ├─ Log every PR decision (agent verdicts + outcome)     │
│  ├─ Track human overrides                                │
│  ├─ Weekly disagreement analysis                         │
│  ├─ Per-repo false positive tracking                     │
│  ├─ Prompt refinement pipeline                           │
│  └─ Threshold auto-tuning recommendations                │
└──────────────────────────────────────────────────────────┘
```

See [09-operations.md](09-operations.md) for full details on complementary systems.
