# Greenfield Kickoff — next-gen autonomous review & release

> **Status: FOUNDING DRAFT.** This is the seed doc for a **new repository**,
> starting fresh from PR Guardian's lessons — not a fork, not "inspiration," a
> deliberate rebuild. Move this file into the new repo when it's scaffolded.
>
> Companion: `TIERED_AUTONOMOUS_REVIEW_DESIGN.md` (the product thinking / depth
> ladder). This doc is the **engineering charter**: what we carry, what we
> rebuild, what we kill, and the founding decisions.
>
> Owner: ewi. Started 2026-07-15. Placeholder product name: **`<TBD>`** (naming
> is an open decision — see §10).

---

## 0. TL;DR

We're building a **tiered autonomous review + release** system. Guardian's
crown jewels (certainty/evidence discipline, adversarial validation, IO-free
decision engine, escalation & readiness state machines) survive **as behavior**.
Its orchestration, persistence, and migration layers — where every prod scar
lived — get rebuilt clean. New tech stack only where it earns it; **Python stays
for the review brain.**

---

## 1. What we're building — and the axiom we're inverting

**The pitch:** a code-review system designed for *no human review per PR*. Fast
gate on every PR, deep review-and-fix nightly, and a **release gate** where a
human signs off only when the system (or policy) says it matters.

**The founding axiom inversion — this is why it's a new repo:**
Guardian's own ARCHITECTURE.md closes with *"The whole project rests on humans
staying in the loop for non-trivial PRs."* That assumption is baked into its
trust tiers, escalation, human-gate agent, and "advisory verdict" invariant. The
new product **negates that premise**: humans are out of the per-PR loop by
design, present only at release, only when flagged. You cannot cleanly bolt an
inverted axiom onto a codebase built on the original — hence, fresh.

**Evidence it's the right bet** (the 30-day audit): 89% of PRs merged with zero
human comment; 22 net-new bugs (8 HIGH) shipped past humans *and* bots; all in
frontend PCF/TS repos (.NET clean). Human per-PR review is already a rubber
stamp. See `TIERED_AUTONOMOUS_REVIEW_DESIGN.md §1`.

---

## 2. The tiered architecture (target shape)

Three tiers, **three different jobs, deployed separately** (different
resource/latency/cost profiles — do NOT co-host like Guardian did):

| Tier | Job | Depth | Deployable |
|---|---|---|---|
| **1 · Fast gate** | every PR, on the critical path | mechanical gates + subset of agents + `resolve_decision`; no persistence required | thin, CI-runnable (proven feasible by Guardian's `review-local` + `fake` provider) |
| **2 · Deep review + fix** | nightly, keep `main` healthy | full agent set + adversarial validator per PR + **new fix-generation stage** → fix PRs | batch service, its own scaling |
| **3 · Release gate** | per release cut, decide to ship | fat check over `last-release..HEAD` + rollup → ship / **sign-off** / block | gated deploy integration (`deployment_protection_rule`) |

Fix = **confidence-gated PR, never a commit to `main`**. Release gate
**re-runs** the deep check over the window (never trusts last night). Sign-off =
**AI escalation + non-negotiable policy floor** (auth/payments/migrations always
need a human). The wizard is repurposed to walk a human through a release.
Rationale + open forks in the companion doc §3–§4.

### 2.1 Tier-1 agent — the **poison check** · **DECIDED**

The fast gate is a **poison detector, not a quality reviewer.** Its only job:
stop what is *irreversible or exploitable if it merges*, because nothing else
runs until tonight's deep pass. The test is **not** "is this high severity?" —
it's **"if this sits in `main` for a day, is the damage done and undoable?"**
That splits *poison* (block) from *blemish* (stay silent, nightly owns it).

Scope = **two families only:**

- **A · Semantic security (high/critical).** Layered *on top of* the mechanical
  semgrep/gitleaks tier — the agent catches what patterns can't: authz/authn
  bypass (removed ownership check, endpoint made public), injection with real
  reachability (SQLi/command/path/SSRF), data/PII exposure (into responses or
  logs), crypto misuse (disabled TLS verify, hardcoded key, weak algo),
  user-triggerable DoS (unbounded alloc/loop/catastrophic regex on attacker
  input).
- **B · Irreversible harm.** Not security, but worse than a bug — you can't get
  the data back. This is what the audit's HIGH findings actually were: **silent
  data loss/corruption** (Time #14317 "rewrites stored hours on blur",
  PowerGantt #14407 "lookup change lost, no error"), **destructive/unguarded
  operations** (unconditional deletes; delete depending on residual temp state),
  **dangerous migrations** (drop/rename/narrow on a populated column; a backfill
  that loses precision).

Discriminator between A/B and "wait for nightly": **silent + irreversible → gate
now; loud + recoverable → nightly.** A wrong-but-visible calc waits; one that
silently corrupts stored money does not.

**Precision discipline (opposite of the deep pass — precision-first, ruthless):**
1. **Blocks only on `detected` certainty.** Suspected/uncertain don't block the
   fast lane — they flow to nightly. Reuse the evidence-gated certainty system.
2. **No REV verdict — binary BLOCK or ALLOW.** Falls straight out of the axiom
   inversion: no human in the per-PR loop, so "escalate to a human" has nowhere
   to go. Veto power only; the gate never "approves" (quality-vouch is at
   release).
3. **One adversarial refute pass before any block.** Fires only on a block
   *candidate* (rare) → near-zero latency cost, stops false vetoes. If refuted,
   the change passes and nightly still scrutinizes it.

Effect: the gate almost never speaks; when it does, it's right and it's serious.
That's what earns trust in a gate with no human behind it. Name it for the job:
`poison_check` / `merge_blocker` — not "reviewer."

### 2.2 Release visual-QA stage · **DECIDED (v1)**

For apps that can be driven visually (web apps, etc.), a stage of the Tier-3
release run: an agent spins up the release build, drives it via **Playwright
MCP**, exercises the **flows touched by the release**, and produces a
**before/after screenshot + behavior report** the human signs off on. Catches
the bug class static review can't — the one that only appears when you *run* the
thing (blank chart, modal won't open, button moved behind the header). Second
win: it makes the human sign-off *cheap and concrete* (8 annotated screenshots
vs. reading 34 PRs) — which is the whole point of keeping the human rare.

Decided design:
- **Evidence stage, not an autonomous hard gate.** [DECIDED #2] Output = a
  report feeding the sign-off. Clearly-broken deltas **trigger** mandatory
  sign-off (plugs into the D4 escalation trigger); it **never** hard-blocks on a
  flaky visual judgment.
- **Before/after, not absolute judgment.** Drive the *same* change-scoped flows
  on `last-release` and `HEAD`, diff visually + behaviorally, report deltas.
  "Rendered yesterday, blank today" is reliable; "is blank" is a coin flip. This
  single move kills most false alarms.
- **Harness-first for PCF.** [DECIDED #1] Start with the **PCF local test
  harness** (`npm start`) — a browser-hosted single control with mock context,
  drivable by Playwright now, no standing infra. This is most of the value at a
  fraction of the cost and matches how the controls are actually built. Full
  app-flow QA in a **provisioned Dataverse env** (integration bugs, real data)
  is a later escalation, not v1.
- **Change→flow mapping** leans on the wizard's capability clusterer (crown
  jewel): changed components → affected flows to exercise.
- **Flakiness → deterministic seed/mock data + retries + before/after.** A false
  "broken" screenshot erodes the sign-off as fast as a false block.
- **Conditional & config-gated per profile** — off for backend/library repos, on
  for visual ones.

---

## 3. CARRY FORWARD — the crown jewels (preserve as behavior)

Non-negotiable. These are the product; re-code them, don't re-derive them blind.

1. **Certainty system with evidence-gated downgrade.** Agents *claim* certainty;
   the decision engine *recomputes* it from an evidence-signal count and
   auto-downgrades (`detected`→`suspected` with <2 signals, etc.). Stops LLM
   confidence theater from driving verdicts. Enforced **both** in the agent
   prompt *and* structurally on output. (Guardian: `decision/engine.py`
   `validated_certainty()`.)
2. **Adversarial generator–critic validator.** A separate LLM pass sees all
   findings + diff and votes keep/dismiss/downgrade, with cross-agent
   line-proximity dedup. The false-positive suppressor (the 91%-cull that made
   the audit trustworthy). Can use a cheaper model. Tier 1 may skip it for
   latency; Tier 2 always runs it.
3. **IO-free weighted decision engine as a pure function.** Per-agent weights +
   risk×repo matrix + thresholds → verdict. One `resolve_decision` shared by
   first-review and re-review so they *can't diverge*. Unit-testable in ms.
4. **Structural vs finding-derived escalation split.** "Why is a human needed?"
   has two disjoint answers: structural (touched a hotspot / new dep / risky
   path) vs transient findings. Lets you say "only finding-derived reasons
   remain, and they're fixed → auto-clear." (Guardian ADR-002.)
5. **Gate-agent routing, made the default.** A dedicated agent judges the
   *nature* of a change and is **blind to other agents' findings** (so certainty
   can't leak in), **fail-closed** on error. Guardian's `structural_only` mode
   proved findings-noise, not danger, was gating humans — this is the core
   routing logic for an autonomous product. (ADR-011.)
6. **Config-gated authority — no path-glob "safety nets."** The system won't
   auto-act on a repo it hasn't been told how to judge (real topology or
   explicit rules only). Guardian's built-in path globs *masqueraded as safety
   and broke the product* (ADR-012). Never reintroduce language-agnostic path
   heuristics.
7. **Clearance ≠ platform approval.** Opt-in to *run* and permission to *click
   approve* are separate switches. Maps perfectly onto the tiers (gate comments
   → deep fixes → release approves w/ sign-off). (ADR-009.)
8. **Readiness as a durable state-machine record.** Reviews don't fire straight
   off a webhook — a durable candidate waits out CI/security/topology, survives
   deploys, and is recovered by a reconciler for missed/out-of-order webhooks.
   Earned its keep in prod. Any webhook-driven system needs this. (ADR-008.)
9. **Finding lifecycle + fix-by-inference, with restraint.**
   `open→dismissed|fixed|verified`; fixed/regressed inferred by strict signature
   set-diff; `verified` is terminal (audit permanence). Deliberately no fuzzy
   rename matching. Keep the restraint. (ADR-003/004) — **but** decouple the
   signature from the agent's display name (see §4.4).
10. **Deterministic mechanical gates as a distinct fast tier.** semgrep,
    gitleaks, dep-audit, PII, migration-safety. Cheap, deterministic, never
    hallucinate. The hard floor.
11. **LLM provider abstraction with `fake` + `claude-cli` dev providers.**
    Enables offline, keyless, DB-less self-validation of a real review. Genuinely
    good — keep it day one.

---

## 4. REBUILD DIFFERENTLY — where Guardian bled

1. **No god orchestrator.** Guardian's `core/orchestrator.py` = 2,214 lines
   owning pipeline execution, re-review, dismissal re-decide, finalize, **and all
   platform side-effects** + token pricing + status mapping. Half the prod scars
   (finalize diverges on status, posts to stale SHA, base-merge carry-forward)
   live here. **Rebuild as explicit composable pipeline stages.**
2. **Side-effects are their own layer.** Extract verdict/status/comment posting
   out of orchestration entirely, behind the platform-adapter protocol. **Exactly
   one** "post final verdict + status to the *live* head" function that every
   path calls. This single refactor kills a cluster of Guardian's incidents.
3. **One pipeline, parameterized — not two.** Guardian's review and re-review are
   near-parallel reimplementations with two finalize paths; their divergence
   caused real incidents. One path, parameterized by mode.
4. **Stable agent IDs; signature ≠ display name.** Guardian baked the agent name
   into finding signatures (`file::category::agent`), so renaming/splitting an
   agent invalidates all historical lifecycle records — which is *why* ADR-006's
   agent split was never shipped. Use a stable agent `id` in the signature.
5. **No `core/` junk drawer.** Model explicit subsystems (pipeline, readiness,
   eventing, scans, chatops) as named modules. Guardian's cross-layer invariant
   #3 exists only to police an overloaded package.
6. **Tier-scoped config, not one god-object.** Guardian's `GuardianConfig` is
   ~30 nested models flat under one root. Scope config per tier: gate config ≠
   deep-review config ≠ release-gate config.
7. **Keep Guardian-owned policy, not repo-root config** (ADR-007). Review policy
   lives in the service (edited in-app), *not* a `review.yml` in the target repo —
   so a rogue agent committing to a repo can't rewrite its own review rules.

---

## 5. KILL LIST — patterns not to reintroduce

- **Dual schema management** (`create_all` + Alembic) and the **sqlite test
  dialect.** Guardian ran two schema systems, trusted neither; the sqlite test
  DB gave false confidence on the exact failure modes that hit prod. Pick ONE
  truth; **test migrations against Postgres.**
- **`String(n)` for anything touching LLM output.** A `varchar(64)` overflow
  silently truncated and **lost all findings on save** in prod — invisible to
  the whole test suite. **LLM-output columns are `Text`, full stop.** And a
  failed persist must **fail the run loudly, never fake-complete.**
- **Blind bot-author full-bypass** (`exempt_authors`). The code's own comment
  flags it as a supply-chain hole. For trusted automation, run mechanical gates
  only — never a blanket skip.
- **Built-in one-size path globs** as a safety net (killed by ADR-012 already).
- **Orchestrator-owned side-effects / pricing tables / status mapping.**
- **Proposed-but-unimplemented ADRs as live design** (Guardian ADR-005, ADR-006).
  Decide fresh; don't inherit half-built splits.

---

## 6. Invariants to enforce day one (CI-checked)

Guardian stayed clean (one TODO in 31k lines) because architectural rules broke
CI. Keep import-linter (or equivalent) and enforce:

1. **Mechanical/deterministic gates must not call the LLM layer.** (Essential —
   makes the fast tier trustworthy and cheap.)
2. **The decision layer is IO-free** — no api/persistence/platform/dashboard
   imports. (Highest-value invariant: makes the verdict a provable pure
   function.)
3. **Platform adapters isolated behind a protocol** (NEW — Guardian had the
   protocol but nothing linted the leak).
4. **Agents must not import the decision layer** (NEW — findings flow one way).
5. **Pipeline stages depend inward** (domain + decision), no `core`-style bucket.

---

## 7. Operational requirements — learned the hard way

If multi-replica (Tier 2/3 likely are), these are **day-1**, not afterthoughts:
- **Cross-replica eventing.** In-process event buses froze live progress under
  >1 replica; Guardian's fix was a Postgres LISTEN/NOTIFY bridge. Design the
  event fan-out cross-replica from the start (or use a real broker).
- **Single-runner leader election** for once-only background work (Guardian:
  Postgres advisory lock).
- **SSE/streaming needs heartbeats** — cloud ingress (Envoy/ACA) reaps idle
  connections (Guardian: 15s heartbeat).
- **Right-size memory for batch/boot workloads** (Guardian OOM-crash-looped on
  a broad boot sync at 2Gi).
- **Watch the DB connection ceiling on rolling deploys** (overlapping revisions
  exhausted `max_connections` → deadlock).
- **Degraded no-DB boot mode** was genuinely useful for dev/self-validation —
  keep the app runnable without a DB for the review path.

---

## 8. Tech stack — decisions & recommendations

| Layer | Guardian | Recommendation for new repo |
|---|---|---|
| Review-brain language | Python 3.12 | **KEEP.** The crown jewel lives here; the LLM/agent ecosystem is Python-first. Rewriting the brain in a new language = re-deriving the hardest part in an unfamiliar stack. Hard no. |
| API | FastAPI | Keep for the service API. |
| Boundary models | Pydantic v2 | Keep — clean. |
| DB access | SQLAlchemy async + asyncpg | **RECONSIDER.** The async-SQLAlchemy + Alembic + sqlite-test trio was the source of the two worst scars. Options: (a) same stack but test on Postgres only + Text-for-LLM rule; (b) a lighter data layer (e.g. asyncpg + SQL, or a migration-first tool where the migration *is* the diff). Decision needed — see §10. |
| Migrations | Alembic | **RECONSIDER** (fought the team hard, ADR-010). |
| DB | Postgres | Keep — LISTEN/NOTIFY + advisory locks were the multi-replica escape hatch. |
| LLM SDKs | anthropic + openai + provider abstraction | **KEEP the abstraction** incl. `fake`/`claude-cli`. Default to the latest Claude models (Opus 4.8 / Sonnet 5 / Haiku 4.5). |
| Dashboard | Jinja + Tailwind (Node for CSS) | **OPEN.** Couples a Node toolchain into a Python product for one job. Reconsider whether the new product needs server-rendered dashboard vs thin SPA on the API, or a much smaller UI (release sign-off + findings) than Guardian's. |
| Mechanical tools | gitleaks + semgrep baked in image | Keep, but the fast tier may want them as a sidecar rather than bloating the gate image. |

**Where a genuinely new stack could pay off:** the *service/infra & UI* layer
(not the brain) — if the tiered model wants, say, a job-queue-native runtime for
Tier 2 batch + fix. That's the place to experiment, and it's isolated from the
review core.

---

## 9. Sequencing — first steps

1. **Scaffold the repo** + name (§10). Set up CI with the §6 invariants from
   commit one.
2. **Lift the decision core first** — it's IO-free and lifts cleanly: certainty
   validation, weighted scoring, `resolve_decision`, evidence rules. Port with
   tests. This is the spine; everything hangs off it.
3. **Port the agents + prompts + provider abstraction** (incl. `fake` for
   deterministic tests). Prove a review runs offline via a `review-local`
   equivalent before any platform integration.
4. **Build Tier 1 (fast gate)** as a CI-runnable step. Ship it against
   `resource-planner` in **shadow mode** (advisory).
5. **Then Tier 2 (deep + fix)** as a separate batch service; **then Tier 3
   (release gate + sign-off + wizard)**, including the **visual-QA stage §2.2**
   (harness-first) as part of the tier-3 buildout — it depends on the release
   run existing, so it's sequenced with tier 3, but it's **v1 scope**, not a
   later add-on.
6. **Trust ratchet:** shadow → flip humans to release-only once the
   false-*negative* rate clears a defined bar (that bar is still an open
   question — companion doc §8).

---

## 10. Open decisions (need ewi's call)

- **O1 — Repo name / product name.** (`<TBD>`.)
- **O2 — Data layer.** Same async-SQLAlchemy+Alembic (with Postgres-only tests +
  Text rule) vs a lighter/migration-first stack. Biggest stack fork.
- **O3 — Mono-service vs multi-service from day one.** Lean: separate deployables
  per tier (different resource profiles), shared review-core library.
- **O4 — Dashboard/UI scope & stack.** Full dashboard vs minimal sign-off UI;
  Jinja vs SPA vs none.
- **O5 — Carried-over product forks** (from companion doc): F1 per-PR fast vs
  thin-only; F2 fix aggressiveness; F3 release cadence; F4 sign-off granularity.
- **O6 — What, if anything, of Guardian is *lifted verbatim* vs reimplemented.**
  (Decision core = strong lift candidate; orchestration/persistence = rebuild.)
- ~~**O7 — Release visual-QA stage**~~ **RESOLVED 2026-07-15:** in v1;
  harness-first for PCF (single-control `npm start` harness, not a provisioned
  Dataverse env for v1); advisory + escalation, never an autonomous hard-block.
  See §2.2.

---

## Appendix — Guardian ADR heritage map

| ADR | Verdict for new repo |
|---|---|
| 002 sticky-trigger split | **Honor** — structural vs finding escalation |
| 003 finding lifecycle | Honor (fix the table misnomer + signature/agent-name coupling) |
| 004 fix-by-inference | Honor the restraint (no fuzzy rename) |
| 005 final auto-approval gate | Unimplemented — decide fresh |
| 006 split verifier identity | Unimplemented — decide fresh (stable agent id) |
| 007 Guardian-owned profiles | **Honor** — policy in service, not repo |
| 008 readiness candidates | **Honor** — durable state machine |
| 009 clearance ≠ approval | **Honor** — maps onto the tiers |
| 010 squashed migration baseline | Cautionary tale — pick ONE schema truth |
| 011 structural-only escalation | **Honor & default** — the gate-agent routing |
| 012 config-gated auto-approve | **Honor** — no path-glob safety nets |

## Session log
- **2026-07-15** — Founding draft. Decision to greenfield (ewi's call, after
  trade-off discussion). Retrospective mined Guardian for keep/rebuild/kill.
  Established axiom inversion, tiered target, crown-jewel carry-forward, kill
  list, day-1 invariants, ops lessons, stack recommendations. Open decisions
  O1–O6 pending.
- **2026-07-15** — Tier-1 **poison check** agent spec DECIDED (§2.1): two
  families (semantic security high/crit + irreversible harm), silent-on-blemish,
  detected-only + no-REV + single-refute discipline. **Visual-QA stage** DECIDED
  for v1 (§2.2): harness-first for PCF, advisory+escalation not hard-block,
  before/after diffing. O7 resolved.
