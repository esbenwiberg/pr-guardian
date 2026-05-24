# Agent Redesign

> Tighten the six specialist prompts, split `architecture_intent` into two
> verifier-framed agents (`intent` + `architecture`), and make both new agents
> gracefully skip when they have no ground truth to anchor on.

## Motivation

Two problems with the current agent set:

1. **Prompt scope is soft.** Specialist prompts say "only report findings you
   can point to in the diff" but don't enforce it. The validator catches some
   of this post-hoc, but a hard upstream rule cuts noise earlier and cheaper.
2. **`architecture_intent` is two jobs.** It mixes auditor mode (free-floating
   "this looks like a layer violation") with verifier mode (does the diff match
   the work item). The verifier half is the high-value half; the auditor half
   is where the subjective noise lives.

Reframing both new agents as verifiers — each anchored to a discoverable ground
truth — gives sharper output and a graceful no-op when the ground truth is
missing, instead of confident nonsense.

---

## Prompt-Level Changes (all six specialist agents)

| Change | Why |
|---|---|
| **Diff-header citation is a hard rule.** Every finding must cite a file path that appears as a header in the diff. Drop findings without one. | Machine-checkable scope rule. Eliminates whole categories of hallucinated findings before the validator runs. |
| **Context lines are read-only.** Lines without a `+` prefix may only inform context, never serve as the basis for a finding. | Currently enforced post-hoc by the validator. Pushing it upstream into each specialist's base prompt reduces validator load. |
| **JSON-only output, no markdown fences.** | Already in `validator/base.md`. The six specialists don't enforce output format — adding it hardens parsing. |

Source: borrowed from autopod's `pre-submit-review.ts` and `review-agentic-runner.ts`
reviewer prompts.

---

## Split `architecture_intent` → `intent` + `architecture`

Same agent count after the split (6 → 7), sharper roles. Both are verifiers;
they differ only in what claim they verify and where they find it.

### New agent: `intent`

Anchored to the author's stated claim.

**Inputs:**
- PR title
- PR description / body
- Linked work item (GitHub issue, ADO work item)
- **Referenced spec files** — when the PR mentions `specs/foo/design.md`, the
  agent reads that file as part of its anchor

**Verifier framing:** "verify the diff does what's claimed — nothing more,
nothing less."

**Finding types:**
- Undisclosed scope (diff changes things the PR didn't claim to change)
- Incomplete scope (work item lists X/Y/Z; diff only does some)
- Hidden behavior change (no-op refactor that isn't, undisclosed API/schema shifts)
- Pattern shortcuts (PR claims a feature but bypasses the established way of doing it)

**Missing-anchor behavior** (hooked to existing triage classifier):

| Description quality | Diff triage | Agent behavior |
|---|---|---|
| Detailed + linked spec | any | Full verifier mode |
| Short title, no body | trivial / low | Skip silently |
| Short title, no body | medium / high | **Finding**: "undisclosed scope — N files changed without explanation" |
| Vague ("misc fixes") | medium / high | Devalue + flag scope opacity |
| Detailed body that contradicts the diff | any | Primary verifier signal |

The asymmetry vs. architecture: for `intent`, *absence of an anchor* is itself
sometimes the finding (when paired with non-trivial scope).

### Refocused agent: `architecture`

Anchored to the repo's stated architecture.

**Tiered behavior:**

| Ground truth available | Agent mode |
|---|---|
| ADRs, `AGENTS.md`, `CLAUDE.md`, `architecture.md`, etc. | Full verifier mode — flag deviations from written rules |
| Only sibling-file patterns visible | Narrow local-pattern check — "this file deviates from siblings in the same directory" |
| Nothing | **Skip.** Emit a status line in the verdict (not a finding): "no architecture context found — agent skipped" |

The point: an architecture agent without an anchor produces subjective noise.
Skipping is honest. Teams that want strong architecture review will write the
ADRs; teams that don't get a no-op. The agent's behavior teaches the team what
it needs.

---

## Research: Architecture Anchor Discovery

Before implementing the `architecture` agent's anchor-discovery step, catalogue
what to look for across ecosystems. Output should be a precedence-ordered
discovery spec.

**Cross-language conventions:**
- `docs/adr/` / `docs/architecture/decisions/` (ADRs)
- `ARCHITECTURE.md`
- `AGENTS.md`, `CLAUDE.md`
- `CONVENTIONS.md`, `docs/conventions/`
- `CONTRIBUTING.md` (architecture-relevant sections)
- `.cursorrules`, `.github/copilot-instructions.md`

**Ecosystem-specific:**
- **.NET**: `.sln` structure, `Directory.Build.props`, `.editorconfig`, layered folder conventions (Domain / Application / Infrastructure)
- **Python**: `pyproject.toml` layout, `src/` vs flat, package boundary conventions
- **Node / TypeScript**: workspaces (`package.json`, `nx.json`, `turbo.json`, `lerna.json`), `tsconfig` path mappings
- **JVM**: Maven / Gradle multi-module structure
- **Go**: `internal/`, `cmd/`, `pkg/` conventions
- **Rust**: workspace structure

**Architecture-as-code:**
- C4 model files, Structurizr DSL
- PlantUML, Mermaid diagrams in `docs/`
- arc42 templates, `.arch/` directories
- dependency-cruiser / depcruise configs

**Per-repo override:**
- `.pr-guardian.yml::architecture_docs` — explicit list of files the team wants
  the agent to treat as architectural ground truth

**Spec output should include:**
- Precedence order (which signals win when multiple are present)
- What each file type tells the agent (rules vs. conventions vs. examples)
- How strongly to weight each (ADR > AGENTS.md > sibling-file pattern)
- When the union of available signals counts as "enough anchor" vs. "skip"

---

## Deferred

- **Generalize the verifier-with-discovered-anchor pattern to other agents.**
  Security ← threat model. Tests ← testing conventions doc. Performance ← perf
  budget doc. Same mechanism, different anchor source. Nail `intent` and
  `architecture` first; revisit once they're stable.

---

## Open Questions

- Does the `intent` agent's "absence is the finding" rule need a length /
  file-count threshold, or is the triage classifier's `medium`/`high` line
  sufficient on its own?
- Should the `architecture` agent's skip-with-status surface in the dashboard
  as a distinct UI state, or just disappear from the verdict?
- For repos that span multiple ecosystems (e.g. a TypeScript frontend + .NET
  backend in one repo), should anchor discovery be path-scoped?
