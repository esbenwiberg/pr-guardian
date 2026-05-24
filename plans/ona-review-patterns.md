# Ona Review Patterns — Supplement

> Targeted follow-up to `prompt-engineering-patterns.md`. Surveys how Ona
> (the agentic-AI rebrand of Gitpod) handles PR review and identifies what
> Guardian could borrow that wasn't already covered by Anthropic / OpenAI
> Codex / Qodo / claude-code. Short on purpose — Ona's public material
> contains a lot of marketing and very little prompt text, so most of the
> learnings are architectural rather than prompt-level.

## Confidence note

Every Ona-owned URL referenced below (`ona.com/*`) returns HTTP 403 to
`WebFetch` and `curl` from this environment, regardless of user-agent.
All Ona content in this report was reconstructed from Google's snippet
view (search-result excerpts) of those same pages, plus secondary
coverage (The Register, InfoQ, ZenML LLMOps database, Hacker News). Where
a claim is reconstructed from a snippet rather than a fetched primary
source, the citation includes `(snippet)`. No prompt text from Ona's
templates was retrievable — the marketing pages mention the template
exists but never quote it.

---

## 1. What Ona actually is

**Verified facts.**

| Claim | Source | Confidence |
|---|---|---|
| Gitpod, the company that built the Gitpod CDE, rebranded to "Ona" on 3 Sep 2025 and pivoted to an agentic AI engineering platform. | <https://www.theregister.com/2025/09/03/gitpod_rebrands_as_ona/>; <https://www.infoq.com/news/2025/09/gitpod-ona/> | High (multiple independent press sources) |
| The product is structured as three layers: **Ona Environments** (sandboxed cloud dev VMs), **Ona Agents** (long-running coding agents), **Ona Guardrails** (RBAC / SSO / audit / command deny-lists). | The Register article (above) | High |
| Each agent runs in its own ephemeral, OS-isolated VM (not a shared filesystem like Claude Code worktrees). | <https://ona.com/compare/claude-code> (snippet); also surfaced in The Register coverage | High |
| Ona has a dedicated PR-review case page at `ona.com/cases/code-review` and a how-to story at `ona.com/stories/automating-code-review`. PR review is a first-class product mode, not just a side-effect of the autonomous-coding agent. | Search-result titles + multiple snippet excerpts | High |
| The GitHub org is still `github.com/gitpod-io`. Visible repos include `gitpod`, `openvscode-server`, three SDKs (TS/Python/Go), `ona-kiro-power` (Kiro power for offloading work to Ona), `terraform-google-ona-runner`, `memo` (a Notion-style demo "built entirely by AI agents"). | <https://github.com/gitpod-io> (rendered) | High |
| The platform supports three operator postures: **manual** (agent suggests, human applies), **assisted** (agent applies with visibility), **autonomous** (agent acts unsupervised). | The Register article | High |
| Ona dogfoods aggressively: by their own claim, ~60% of merged PRs are co-authored by Ona and ~72% of merged LOC originate from Ona in a recent week. | <https://ona.com/> (snippet); repeated across multiple stories | Medium (self-reported; no third-party verification) |

**Verdict.** Ona is closer architecturally to autopod (long-running agent
harness inside a sandboxed VM) than to Guardian (stateless static review
gate). But unlike autopod, Ona also ships a **dedicated PR-review mode**
on top of that harness — a single product that does both "write the
code" and "review the code another agent wrote." That dual role is the
most interesting thing for Guardian to study.

---

## 2. Review-relevant patterns

### 2.1 The PR-review pipeline (reconstructed)

From the `stories/automating-code-review` page (snippet) and the
`cases/code-review` page (snippet), Ona's PR review flow is:

1. **PR webhook fires** when a PR is opened or marked ready-for-review.
   ([Ona Automations docs, snippet](https://ona.com/docs/ona/automations/overview))
2. **An Ona Agent boots an ephemeral VM**, clones the repo at the PR
   commit, installs the dev container, runs the test suite.
   ([cases/code-review, snippet](https://ona.com/cases/code-review))
3. **The agent reads the linked ticket** via integrations with Linear,
   Jira, Confluence, Notion. It cross-references the diff against the
   ticket's acceptance criteria. ([automating-code-review, snippet](https://ona.com/stories/automating-code-review))
4. **The agent posts a structured PR review** consisting of: summary,
   inline comments, suggested fixes (some of which it offers to apply),
   and an overall assessment. ([automating-code-review, snippet](https://ona.com/stories/automating-code-review))
5. **Optional auto-fix loop.** If configured, the agent will commit
   fixes for issues it found, rerun the test suite, and iterate "until
   builds pass." ([cases/code-review, snippet](https://ona.com/cases/code-review))
6. **Engineers can reply to the agent's comments** in the PR thread and
   the agent engages in conversation. ([automating-code-review, snippet](https://ona.com/stories/automating-code-review))

### 2.2 System prompt style

**No prompt text is publicly available.** The closest we get is:

> Ona provides a code review automation template with a detailed,
> high-quality prompt that has been refined through extensive internal
> use and customer feedback. Teams can use it as-is or customize it to
> match their own review standards.
> ([automating-code-review, snippet](https://ona.com/stories/automating-code-review))

That's a marketing claim, not a prompt excerpt. We can infer the persona
framing only indirectly through the slash-command pattern (see 2.4) and
the "Skills" docs (see 2.5).

### 2.3 Output format

**Structured PR comments** — not free-form, not JSON. From snippets:

> The agent leaves a structured review on the pull request including
> a summary of the changes, inline comments on specific issues,
> suggestions for quick fixes it can handle, and an overall assessment.
> ([automating-code-review, snippet](https://ona.com/stories/automating-code-review))

This is GitHub-native review output: top-level summary + inline
file-line comments + "approve / request changes" verdict + optional
suggested-edit blocks (the GitHub `suggestion` markdown feature).
Guardian already produces something similar via its adapter layer; the
novel piece is the *suggested-fix* affordance Ona lifts to first-class
(the agent both flags and offers to fix in one comment).

### 2.4 Tool access

**The review agent has full repo tools, not just diff access.** This is
the single largest architectural difference between Ona and Guardian.

| Capability | Ona | Guardian |
|---|---|---|
| Read the diff | yes | yes |
| Read files outside the diff | yes (full checkout) | no |
| Run the test suite | yes | no |
| Run linters / typecheckers | yes | no (autopod does, Guardian doesn't) |
| Run the application | yes (dev container boots) | no |
| Read linked tickets (Jira/Linear/etc) | yes | no |
| Read repo docs (AGENTS.md, ADRs) | yes (and explicitly designed for AGENTS.md) | partial (per `architecture-anchor-discovery.md`, planned) |
| Commit fixes back to the PR | yes (optional) | no (by design — Guardian is a verdict, not a coder) |

Source for the runtime tools: <https://ona.com/cases/code-review>
(snippet) — "the agent runs in a full dev container environment with
access to the codebase, it can actually run the code and execute tests."

### 2.5 Slash commands and Skills

This is Ona's most distinctive prompt-engineering pattern.

**Skills** are `SKILL.md` files (YAML frontmatter + markdown body)
discovered from `.ona/skills/` and from organization-level locations.
The agent auto-discovers them by matching the user's request against
each skill's `description` field. Format is identical to the Claude
Code / Anthropic Skills spec; Ona explicitly adopted the open standard.
([docs/ona/agents/skills, snippet](https://ona.com/docs/ona/agents/skills))

**Slash commands** are an optional invocation method for a skill —
typing `/review-like-mads` in chat invokes the skill named `review-like-mads`
with whatever follow-on text the user appends.
([docs/ona/slash-commands, snippet](https://ona.com/docs/ona/slash-commands))

Two snippets that matter:

> Slash commands let you codify and share workflows from your best
> engineers — for example, our internal frontend favorite quickly
> became `/review-like-mads`.
> ([cases/code-review, snippet](https://ona.com/cases/code-review))

> Ona has an engineer named Anton who writes exceptional PRs and gives
> thorough reviews, and instead of hoping everyone adopts his habits,
> they encoded them into slash commands that the whole team runs on
> every PR.
> ([stories/99th-percentile-org, snippet](https://ona.com/stories/99th-percentile-org))

The pattern: **a single team-member's review style becomes a reusable,
named prompt** that any agent can invoke. The skill body is the system
prompt for that review style; the name (`review-like-mads`) is how
engineers reference it.

### 2.6 Auto-approve gate

The closest direct competitor to Guardian's "low-risk auto-approval"
logic. From the `auto-approving-low-risk-prs` story (snippet):

> An Ona Automation evaluates every PR against objective criteria
> automatically, which removes the temptation to game the system and
> makes the boundary auditable.
>
> A change qualifies as low-risk only if it meets all of the following:
> Fewer than 1,000 lines changed (additions and deletions combined),
> along with [small changes, no sensitive areas touched, tests passing,
> no infrastructure modifications].
>
> Engineers cannot self-classify a change as low-risk... If any single
> criterion is not met, the change is routed to human review.

The reported impact:

> Lead time down 74%, time to first approval down 98% (2h49m → 3.8m),
> deploys tripled. ([auto-approving-low-risk-prs, snippet](https://ona.com/stories/auto-approving-low-risk-prs))

And the governance line that matters:

> Any changes to the evaluation logic, the criteria, or the agent's
> review prompt require explicit approval from the Head of Product
> Design and Engineering. Swapping the AI model counts as a material
> change to the policy.

That last sentence is the most policy-mature thing in any of the
public material we surveyed across the prior report.

### 2.7 Verification vs auditing framing

Ona's published material doesn't use the verifier/auditor vocabulary
from Anthropic's outcome-grader cookbook. The closest analogue is
implicit: review happens against an explicit anchor (the linked ticket
+ AGENTS.md + the team's `.ona/skills/` set), and the agent is
expected to ground each finding in that anchor. The slash-command
pattern (`/review-like-mads`) is effectively a way to load a different
verifier persona per invocation.

### 2.8 PR-review automation as part of a "software factory"

Worth noting because it's the framing Ona repeats most:

> The PR Reviewer was the first automation turned on, with no code
> merging without an agent reviewing it first. Every PR gets checked
> against the conventions documented in markdown files, tested, and
> either approved or sent back with comments. This piece turns
> "agents writing code" into something closer to a production line.
> ([building-a-software-factory-week-1, snippet via ZenML LLMOps DB](https://www.zenml.io/llmops-database/building-a-software-factory-with-ai-agents-and-automation-loops))

The mental model is **agents reviewing agents**. The PR-review pass
exists primarily to gate the autonomous-coding agent's output, not to
catch human mistakes. This matters for Guardian because it implies a
different calibration: Ona's reviewer is tuned for "what does an agent
typically get wrong" not "what does a human typically get wrong."

---

## 3. What's new vs the leaders Guardian already surveyed

Filtered against `prompt-engineering-patterns.md`. Anything that's a
repeat is collapsed to one line.

| Ona pattern | New vs prior report? |
|---|---|
| Reviewer persona, structured PR-comment output, ticket-grounded review | **Not new.** Mirrors Codex / Qodo / claude-code patterns already in §1 of the prior report. |
| Coverage-first / filter-later split | **Not new.** Already in §3 Gap 2. |
| Schema-as-instruction, citation requirement | **Not new.** Already in §1 + §3 Gap 1. |
| Skills / `SKILL.md` as the prompt-distribution unit | **New as a mechanism, even if the underlying idea (repo-local override file) is in §1.** Anthropic's REVIEW.md is one file per repo, top-down. Ona's Skills are *many* files per repo, bottom-up, with description-based auto-discovery. That's a different distribution model. |
| Per-engineer slash commands (`/review-like-mads`) | **New.** No leader in the prior report had a "named-persona" mechanism. Anthropic's `claude-code` has slash commands but they're not framed as "this is how Mads reviews." The cultural framing is new and worth thinking about. |
| Full-checkout review with test execution + iterative fix loop | **New as a review-mode capability.** The prior report assumed diff-only review; Anthropic's `claude-code` plugin spawns subagents that verify by running code, but doesn't iterate-and-fix. Ona's review agent will commit fixes and rerun tests until green. |
| Ticket cross-referencing as a first-class review check | **Adjacent.** The Anthropic `claude-code` review prompt mentions checking against PR description; Qodo can fetch a ticket. Ona elevates this to a routine gate, which is exactly what Guardian's planned `intent` agent will do (per `agent-redesign.md`). |
| Auto-approve criteria as an explicit, governed policy (1000 LOC ceiling, no sensitive areas, tests passing, no infra changes; non-self-classifiable; model-swap is a material policy change) | **New in maturity.** Guardian has triage tiers, but Ona ships an actual *governance policy* around them, including the line about model swaps being a policy change. No leader in the prior report goes this far. |
| AGENTS.md as the canonical repo-instruction file | **Not new** — the prior report's §1 already lists `AGENTS.md` under "high-priority repo-local instruction file." Ona just confirms the convention is winning. |
| Slash-commands taking inline arguments (`/review-like-mads Focus on the auth changes in src/auth/`) | **New.** Lets one named persona be steered per-invocation without a new skill. |

---

## 4. Concrete proposals for Guardian

Be honest about ROI: most of Ona's review tech is operational, not
prompt-level. There are exactly two prompt/UX patterns and one policy
pattern worth pulling.

| # | Borrow | Why | Effort |
|---|---|---|---|
| 1 | **Codify per-engineer review styles as named personas.** Ship Guardian's six specialists as a *default set*, but allow teams to drop additional named review styles into `.pr-guardian/reviewers/<name>.md`. The dashboard surfaces them as togglable extra agents. The slash-command UI is unnecessary (Guardian isn't an interactive agent), but the *file format* of "one named reviewer = one prompt" is reusable. | Lets a team encode "review like our security lead" as a Guardian agent without forking any specialist. Bottom-up complement to the top-down `.pr-guardian.yml` config. | M |
| 2 | **Document an explicit auto-approval policy template.** Borrow Ona's exact framing — non-self-classifiable, named criteria, governed model changes — and ship it as a sample `.pr-guardian/policy.md` that teams paste into their repo. Guardian already computes the triage tier; the missing piece is the *policy text* that justifies the gate to skeptical reviewers. Ona's policy reads as a model. | Auto-approval lives or dies on trust. A policy that says "this AI cannot be gamed; here are the exact criteria; the model is locked" is the unlock. Pure docs / template work — no code. | XS |
| 3 | **Add "the same review prompt can take inline steering arguments" to the re-review flow.** When the author re-reviews after dismissing findings (per `finding-feedback-loop.md`), let them add a one-line steering note that gets injected into every specialist's user message — `<reviewer_note>` block alongside `<previously_dismissed>`. Equivalent to Ona's `/review-like-mads Focus on auth/`. | Tiny addition to the feedback-loop plan. Adds zero new agent code, just one more XML block in `build_agent_context`. | XS |

**Things to NOT borrow.**

- **The full-checkout + run-tests review architecture.** Guardian is
  intentionally a static gate that runs in seconds, not a long-lived
  agent. Adopting Ona's runtime would erase Guardian's distinguishing
  property (cheap, deterministic, no VM provisioning). The autopod
  service is the right home for that pattern if we want it.
- **The auto-fix-and-iterate loop.** Same reason. Guardian's job is
  the verdict; fixes are out of scope.
- **Renaming specialists after team members.** The slash-command name
  trick (`/review-like-mads`) reads as cute internally and as cargo-cult
  externally. Keep Guardian's specialist names functional
  (`security_privacy`, `performance`, etc.); let *teams* invent named
  reviewers via proposal #1 above.

---

## 5. Architectural learnings beyond prompts

A few non-prompt patterns from Ona that map onto Guardian's roadmap.

### 5.1 Ephemeral, isolated environments per review

Ona spins up a fresh VM per PR review. Guardian doesn't and shouldn't
(see §4 above), but the *isolation property* matters: each review is
auditable, reproducible, and has no leaked state from prior reviews.
Guardian achieves this trivially by being stateless — worth noting as a
shared design value, not a thing to copy.

### 5.2 Webhook-driven review trigger as the canonical entry point

Ona's setup ("when a pull request is opened or moved to ready-for-review
on any of the configured projects, the automation fires; no manual step
required" — [automating-code-review, snippet](https://ona.com/stories/automating-code-review))
is the same model Guardian uses. Confirmation that the right primitive is
*the platform's PR-event webhook*, not a polling worker or a manual
trigger. No change for Guardian — just validation.

### 5.3 Review agent has tool access, by design

Ona's review agent reads the linked ticket via integrations (Linear,
Jira, Confluence, Notion). Guardian's `intent` agent (per
`agent-redesign.md`) needs the same input. The architectural question
Ona's design poses: is "fetch the linked ticket" a *retrieval step
before the agent runs* (Guardian's likely answer) or *a tool the agent
can call during the run* (Ona's apparent answer)?

For Guardian, pre-fetch is cheaper and more deterministic. Worth
making explicit in the `intent` agent's discovery-step spec: ticket
text is hydrated *before* the agent boots, then injected as a
`<linked_ticket>` XML block. The agent never sees the ticket API.

### 5.4 The "agent reviewing agent" calibration

If Guardian's customers start running autopod (or similar) and the
reviewed PRs are increasingly agent-authored, Guardian's specialists
should know that. Agent-authored code has different failure modes than
human-authored code:

- More likely: hallucinated imports, plausible-looking API misuse,
  over-eager refactors, scope creep into adjacent files
- Less likely: copy-paste duplication, naming typos, off-by-one in
  hand-written loops

The prior report didn't mention this calibration axis. Worth a future
task: add an optional `<author_type>human|agent|mixed</author_type>`
hint to the agent context, sourced from PR metadata or commit-author
heuristics. Don't bake into prompts yet — flag for the agent-redesign
follow-up.

### 5.5 Skills as a third config surface

Guardian today has two config surfaces: the built-in specialist prompts
(shipped with the code) and the planned `.pr-guardian.yml` (per-repo
overrides). Ona shows a third tier: **per-repo *additional* agents**
contributed as `SKILL.md`-style files in the repo, auto-discovered by
the orchestrator. This is what proposal #1 in §4 above formalizes.

The interesting design question: should those team-contributed agents
go through Guardian's validator like the built-in specialists do? Best
answer is probably yes — validator handles dedup and confidence
filtering regardless of which specialist produced the finding. A new
specialist that doesn't go through the validator would break Guardian's
"specialists cast wide, validator narrows" invariant.

---

## 6. Sources

Primary (Ona-owned; all fetched as snippet only due to 403 on
`WebFetch`):

- Ona homepage — <https://ona.com/>
- Code review case page — <https://ona.com/cases/code-review>
- Code review automation story — <https://ona.com/stories/automating-code-review>
- Auto-approve low-risk PRs story — <https://ona.com/stories/auto-approving-low-risk-prs>
- 99th percentile engineering org story — <https://ona.com/stories/99th-percentile-org>
- Building a software factory: week 1 — <https://ona.com/stories/building-a-software-factory-week-1>
- Gitpod-is-now-Ona launch story — <https://ona.com/stories/gitpod-is-now-ona>
- Ona vs Claude Code comparison — <https://ona.com/compare/claude-code>
- Skills documentation — <https://ona.com/docs/ona/agents/skills> and <https://ona.com/docs/ona/skills>
- Slash-commands documentation — <https://ona.com/docs/ona/slash-commands>
- AGENTS.md documentation — <https://ona.com/docs/ona/agents-md>
- Automations overview — <https://ona.com/docs/ona/automations/overview>
- Templates marketplace — <https://ona.com/templates>
- Best practices — <https://ona.com/docs/ona/best-practices>
- Full-docs LLM dump (also 403) — <https://ona.com/docs/llms-full.txt>

Secondary (fetched successfully or rendered via search snippets):

- The Register coverage of the rebrand — <https://www.theregister.com/2025/09/03/gitpod_rebrands_as_ona/>
- InfoQ coverage of the rebrand — <https://www.infoq.com/news/2025/09/gitpod-ona/>
- ZenML LLMOps Database entry on Ona's software factory — <https://www.zenml.io/llmops-database/building-a-software-factory-with-ai-agents-and-automation-loops>
- Hacker News thread on the launch — <https://news.ycombinator.com/item?id=45102431>
- Ry Walker comparison of agentic platforms — <https://rywalker.com/research/model-agnostic-agentic-engineering-platforms>
- Ona GitHub org (rendered) — <https://github.com/gitpod-io>
- ona-kiro-power repo README — <https://github.com/gitpod-io/ona-kiro-power>

Inaccessible from this environment:

All `ona.com` pages returned HTTP 403 to both `WebFetch` and `curl`
regardless of user-agent (tested: default, Chrome, GPTBot, ChatGPT-User,
plain `curl`). The site appears to block non-browser traffic at the
edge. Content from those pages is reconstructed from Google search
snippets only; direct quotes are marked `(snippet)`.

The Ona YouTube channel video on automating code review
(<https://www.youtube.com/watch?v=teaCTYBPMzM>) also returned 403; no
transcript was retrievable.
