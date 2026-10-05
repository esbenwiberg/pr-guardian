# Tiered Autonomous Review & Release Model — working design

> **Status: WORKING DRAFT / thinking in progress.** Not a spec, not approved.
> This is a living document capturing the exploration of moving PR Guardian's
> consumers away from per-PR human review toward a tiered model: fast per-PR
> gate → nightly deep review+fix → release gate with human sign-off *when it
> matters*. Decisions here are marked **DECIDED / LEAN / OPEN**. Update as we go.
>
> Owner: ewi. Started 2026-07-14.

---

## 1. Why (the thesis)

We want to stop relying on per-PR human review — even for large/heavy PRs —
because **it isn't a safety net; it's a rubber stamp.** This is evidence-backed,
not vibes.

### The 30-day audit (7 repos, 159 merged PRs)
- **89%** of PRs merged with **zero** genuine human review comment.
- Human review is *already* mostly automated: ~4 in 5 review comments come from
  bots (an existing AI agent, Sonar, PR-Guardian). Only 36 genuine-human
  comments total, and only **7 of 31** were substantive review.
- **22 net-new confirmed bugs (8 HIGH)** shipped past *both* humans and bots.
- Where a human *did* comment on a buggy PR, it was banter / nitpick / process
  ping — **never the actual defect** (e.g. "It IS used ffs. Read the code." on a
  PR that silently corrupts stored hours).
- **100% of confirmed defects were in the frontend PCF/TS-React repos**
  (PowerHeatmap, PowerGantt, Power-Financials). The .NET repos came back clean.
- Findings were trustworthy because of a **two-stage generous-review +
  adversarial-verify pass (91% cull rate)** — a single-pass agent would drown you
  in false positives.

**Reframe:** we're not removing a working control. There is no net. We're
building the first real one. The human moves from per-PR gatekeeper to
**per-release sign-off, only when the agent (or policy) flags it.**

---

## 2. The model — depth ladder

Nightly and release do **different jobs**, not the same review twice:
- **Nightly = keep `main` healthy** (continuous; catch + *fix* what the cheap
  per-PR gate let through; optimizes branch state).
- **Release = decide to ship** (periodic; go/no-go on a *cut* of main; optimizes
  confidence to ship).

| Tier | Cadence | Depth | Verdict | Who acts |
|---|---|---|---|---|
| **Per-PR** | every PR | fast: mechanical gates + fast triage + consumer thin CI (lint/type/test) | auto-approve obvious-safe / hard-block obvious-danger / **let the middle merge** | nobody (trunk-based) |
| **Nightly** | daily | deep: two-stage generous + adversarial (the 91%-cull pass) **+ fix** | fix high-confidence findings, ticket the ambiguous | Guardian opens fix PRs |
| **Release** | per cut | fat check over `last-release..HEAD` + rollup | ship / **sign-off** / block | human signs off *when flagged*, via the wizard |

Maps 1:1 onto industry-proven pieces: cheap gate on the critical path
(Semgrep/CIFuzz), deep async that bisects-and-tickets (ClusterFuzz), release as
authoritative gate (Argo/Kayenta). We're assembling proven parts, not inventing.

---

## 3. Decision log

### D1 — Per-PR stays *fast*, not *dumb* · **LEAN (open fork)**
Keep a fast Guardian pass per-PR (auto-approve safe, hard-block dangerous, merge
the middle) rather than stripping per-PR to pure thin CI. Cheap; catches
egregious stuff at merge instead of 12h later. The expensive **two-stage
adversarial** pass moves to nightly — it's too costly per-PR, which is exactly
why it belongs nightly. Audit backs this: per-PR Guardian missed 2 buggy PRs;
the deep two-stage pass caught all 22.
- **Fork F1 (OPEN):** keep fast per-PR pass *vs.* strip to thin CI + lean 100%
  on nightly/release. Changes how much bad code sits in main between merge and
  nightly fix.

### D2 — Fix = a PR, confidence-gated. Never a commit to main. · **LEAN**
- A fix is a **PR, not a push to main.** Direct commits make Guardian author
  *and* reviewer → segregation-of-duties violation + silent corruption risk.
- **Only auto-fix high-confidence / mechanical findings** (double-submit guard,
  missing error handling, git-ignored buildInfo). **Semantic/intent findings**
  (thousand-separator grouping; fallback resolving wrong entity) get *ticketed
  and routed to the human*, because the fix could be wrong — Guardian doesn't
  know intended behavior. Use the existing certainty system; don't bypass it.
- **Recursion:** who reviews Guardian's fixes? → the next nightly pass + the
  release gate. Safe only if fixes are confidence-gated and **deduped** (extend
  the diff-identity carry-forward that already exists for auto-approve to
  findings) so nightly doesn't churn main fighting itself / human edits.
- **Fork F2 (OPEN):** confidence-gated auto-fix PRs from day one *vs.* a
  "propose only, no PR without human trigger" mode during the trust ratchet.

### D3 — Release gate RE-RUNS the fat check over the window · **LEAN**
The release gate must run a fresh deep pass over `last-release..HEAD`, **not**
trust last night's results. Otherwise anything merged after the nightly run
ships unreviewed by the deep pass → holes when release cadence > nightly
cadence. This decouples release cadence from nightly cadence entirely. Nightly =
early-warning + fix; release = authoritative gate.

### D4 — Sign-off = AI escalation + non-negotiable policy floor · **LEAN**
"Human signs off if AI deems necessary" — but not *purely* AI's discretion (a
false-negative on "do we need a human?" ships a bad release unsigned). Two-part
trigger:
- **AI escalates:** any un-auto-fixed REV finding, any low-confidence fix, any
  architecture-drift/design finding in the window.
- **Policy floor (not AI's call):** changes to auth / payments / migrations /
  security surface *always* need sign-off, config-driven.
This is also the **compliance answer**: SOX §404 / SOC 2 CC8 want a
human-other-than-author sign-off. We give exactly that — at *release*
granularity, on the aggregate, when flagged. "No human review" becomes the
precise, defensible "no human review *per PR*; human sign-off *per release, when
it matters*." (PCI-DSS 6.2.3 already permits automated review outright.)

### D5 — Repurpose the wizard for release sign-off · **LEAN**
The review wizard already clusters related changes into reviewable chapters.
Point it at a *release*: "34 PRs since last release, 6 themes; here's what
Guardian found/fixed and the 2 things it wants your eyes on." Human signs off
holistically or bounces a chapter back → becomes a blocking finding → fix run /
manual fix → re-gate. Puts humans on the ~24% they're actually good at
(design/intent), not line-by-line hunting.

---

## 4. Open questions / forks (need ewi's call)
- **F1:** Per-PR — fast Guardian pass (D1 lean) vs. strip to thin CI only?
- **F2:** Fix aggressiveness — auto-fix PRs from day one (D2 lean) vs.
  propose-only during trust ratchet?
- **F3:** Release cadence assumptions — how often do consumers actually cut
  releases? (Determines whether on-demand fat re-run is cheap enough.)
- **F4:** Sign-off granularity — all-or-nothing on the release vs. per-chapter?
  (Leaning all-or-nothing to ship a coherent cut, chapters can be bounced back.)

---

## 5. Failure modes / risks to watch
- **Cost/latency.** Two-stage adversarial over a full day's diff + fixes + a fat
  release run = real LLM spend (`deep_max_prs=25` today). Needs a budget story:
  diff-scoped + hotspot-prioritized. Point deep budget at frontend PCF/TS repos;
  skip/thin the .NET repos (audit: clean).
- **Fix churn.** Auto-fix PRs fighting human edits or re-fixing the same thing.
  Needs finding-identity dedup + "don't touch what a human just changed."
- **Merge→release gap.** Load-bearing; mitigated by D3 (release re-runs).
- **AI deciding when humans are needed** (meta-risk). Mitigated by D4 policy
  floor.
- **Trust ratchet.** Cannot flip cold from "humans review everything" to "humans
  sign releases only." Run nightly-fat + release-gate in **shadow mode**
  (advisory, humans still review) until the false-*negative* rate is trusted,
  *then* flip. The 30-day audit is the first shadow-mode dataset. Rollout is
  evidence-driven, not faith-driven.

---

## 6. Packaging — what Guardian should own
Goal: consumers stop hand-rolling 148-line YAML (today they own the cron,
baseline-tag dance, 80-iteration poll loop, and the bash `case` that *is* the
release policy). Deliver as GitHub **reusable workflows** (`workflow_call`)
hosted in Guardian's repo:
- `guardian-pr-fast.yml` — fast per-PR Guardian pass + templated thin-CI stub.
- `guardian-nightly-fix.yml` — Guardian owns baseline, poll, deep pass, fix-PR
  creation.
- `guardian-release-gate.yml` — fat window check + wizard sign-off handoff;
  ideally wired as a GitHub `deployment_protection_rule` so it gates the
  *deploy*, not just a CI job.
Consumer surface: `uses: <org>/pr-guardian/.github/workflows/nightly-fix.yml@v1`
+ secrets. ADO gets the equivalent template + `Invoke REST API` gate.

### Current Guardian building blocks (already exist)
- `POST /api/review/range` — full pipeline over `base..head`, informational
  verdict (`auto_approve`/`human_review`/`reject`/`hard_block`). No write-back.
- `POST /api/scan/recent` `{deep:true}` — per-PR verdicts over a window
  (`recent_changes_deep`), self-contained, no review rows. `deep_max_prs=25`,
  `deep_concurrency=4`.
- `guardian/last-reviewed` baseline tag convention; example workflows in
  `examples/github/`.
- PR path posts `guardian/review` commit status (branch protection can require).
- **Gaps:** CLI never exits non-zero on a verdict; no release-readiness
  aggregate/rollup; no packaged action; `human_review` handling is baked into
  example YAML, not a Guardian concept.

---

## 7. Industry research (what others do — 2024-2026)
- **Merge queues** (GitHub, GitLab, Graphite, Trunk.io, Mergify): all support
  merge-on-green with zero human approval, but the queue is a *scheduler, not a
  judge*; human approval is a separate branch-protection gate. Trunk.io says it
  outright. **Trap:** a stray CODEOWNERS/required-review rule silently vetoes
  auto-merge (Renovate's known footgun) — Guardian should detect + warn.
- **AI reviewers** (CodeRabbit, Greptile, Qodo, Devin, Cursor, Sourcery,
  Ellipsis): *none* auto-approve by default; *none* auto-merge. Universal red
  line. Guardian's "never auto-merge, author clicks" matches the whole industry.
- **Release gating**: genuinely-automatic only = canary + metric rollback (Argo,
  Flagger, LaunchDarkly, Kayenta Mann-Whitney). Error budgets / DORA are
  *inputs*, not gates. Argo's `Inconclusive → human` == our release sign-off.
- **Nightly/deep split is real & vendored**: CodeQL (PR + weekly cron), Semgrep
  (narrow per-PR + broad nightly on main), ClusterFuzz (heavy async →
  bisect → triaged ticket, seeds the cheap per-PR tier). SonarQube's hard lesson:
  **gate on new/diff findings, never total debt** — whole-codebase gates fail
  forever and get disabled. (Our range/deep is already diff-scoped — keep it.)
- **Compliance walls**: SOX §404 / SOC 2 CC8 = segregation of duties (human ≠
  author must sign off) → full automation doesn't satisfy it; our release
  sign-off does. PCI-DSS 6.2.3 explicitly permits automated review.
- **AI-code risk data**: ~45% of AI-generated samples carry an OWASP Top-10
  vuln; design flaws up ~150% under AI-assisted dev; devs *believe* AI code is
  safer while shipping more insecure code. "Remove the human" is backwards
  *unless* the agent gate is genuinely good + there's a net under it — which the
  audit says is our situation.

---

## 8. Next steps / parking lot
- [ ] Resolve F1 (per-PR fast vs. thin-only) and F2 (fix aggressiveness).
- [ ] Decide release cadence assumptions (F3).
- [ ] `/plan-feature` the build: release-readiness verdict + rollup, fix-PR
      mechanics, wizard-for-release mode.
- [ ] Quick win regardless of forks: CLI `--fail-on reject,hard_block[,human_review]`
      exit codes (kills the hand-rolled bash `case`).
- [ ] Design the `no_human_mode` REV routing: `block | ticket | warn`, and where
      a `ticket` lands / how it's tracked.
- [ ] Shadow-mode plan: keep running the deep audit; define the
      false-negative-rate bar that justifies flipping humans to release-only.
- [ ] CODEOWNERS / required-review veto detector in the profile UI.

## Session log
- **2026-07-14** — initial exploration. Established thesis (audit), depth ladder,
  D1–D5 (all LEAN), forks F1–F4, failure modes, packaging, research. Nothing
  DECIDED yet; continuing to think.
- **2026-07-15** — **DECISION: greenfield.** ewi chose to start a new repo /
  fresh architecture rather than extend Guardian (I argued for a flagged module;
  ewi's call). Founding axiom inversion (humans out of per-PR loop) makes it
  defensible. Ran a Guardian architecture retrospective (keep/rebuild/kill).
  Engineering charter now lives in **`GREENFIELD_KICKOFF.md`** — read that for
  crown-jewel carry-forward, kill list, day-1 invariants, stack recs, and open
  decisions O1–O6. This doc remains the *product* thinking; forks F1–F4 carry
  over as O5.
