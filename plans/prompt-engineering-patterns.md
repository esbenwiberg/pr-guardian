# Prompt-Engineering Patterns for Code Review Agents

> Survey of how Anthropic, OpenAI, CodeRabbit, Greptile, Ellipsis, Qodo, and
> the LLM-as-judge literature structure prompts for code-review and
> verifier-style tasks. Maps the techniques they share onto Guardian's
> existing prompts and proposes a handful of concrete, low-effort edits that
> close the biggest gaps.

## Motivation

Guardian's specialist prompts are short ("role + Checks + Output
Requirements"); the validator is more sophisticated but lives alone. The
`agent-redesign.md` plan already commits to three structural fixes
(diff-header citation, context-lines-are-read-only, JSON-only output). This
report asks: *what else are the leaders doing that we aren't?*

Findings ordered by ROI. Skip the rationale, read the table at
[Concrete proposals](#5-concrete-proposals).

---

## 1. Patterns survey

Grouped by purpose. Each row is "what the technique is" + "who recommends it"
+ "what they say about code-review specifically."

### Structural

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **XML tags around content blocks** (`<diff>`, `<pr_context>`, `<finding>`) | Anthropic prompting best practices; appears in every Anthropic cookbook example | Anthropic explicitly recommends "Wrap each type of content in its own tag... reduces misinterpretation." Tags also let the model *output* in the same vocabulary it received. |
| **Long-form data at the top, query/instructions at the bottom** | Anthropic ("Queries at the end can improve response quality by up to 30% in tests, especially with complex, multi-document inputs") | Direct quote from Anthropic's long-context guidance. Code review is exactly this shape — large diff + targeted instruction. |
| **Structured output via tools / response schema, not "respond with JSON"** | Anthropic ("Try simply asking the model to conform to your output structure first... For classification tasks, use either tools with an enum field containing your valid labels or structured outputs"); OpenAI ("avoid describing the expected output schema in the prompt and use Structured Outputs for automatic validation") | The OpenAI Codex review prompt uses a JSON schema as a literal instruction *and* a tool. Qodo PR-Agent uses Pydantic types rendered into the prompt. |
| **Hunk-aware diff format with line numbers** | Qodo PR-Agent (their actual prompt block) | Qodo prefixes each line in the new hunk with its line number *inside* the diff body, so the model can cite without inventing. Their prompt explicitly says these numbers "are not part of the actual code, and should only be used for reference." |
| **Per-finding evidence pointer (file + line range)** | OpenAI Codex review prompt requires `code_location.line_range.{start,end}` with "the most suitable subrange that pinpoints the problem"; Anthropic Code Review docs require inline-comment placement; Ellipsis attaches evidence to each draft comment for the verification stage | All three treat the evidence pointer as a hard schema field, not a suggestion. |

### Reasoning

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **Coverage at finding-stage, filtering at a later stage** | Anthropic, dedicated section "Code review harnesses": *"Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage — a separate verification step will do that. Your goal here is coverage..."* | This is the single most code-review-specific recommendation in the entire Anthropic doc. They explicitly warn against conservative-language prompts ("don't nitpick") that cause modern Claude models to silently drop findings. |
| **Two-stage prompting** (generate → judge / generate → reformat) | LangChain LLM-as-judge guide; Datadog hallucination detector; Ellipsis ("multistage filtering pipeline... Logical Correctness filter") | Datadog: stage 1 is unrestricted self-critique, stage 2 reformats to schema. Ellipsis: each generator attaches evidence, then a separate filter validates each draft against that evidence. |
| **Self-check / verify-before-emit** | Anthropic ("Ask Claude to self-check. Append 'Before you finish, verify your answer against [test criteria].'"); Anthropic `claude-code` review command (step 5: "launch parallel subagents to validate the issue") | Anthropic's `claude-code` plugin literally spawns a *separate subagent per candidate finding* to verify the issue exists before posting. |
| **According-to / quote-grounding prompts** | Anthropic long-context recipe ("ask Claude to quote relevant parts of the documents first before carrying out its task"); "According to..." prompting paper (arXiv 2305.13252) | For code review the equivalent is "quote the exact diff line you're flagging before describing the finding." Forces the model to ground in the diff. |

### Persona / framing

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **Reviewer-of-another-engineer persona** | OpenAI Codex (*"You are acting as a reviewer for a proposed code change made by another engineer"*); Qodo PR-Agent (*"You are PR-Reviewer, a language model designed to review a Git Pull Request"*); Anthropic Code Review plugin (*"Code review a pull request"*) | All three open with the same frame. Implicit: not the author, not a teacher — a peer reviewer. |
| **"The author would fix this if they knew"** test | OpenAI Codex review prompt, criterion 5: *"The author of the original PR would likely fix the issue if they were made aware of it."* | A behavioural rubric. Cleaner than "is this important?" because it forces the model to predict author behaviour rather than its own opinion. |
| **Verifier vs. auditor framing** | Anthropic outcome-grader cookbook (the rubric/grader is "independent, stateless... no ability to be persuaded... mandatory verdict... must pass/fail every criterion") | Maps directly onto Guardian's `intent` and `architecture` agents in `agent-redesign.md`. The cookbook formalizes the pattern. |
| **Adversarial / "represent the developer's perspective"** | Guardian's own `validator/base.md` already does this | The Datadog hallucination judge uses the inverse — frames the source as "expert advice" and the candidate as "candidate answer" to bias toward skepticism. |

### Evidence / grounding

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **Findings must cite a `file:line` visible in the diff** | OpenAI Codex (`code_location` is a required schema field, "must overlap with the diff"); Anthropic Code Review docs (REVIEW.md example: *"behavior claims need a `file:line` citation in the source, not an inference from naming"*); Anthropic `claude-code` review command (step 9, requires permalink) | Guardian's `agent-redesign.md` already commits to "diff-header citation as hard rule." Leaders go further: line range required, range "as short as possible" (≤5–10 lines per Codex). |
| **Explicit "you only see the diff" warning** | Qodo PR-Agent: *"Note that you only see changed code segments (diff hunks in a PR), not the entire codebase. Avoid suggestions that might duplicate existing functionality or questioning code elements (like variables declarations or import statements) that may be defined elsewhere"* | Without this, models routinely flag "this function is undefined" when it's defined outside the diff window. Guardian has nothing equivalent. |
| **Pre-existing code is out of scope** | OpenAI Codex criterion 4 ("introduced in the commit"); Anthropic Code Review docs treat pre-existing bugs as their own severity bucket (🟣 Pre-existing); Guardian's validator handles it post-hoc | Leaders push this rule *upstream* into the specialist, not just into the validator. |
| **Provability requirement** | OpenAI Codex criterion 7: *"It is not enough to speculate that a change may disrupt another part of the codebase, to be considered a bug, one must identify the other parts of the code that are provably affected."* | Sharper than Guardian's "no hedging" rule because it specifies what evidence counts. |

### Output discipline

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **Schema-as-instruction inside the prompt** | OpenAI Codex (literal JSON skeleton with field comments); Qodo PR-Agent (Pydantic schema rendered into prompt); Guardian validator already does this | Universal. |
| **Confidence score per finding** | OpenAI Codex (`confidence_score: 0.0-1.0`); Anthropic Code Review (0-100, default threshold 80); Anthropic recommends this for downstream filtering | Guardian uses `certainty: detected/suspected/uncertain` — same shape, coarser scale. |
| **Priority / severity tag** | OpenAI Codex (P0/P1/P2/P3); Anthropic Code Review (Important / Nit / Pre-existing); Qodo (score 0-100) | All three quantize severity. Guardian has `severity` but no `nit` bucket — leaders call this out as a distinct class for "worth fixing, not blocking." |
| **"Empty list is acceptable"** | Qodo PR-Agent: *"An empty list is acceptable if no clear issues are found."*; Anthropic ("If everything you found is a Nit, lead the summary with 'No blocking issues.'") | Modern models will invent findings to satisfy a non-empty schema if you don't say this. Guardian's prompts don't say it. |

### Customization / instruction-priority

| Pattern | Sources | Code-review-specific note |
|---|---|---|
| **High-priority repo-local instruction file** | Anthropic `REVIEW.md` ("injected directly into every agent... as highest-priority instruction block"); Greptile `greptile.json` instructions; CodeRabbit `.coderabbit.yaml` | Maps onto `agent-redesign.md`'s `review.yml::architecture_docs` proposal but is broader — per-class severity overrides, skip rules, repo-specific must-checks. |
| **Re-review convergence rule** | Anthropic REVIEW.md example: *"after the first review, suppress new nits and post Important findings only"* | Directly relevant to `finding-feedback-loop.md`. The re-review stage should not produce a fresh nit storm. |

---

## 2. What Guardian already does well

Read the specialists and validator together; the system as a whole hits more
best practices than any single prompt reveals.

| Practice | Guardian's implementation | Leader equivalent |
|---|---|---|
| Multi-agent decomposition (specialists fan out, validator narrows) | Six specialist prompts → `validator/base.md` filter | Ellipsis architecture; Anthropic Code Review's "fleet of specialized agents... a verification step checks candidates"; `claude-code` plugin's step-4-then-step-5 split |
| Explicit dismissal taxonomy | `validator/base.md` §"Dismissal criteria" (six numbered classes) | LangChain LLM-as-judge: *"detailed rubric with examples yields better outcomes than vague instructions like 'rate responses for quality'"* |
| Persona that justifies skepticism | `validator/base.md`: *"You represent the developer's perspective. Developers lose trust in review tools that cry wolf..."* | OpenAI Codex's reviewer persona; Datadog's "expert advice / candidate answer" asymmetric framing |
| Severity downgrade as a distinct action | `validator/base.md` §"Downgrade criteria" (`keep / dismiss / downgrade`) | Anthropic Code Review's three-bucket scheme (Important / Nit / Pre-existing); few leaders allow downgrade as a verb on existing findings — this is a Guardian-specific strength |
| Concrete noise-control rules with thresholds | `recent_changes/trend/base.md` §"Noise Control": *"If you only see one data point, do not create a finding"* | Anthropic explicitly recommends "be concrete about where the bar is rather than using qualitative terms like 'important'" |
| Per-agent scoping | Tight Checks list per specialist (e.g., `performance/base.md` enumerates 11 concrete categories) | Ellipsis: "make the problem easier for the LLM to solve" by decomposing into focused agents |
| Symmetric error penalty | `validator/base.md`: *"letting a false positive through is penalised just as much as dismissing a real issue"* | LangChain calibration guidance — naming the failure mode out loud sharpens the judge |

Guardian's pattern-match against the leaders is strongest where the
validator is involved. The specialists themselves are the weak link.

---

## 3. Gaps

Ordered roughly by impact.

### Gap 1: Specialists have no explicit "you only see the diff" warning

**Anchor:** Qodo PR-Agent's system prompt block:
> Note that you only see changed code segments (diff hunks in a PR), not the entire codebase. Avoid suggestions that might duplicate existing functionality or questioning code elements (like variables declarations or import statements) that may be defined elsewhere in the codebase.

Without this, models flag "missing import" or "function not defined" when
the symbol lives outside the diff window. Guardian's specialists currently
get scope rules ("only report findings you can point to in the diff") but
no warning about *what they don't see*.

Source: <https://github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/pr_reviewer_prompts.toml>

### Gap 2: No finding-stage coverage vs. filter-stage separation in the prompt

**Anchor:** Anthropic prompting best practices, "Code review harnesses" section:
> When a review prompt says things like "only report high-severity issues," "be conservative," or "don't nitpick," Claude Opus 4.7 may follow that instruction more faithfully than earlier models did... Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage — a separate verification step will do that.

Guardian's specialists tell the model to be conservative *inside the
finding step* (e.g., `trend/base.md`: "When in doubt, do NOT report").
Anthropic warns this combines badly with a downstream validator: the
specialist drops the candidate, the validator never sees it. The right
factoring is "specialists cast a wide net; validator filters" — Guardian's
two-stage architecture supports this but the prompts contradict it.

Source: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices> (section "Code review harnesses")

### Gap 3: No XML tags around inputs

**Anchor:** Anthropic prompting best practices:
> XML tags help Claude parse complex prompts unambiguously, especially when your prompt mixes instructions, context, examples, and variable inputs. Wrapping each type of content in its own tag (e.g. `<instructions>`, `<context>`, `<input>`) reduces misinterpretation.

Guardian's specialists are pure prose markdown. The user message that
carries the diff, file list, blast radius, and (per the feedback-loop plan)
dismissals is presumably concatenated without structural separators. This
is the cheapest possible upgrade — wrap the four context blocks in
`<diff>`, `<pr_metadata>`, `<previously_dismissed>`, `<repo_anchor>` and
the model parses the boundaries for free.

Source: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags>

### Gap 4: Findings don't quote the diff line they flag

**Anchor:** Anthropic long-context recipe:
> Ground responses in quotes... ask Claude to quote relevant parts of the documents first before carrying out its task. This helps Claude cut through the noise of the rest of the document's contents.

Guardian findings carry `file` + `line` (per the redesign plan, a header
citation). None of the prompts require the finding body to *quote the
offending line*. Quoting forces the model to ground the finding in real
diff content; if it can't produce a quote, the finding is hallucinated.

Source: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices> (Long context section); also "According to..." prompting (<https://arxiv.org/pdf/2305.13252>)

### Gap 5: No "author would fix this" rubric

**Anchor:** OpenAI Codex review prompt, criterion 5:
> The author of the original PR would likely fix the issue if they were made aware of it.

Guardian's specialists list categories ("Authentication bypass possibilities",
"N+1 query patterns") but no behavioural test for "is this worth posting?"
The Codex criterion is a one-liner that does what the validator's
dismissal taxonomy does, but at the source — and in a frame the specialist
can apply per-finding without categorising into Guardian's six dismissal
classes.

Source: <https://github.com/openai/codex/blob/main/codex-rs/core/review_prompt.md>

### Gap 6: No per-finding confidence number

**Anchor:** OpenAI Codex schema: `confidence_score: 0.0-1.0` per finding.
Anthropic Code Review uses 0-100 with default threshold 80 for posting.

Guardian has `certainty: detected/suspected/uncertain` — a three-level
ordinal. A continuous score is finer-grained and lets the validator (or
the dashboard) sort, threshold, or weight without re-asking the model.
The migration is mechanical: keep the three-level enum but also emit
`confidence: 0.0-1.0`.

Source: <https://github.com/openai/codex/blob/main/codex-rs/core/review_prompt.md>; <https://code.claude.com/docs/en/code-review>

### Gap 7: No "empty list is acceptable" instruction

**Anchor:** Qodo PR-Agent:
> An empty list is acceptable if no clear issues are found.

Modern Claude/GPT models trained to be helpful will invent low-quality
findings if the schema is non-empty and they've found nothing. Guardian
prompts say "report findings you can point to" but never say "zero
findings is a valid response." Cheap fix.

Source: <https://github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/pr_reviewer_prompts.toml>

### Gap 8: No verifier-style "provably affected" rule

**Anchor:** OpenAI Codex criterion 7:
> It is not enough to speculate that a change may disrupt another part of the codebase, to be considered a bug, one must identify the other parts of the code that are provably affected.

Guardian's `validator/base.md` dismisses "speculative" findings using
hedge-word detection ("might", "could potentially"). Codex pushes the bar
higher: even confident-sounding findings need to *name the affected
code*. This is what makes Codex's reviews terse and concrete.

Source: <https://github.com/openai/codex/blob/main/codex-rs/core/review_prompt.md>

### Gap 9: No "nit" severity tier

**Anchor:** Anthropic Code Review:
> 🟡 Nit — A minor issue, worth fixing but not blocking

Guardian uses `severity: low/medium/high/critical`. "Low" is ambiguous:
is it a minor real bug or a polish suggestion? A dedicated `nit` bucket
lets the dashboard collapse them, lets re-review suppress them, and gives
the validator a downgrade target softer than `dismiss`.

Source: <https://code.claude.com/docs/en/code-review>

### Gap 10: No few-shot examples in specialist prompts

**Anchor:** Anthropic prompting best practices:
> Examples are one of the most reliable ways to steer Claude's output format, tone, and structure... Include 3-5 examples for best results... Wrap examples in `<example>` tags.

Few-shot prompting is the highest-leverage technique Guardian has *not*
adopted at all. One worked example per specialist (e.g., the
`security_privacy` agent shown one true-positive SQL-injection finding +
one dismissed-style false positive) would calibrate severity and tone in
a way that no amount of prose instruction matches. The reason this is
gap 10, not gap 1, is that adding examples is more work than the other
fixes and risks overfitting if chosen badly.

Source: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices>; few-shot empirical study (<https://www.morphllm.com/few-shot-prompting>)

---

## 4. Anti-patterns

What the leaders warn against, with Guardian's current exposure noted.

| Anti-pattern | Source | Guardian's exposure |
|---|---|---|
| **Contradictory instructions accumulating over time.** Repeating semantically similar rules ("If data is missing, then..." and "If data is incomplete, then...") confuses the model. | OpenAI Developer Community thread (<https://community.openai.com/t/prompt-anti-patterns-when-more-instructions-may-harm-model-performance/1372460>) | Low. Specialist prompts are short and tightly scoped. |
| **Conservative-language prompts on modern models.** "Don't nitpick," "be conservative," "only report high-severity issues" can cause modern Claude to silently drop investigated bugs. | Anthropic, "Code review harnesses" section | **High.** `trend/base.md` ends with *"When in doubt, do NOT report. Developers lose trust in tools that cry wolf."* Six other specialists have similar language. See Gap 2. |
| **Telling the model what *not* to do instead of what to do.** | Anthropic ("Tell Claude what to do instead of what not to do... Positive examples... tend to be more effective than negative examples") | Medium. `validator/base.md` is mostly positive framing. Several specialists use "do NOT" language. |
| **Vague qualitative thresholds.** Telling the model to be "important" or "high quality" without operationalising what that means. | Anthropic Code Review docs: *"be concrete about where the bar is rather than using qualitative terms like 'important' — for example, 'report any bugs that could cause incorrect behavior...'"* | Medium. `architecture_intent/base.md`: "Focus on structural and design issues, not style" — but "structural" and "style" are undefined. |
| **Asking for high-precision numerical scoring.** "Rate quality 0-100" produces inconsistent outputs. Binary or 3-5 level ordinals calibrate better. | Galileo LLM-as-judge guide; Monte Carlo blog | Low. Guardian's existing `certainty` is a 3-level ordinal. Don't move to a 0-100 score for *judgment* — Gap 6 is about confidence on a *single finding*, which is calibratable. |
| **Same model used for generate + critique can rubber-stamp itself.** | Multiple sources on Reflexion / reflection patterns | Low. Guardian's validator runs as a separate LLM call with its own prompt and persona. Worth keeping that separation. |
| **Long, kitchen-sink system prompts** dilute the rules that matter. | Anthropic REVIEW.md guidance: *"Length has a cost: a long REVIEW.md dilutes the rules that matter most. Keep it to instructions that change review behavior."* | Low. Specialists are tight. The validator is the longest prompt and is still under 60 lines. |
| **Markdown fences around JSON output.** Modern models will wrap JSON in ```json blocks unless told not to, breaking parsers. | OpenAI Codex prompt: *"Do not wrap the JSON in markdown fences or extra prose."*; Qodo: *"Answer should be a valid YAML, and nothing else."* | Already on the redesign list (`agent-redesign.md` change 3). Keep. |
| **Asking the model to compute things best left to code** (file existence checks, line-number arithmetic, hash matching). | OpenAI Codex: line ranges must overlap with the diff — but enforcement is a downstream parser job, not in-prompt | Low. Guardian's `feedback-loop` plan already hashes signatures in code. Keep that boundary. |

---

## 5. Concrete proposals

Each row maps to a gap above. Ordered by ROI (impact / effort). All are
prompt-only changes — no code, no architecture rework.

| # | Change | Files | Effort | Expected impact |
|---|---|---|---|---|
| 1 | **Add "you only see the diff" disclaimer to every specialist.** One paragraph borrowed near-verbatim from Qodo (see snippet below). | All 6 specialist `base.md` | XS | Eliminates "missing import"-style FPs for symbols defined outside the diff window. |
| 2 | **Invert the noise-control language in specialists.** Replace "when in doubt, do NOT report" with "report every finding you can ground in a diff line; the validator will filter for confidence and severity." Specialists become *coverage* agents; the validator stays the *precision* agent. | All 6 specialist `base.md`, especially `trend/base.md` and `architecture_intent/base.md` | S | Anthropic's "Code review harnesses" recommendation, almost verbatim. Should raise recall without hurting precision (validator is unchanged). |
| 3 | **Wrap user-message context blocks in XML tags.** `<diff>...</diff>`, `<pr_metadata>...</pr_metadata>`, `<previously_dismissed>...</previously_dismissed>`, `<repo_anchor>...</repo_anchor>`. Single change in `agents/base.py::build_agent_context`. | `agents/base.py` (one site), each specialist mentions the tag names | XS | Parsing reliability + zero token overhead. Anthropic's #1 structural recommendation. |
| 4 | **Require findings to quote the offending diff line.** Add a `quote` field to the JSON schema: *"the exact diff line (starting with `+`) you are flagging."* The validator's existing post-hoc check can verify the quote appears in the diff. | All 6 specialist `base.md` + validator schema | S | Hallucinated findings become impossible — if the model can't produce a real `+` line, the finding can't form. |
| 5 | **Add "empty list is acceptable" to every specialist.** One sentence. | All 6 specialists | XS | Eliminates manufactured findings on clean diffs. |
| 6 | **Add the "author would fix this" rubric** as a per-finding behavioural test, borrowed from OpenAI Codex criterion 5. | All 6 specialists | XS | Single-sentence rubric outperforms long dismissal taxonomies for "is this worth posting?" judgments at the specialist stage. |
| 7 | **Emit `confidence: 0.0-1.0` per finding** alongside the existing `certainty` enum. | Schema in each specialist + validator + parser | S | Lets the validator threshold smoothly (Anthropic Code Review uses 80/100 as default), lets the dashboard sort, lets the feedback loop weight dismissals by how confident the original finding was. |
| 8 | **Add a `nit` severity tier** between `low` and `dismissable`. | Severity enum in all specialists, validator, dashboard | M | Matches Anthropic Code Review's three-bucket model. Lets re-review suppress nits without dropping real-but-minor findings. Also unlocks the "post at most N nits" rule from REVIEW.md. |
| 9 | **Adopt "provably affected" rule for cross-file claims.** Add to validator dismissal criteria: *"If a finding speculates that a change affects other code, dismiss unless it identifies the affected code path by file:line."* | `validator/base.md` | XS | Sharper than the current "speculative" hedge-word rule. Direct lift from Codex criterion 7. |
| 10 | **One worked example per specialist.** Inside `<example>` tags at the bottom of each prompt: one true-positive finding (showing schema + tone) and one anti-example (a finding the agent should NOT produce). | All 6 specialists | M | Highest-impact change here, also the highest-effort. Defer if the schema/quote/scope changes (#1, #2, #4) close the gap. |

### Before/after snippet — gap 2 (the conservative-language inversion)

Current (`recent_changes/trend/base.md`, last line):

> When in doubt, do NOT report. Developers lose trust in tools that cry wolf.

Proposed:

> Report every trend you can ground in two or more PRs from the supplied window. Include trends where you are uncertain about the severity — a downstream validator will filter by confidence, deduplicate against other agents, and downgrade where appropriate. Your job at this stage is recall, not precision.

Same direction, different agent (`security_privacy/base.md`, "Output
Requirements"):

> - Only report findings you can point to in the diff

→

> - Report every finding you can ground in a diff line (a `+`-prefixed line). Err on the side of inclusion: a downstream validator handles dedup, confidence-thresholding, and severity calibration. Your goal here is coverage, not filtering.

### Before/after snippet — gap 1 (the "you only see the diff" disclaimer)

Add to every specialist, right under the one-line role description:

> ## What you can see
>
> You see only the changed segments of this PR's diff plus PR metadata. The
> rest of the codebase is invisible to you. Symbols you don't recognise are
> probably defined outside the diff window — do not flag them as undefined,
> missing, or duplicate without proof from inside the diff. When a finding
> would require checking code outside the diff to confirm, downgrade
> certainty or omit the finding.

### Before/after snippet — gap 4 (require quote)

Add to JSON schema in each specialist:

```json
{
  "file": "src/auth/session.ts",
  "line": 142,
  "quote": "+    return tokens[req.user_id]  // race with logout",
  "category": "...",
  "severity": "..."
}
```

Prompt sentence:

> Every finding must include a `quote` field containing the exact `+`-prefixed line from the diff that the finding is about. If you cannot produce a verbatim quote, drop the finding.

The validator already has access to the diff and can mechanically verify
the quote appears.

---

## 6. Worth-trying-but-uncertain

Things the leaders use but where the evidence-for-Guardian is weaker.

| Technique | When it helps | When it hurts | Guardian-specific take |
|---|---|---|---|
| **Chain-of-thought scratchpad** (`<thinking>...</thinking>` before the JSON output) | Multi-hop reasoning where the answer depends on combining evidence (e.g., intent verification: "does this diff match the work item?") | Single-pattern detection (e.g., "is there an SQL concatenation in this line?") — CoT adds latency for no quality gain | The new `intent` agent in `agent-redesign.md` is a CoT candidate. The pattern-matchy specialists (security, performance) probably aren't. **Test before adopting broadly.** Anthropic notes: "Manual CoT as a fallback. When thinking is off, you can still encourage step-by-step reasoning... Use structured tags like `<thinking>` and `<answer>` to cleanly separate reasoning from the final output." |
| **Few-shot examples** | Calibrating tone, severity, schema adherence (Gap 10) | Risk of overfitting if examples are unrepresentative; risk of model copying example content verbatim into outputs | Use 2-3 examples per specialist, span true-positive + false-positive + edge case. Wrap in `<example>` tags. Anthropic recommends 3-5 but warns "Diverse: Cover edge cases and vary enough that Claude doesn't pick up unintended patterns." |
| **Prefill** | Older Claude models, forcing JSON-only output | Deprecated on Claude 4.6+ ("Prefilled responses on the last assistant turn are no longer supported. Requests... return a 400 error.") | **Don't.** Anthropic explicitly migrated away from prefill in 2026. Use Structured Outputs or just ask for JSON. |
| **Adaptive thinking / reasoning_effort** | Hard diffs where the model would otherwise miss subtle interactions | Easy diffs where extra thinking adds cost and latency for no quality gain | Anthropic's `thinking: adaptive` mode does this automatically on Claude 4.6+; OpenAI's `reasoning_effort` is the equivalent. Don't bake effort settings into the prompt — set them at the API call level per triage tier (trivial → low, medium PR → medium, high-blast-radius PR → high). |
| **Asking the model to self-rate confidence and then post-filter on that** | Recall-vs-precision tradeoffs (Gap 6) | The model's confidence is correlated with truth but not strongly. Don't trust it as an absolute threshold. | Use confidence as one input to validator + dashboard ranking, never as a hard cutoff in the specialist itself. Anthropic Code Review uses 80/100 as default but lets the user tune. |
| **"According to the diff..." prompt prefix** | Improves quoting accuracy in research tasks (paper: arXiv 2305.13252) | Untested for code review specifically | Lower-effort version of Gap 4 (require quote). If quote-as-schema-field is too heavy, try this first. |
| **Multi-agent debate / dueling reviewers** | High-stakes domains where false negatives matter most | Latency + cost roughly 2x; modest quality gain for code review per Ellipsis's experience ("we use *filters* not debate") | Guardian's six specialists + validator already provide the diversity; don't add a duelling layer. |
| **Repo-local `REVIEW.md` style override file** | Teams with strong opinions about severity calibration, skip rules, repo-specific checks | Adds a config surface to maintain; small teams won't use it | The `agent-redesign.md` plan already proposes `review.yml::architecture_docs`. Worth generalising to `review.yml::review_overrides` once the prompt fixes above land — leverages the highest-priority-injection pattern Anthropic uses. |

---

## Sources

Primary sources (direct prompt text or official docs):

- Anthropic prompting best practices — <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices> (especially the "Code review harnesses" subsection)
- Anthropic Code Review docs (REVIEW.md, multi-agent verify pipeline) — <https://code.claude.com/docs/en/code-review>
- Anthropic Claude Code `code-review` plugin command — <https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md>
- Anthropic outcome-grader cookbook — <https://platform.claude.com/cookbook/managed-agents-cma-verify-with-outcome-grader>
- OpenAI Codex review prompt — <https://github.com/openai/codex/blob/main/codex-rs/core/review_prompt.md>
- Qodo PR-Agent reviewer prompt — <https://github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/pr_reviewer_prompts.toml>
- Anthropic XML-tag guidance — <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags>

Secondary sources (summaries, blog posts):

- Ellipsis architecture overview — <https://www.ellipsis.dev/blog/how-we-built-ellipsis> (fetched via summary; original 403'd)
- Greptile v3 agentic review — <https://www.greptile.com/blog/greptile-v3-agentic-code-review> (summary)
- Greptile prompt guide — <https://docs.greptile.com/prompt-guide> (summary)
- CodeRabbit explainable reviews — <https://www.coderabbit.ai/blog/explainable-reviews-coderabbit-review-context-engine> (summary; 403'd on fetch)
- Datadog hallucination judge — <https://www.datadoghq.com/blog/ai/llm-hallucination-detection/>
- Monte Carlo LLM-as-judge — <https://montecarlo.ai/blog-llm-as-judge/> (summary)
- LangChain LLM-as-judge calibration — <https://www.langchain.com/articles/llm-as-a-judge> (summary; 403'd)
- Galileo LLM-as-judge prompt engineering — <https://docs.galileo.ai/concepts/metrics/custom-metrics/prompt-engineering> (summary)
- baz-scm/awesome-reviewers — <https://github.com/baz-scm/awesome-reviewers>
- Few-shot empirical evidence — <https://www.morphllm.com/few-shot-prompting>

Research papers:

- "According to..." prompting (Weller et al., 2023) — <https://arxiv.org/pdf/2305.13252>
- Reducing False Positives in Static Bug Detection with LLMs — <https://arxiv.org/html/2601.18844v1>
- HalluJudge: hallucination detection in code review — <https://arxiv.org/pdf/2601.19072>
- Grounded AI for Code Review — <https://arxiv.org/pdf/2510.10290>

Could not access (cited from secondary summaries only):
OpenAI prompt-engineering guide, OpenAI GPT-5 prompting cookbook, several
vendor blogs (CodeRabbit, Greptile, LangChain articles) returned 403 on
direct fetch. Where this matters I have noted "(summary)" in the citation.
