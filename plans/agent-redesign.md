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

## Prompt-Level Changes (all specialist agents)

Goal: push precision upstream into the specialist prompt so the validator
critic has less noise to clean up. All eight changes are conservative —
none of them increase recall.

| Change | Why | Source |
|---|---|---|
| **Diff-header citation is a hard rule.** Every finding must cite a file path that appears as a header in the diff. Drop findings without one. | Machine-checkable scope rule. Eliminates whole categories of hallucinated findings before the validator runs. | autopod |
| **Context lines are read-only.** Lines without a `+` prefix may only inform context, never serve as the basis for a finding. | Currently enforced post-hoc by the validator. Pushing it upstream into each specialist's base prompt reduces validator load. | autopod |
| **JSON-only output, no markdown fences.** | Already in `validator/base.md`. The six specialists don't enforce output format — adding it hardens parsing. | autopod |
| **"You only see the diff" disclaimer.** Explicit instruction that the agent is reviewing diff hunks, not the full codebase — so don't flag "this function is undefined" / "this duplicates existing code" / "import is missing" type findings when the referent could exist outside the diff window. | Counters a common LLM failure mode where the agent treats the diff as the whole world. Qodo PR-Agent uses this verbatim. | Gap 1, prompt-engineering-patterns |
| **Verbatim quote per finding.** Each finding must include a `quote` field containing the exact diff line(s) it's about. | Anti-hallucination anchor. If the model can't quote the offending line, the finding is likely fabricated. Complementary to the diff-header rule (which scopes to file; quote scopes to line). | Gap 4, prompt-engineering-patterns |
| **"Would the author actually fix this?" self-check.** Before emitting a finding, the agent considers whether a reasonable author would change their code in response. If the answer is "probably not — it's a preference/nitpick/style," skip it. | Cheap internal filter that catches preferenced-as-bug findings before they ship. | Gap 5, prompt-engineering-patterns |
| **"Empty findings list is acceptable" — stated explicitly.** Tell the model directly that returning `[]` is the correct answer when nothing real is wrong. | Counters the LLM's bias toward generating output even when none is warranted. One line, high impact. | Gap 7, prompt-engineering-patterns |
| **"Provably affected" rule.** Findings must demonstrate causal impact on the diff's behavior, not theoretical risk. A finding like "this could potentially be slow" is dismissed; "this loop calls the API once per item in `items` (line 42) — N+1" is kept. | Forces evidence-based reasoning over speculation. Pairs naturally with the verbatim-quote rule. | Gap 8, prompt-engineering-patterns |

Sources: autopod's `pre-submit-review.ts` and `review-agentic-runner.ts`
reviewer prompts (rows 1–3); `plans/prompt-engineering-patterns.md` survey
(rows 4–8). Findings 2, 3, 6, 9, 10 from the prompt-engineering survey
were considered and deferred or rejected — see that file for rationale.

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

## Reviewer Tool Access Scope

Today: all six specialists are diff-only. No repo read, no tool use. The
verifier-framed agents (`intent`, `architecture`) need *some* read access
to do their job — referenced spec files for `intent`, anchor docs for
`architecture`.

| Agent | Tool access |
|---|---|
| `intent` | **Scoped read** — referenced spec files in `specs/` (and similar) |
| `architecture` | **Scoped read** — anchor docs discovered via the algorithm in `architecture-anchor-discovery.md` |
| `security`, `performance`, `code_quality_observability`, `test_quality`, `hotspot` | **Diff-only.** No change. |

Hard rule that survives even with tool access: **findings stay scoped to
files that appear as headers in the diff.** Tools verify the diff against
context; they never produce findings about unrelated code. Same constraint
autopod enforces on its agentic reviewer.

---

## Research

Completed:

- `plans/architecture-anchor-discovery.md` — precedence-ordered discovery
  spec for the `architecture` agent's anchor files, including weighting,
  "enough anchor" thresholds, cheapest-first discovery algorithm, and
  monorepo edge cases. Real-world validation against
  kubernetes/next.js/nx/dotnet-eShop/rust/Django.
- `plans/prompt-engineering-patterns.md` — survey of Anthropic, OpenAI
  Codex, Qodo PR-Agent, Anthropic's `claude-code`, and LLM-as-judge
  guidance. 10 ranked gaps; 5 adopted (rows 4–8 of the Prompt-Level
  Changes table above), 5 deferred or rejected.
- `plans/ona-review-patterns.md` — supplement covering how Ona (Gitpod
  rebrand) approaches review, and what's distinctive vs. the leaders
  already surveyed.

---

## Deferred

- **Generalize the verifier-with-discovered-anchor pattern to other agents.**
  Security ← threat model. Tests ← testing conventions doc. Performance ← perf
  budget doc. Same mechanism, different anchor source. Nail `intent` and
  `architecture` first; revisit once they're stable.
- **Uncertainty-triggered tool escalation.** When a specialist's certainty
  is low, run a second pass with read-only repo tools to verify or
  withdraw the finding. Conceptually appealing but architecturally heavy
  (agentic loop, cost, latency, eval complexity). Defer until the scoped-
  read pattern on `intent`/`architecture` is proven.
- **Coverage-at-source / precision-at-validator inversion.** Anthropic's
  Code Review Harnesses doc recommends specialists fish broadly and let
  the validator critic prune. Guardian today does the opposite —
  specialists self-restrain. Plausibly higher recall but raises noise
  risk; deferred given prior bad experience with noisy findings.
- **Few-shot examples in specialist prompts.** Effective but maintenance
  cost is real. Revisit if specific failure modes recur after the other
  prompt-level changes land.

---

## Open Questions

- Does the `intent` agent's "absence is the finding" rule need a length /
  file-count threshold, or is the triage classifier's `medium`/`high` line
  sufficient on its own?
- Should the `architecture` agent's skip-with-status surface in the dashboard
  as a distinct UI state, or just disappear from the verdict?
- For repos that span multiple ecosystems (e.g. a TypeScript frontend + .NET
  backend in one repo), should anchor discovery be path-scoped?
