# PR Guardian Review System — Deep Analysis

## What You're Doing Right (Don't Break These)

Your architecture is actually solid. You've got the bones:

- **Generator-critic pattern** with the validator agent — this is the industry standard now
- **Risk-based triage** that skips agents on trivial PRs — saves cost and reduces noise
- **Trust tier system** — path-based deterministic classification is smart
- **Severity floor filtering** — suppressing low-value findings per risk tier
- **Evidence basis model** on findings (`saw_full_context`, `pattern_match`, `cwe_id`) — most tools don't have this
- **Token-budgeted context** with priority ranking — security surface first

You're ahead of most OSS review tools. But you're behind CodeRabbit and Greptile in several critical areas.

---

## The Five Critical Gaps

### 1. No Verification Before Posting (The Biggest One)

CodeRabbit's killer feature: before posting a finding, their agent **generates a verification script** (grep, ast-grep, Python) to check whether the assumption is actually true. They only post substantiated findings.

Your agents analyze the diff and produce findings. Nobody checks if the finding is *actually true in the code*. The validator challenges findings on paper, but it can't verify "does this function actually get called with untrusted input?" because it doesn't have tool access.

**The fix isn't just prompts — it's architecture.** Two options:

- **Option A (lighter):** Give the validator agent access to the full file content (not just the diff) and explicitly tell it to verify line references, function names, and assumptions against the actual code
- **Option B (heavier, better):** Add a verification step between agents and validator where findings are checked programmatically — does the file exist? Is the line number in the diff? Does the referenced function/variable exist? Can we grep for callers?

### 2. Prompts Tell Agents What to Find, Not What to Ignore

This is the #1 noise generator. Every prompt is a checklist of things to look for. None of them say **"DO NOT flag these things."** Research is crystal clear: **explicit exclusions prevent more noise than inclusion lists generate signal.**

Your `code_quality_observability` prompt says "Readability (confusing naming, unclear intent, magic numbers)" — that's exactly the kind of instruction that produces style nitpicks developers hate. The security prompt says "Data exposure (PII in logs)" — without context about what's a log statement vs structured telemetry, that'll fire on anything with `logger.info`.

### 3. No Few-Shot Examples

Research calls this "the most powerful technique" for prompt engineering. Zero of your prompts include examples. An agent that sees:

> "Here's a HIGH/DETECTED finding: [concrete example]. Here's something that looks similar but is NOT a finding because [reason]: [concrete non-example]"

...will perform dramatically better than one with just a checklist. The calibration between "this is a real issue" and "this looks like an issue but isn't" is where all the noise lives.

### 4. No Diff Awareness in Prompts

Your prompts don't teach agents how to read a diff. They don't explain:
- Lines with `+` are new code (the author's responsibility)
- Lines with `-` are removed code
- Lines without prefix are context (NOT the author's responsibility unless new code creates a new risk with them)

The validator knows this ("Pre-existing code: The finding is about code that existed before this PR"), but the *generator agents* don't. They're producing findings about context lines, which the validator then has to clean up. **Fix the source, not the filter.**

### 5. No Feedback Loop

Every major player (CodeRabbit, Greptile, Semgrep) has learning from feedback. When a developer dismisses a finding or tells the bot "stop flagging this pattern," it remembers. Your system has no mechanism for this. Each review starts from zero.

This doesn't need to be ML — it can be a simple "learnings" file per repo that gets injected into agent context. "In this repo, X pattern is intentional because Y."

---

## Prompt-Level Improvements (Changing Prompts IS Enough for These)

### A. Add Anti-Patterns to Every Prompt

Each agent prompt should have a **"Do NOT report"** section equal in length to its "What to Look For" section. Example for `code_quality_observability`:

```markdown
## Do NOT Report
- Naming preferences unless the name is genuinely misleading (not just "I'd name it differently")
- Missing comments on self-explanatory code
- Style differences that a linter should catch (formatting, import order, bracket style)
- "Consider using X instead of Y" when both are valid and equivalent
- Magic numbers that are obvious in context (HTTP status codes, common math, array indices)
- TODO/FIXME without a linked issue IF the code is functional and the TODO is aspirational
- Error handling that delegates to a framework's default handler (that IS error handling)
- Pre-existing patterns in context lines — only flag if new code makes them worse
```

### B. Add Calibration Examples (Few-Shot)

Each prompt should include 2-3 examples:

````markdown
## Calibration Examples

### This IS a finding (HIGH/DETECTED):
```diff
+ user_input = request.params["query"]
+ cursor.execute(f"SELECT * FROM users WHERE name = '{user_input}'")
```
SQL injection via string interpolation with user input. CWE-89. Concrete, exploitable, in changed code.

### This is NOT a finding:
```diff
  logger.info(f"Processing request for user {user.id}")
+ result = service.process(user)
```
Logging a user ID is not PII exposure — IDs are not PII unless the system defines them as such. The new code (process call) is unrelated to the log line.
````

### C. Add Diff-Reading Instructions to All Agent Prompts

```markdown
## Reading the Diff
- Lines starting with `+` are NEW code added by this PR — this is your review surface
- Lines starting with `-` are REMOVED code — note removals but don't flag issues in deleted code
- Lines without a prefix are CONTEXT — they exist to help you understand the change. Do NOT flag issues in context lines unless the new code creates a NEW risk with them
- If you're unsure whether code is new or existing, err on the side of NOT flagging
```

### D. Tighten the Certainty Model

Your current certainty definitions are vague. Tighten them:

```markdown
## Certainty Rules — Be Precise
- **DETECTED**: You can cite a specific CWE, point to a concrete exploit path, or identify a definitive bug. You would bet your reputation on this.
- **SUSPECTED**: The code pattern is known-problematic but you need more context to confirm. You would flag this in a human review.
- **UNCERTAIN**: DO NOT USE THIS. If you can't articulate a concrete concern, do not create a finding. Uncertain findings are noise.
```

Yes — effectively **kill the UNCERTAIN certainty level**. Research and developer feedback are unanimous: hedging language ("might", "could potentially", "consider whether") is the #1 source of noise. If an agent can't be at least "suspected," it shouldn't report.

### E. Add Output Constraints

```markdown
## Output Constraints
- Maximum 5 findings per agent per review. If you have more, keep only the 5 most impactful.
- Every finding MUST reference a specific line in the diff (with + prefix)
- Every suggestion MUST be concrete enough to implement without further research
- If your suggestion is "add input validation" — specify WHAT input, WHAT validation, WHERE
- Do not use hedging language: "might", "could potentially", "consider whether" → rephrase as definitive statement or don't report
```

### F. Persona Upgrade

Your prompts say "You are a security review agent." That's weak. Research shows specific personas perform better:

```markdown
You are a senior security engineer performing a focused review of a pull request. You have 10 years of experience in application security, have triaged thousands of vulnerability reports, and have a strong bias against false positives. You know that developers lose trust in review tools that cry wolf — you would rather miss a low-impact issue than flag something that wastes a developer's time.
```

---

## Architecture-Level Improvements

### Should You Add More Agents? No.

Six review agents + a validator is already at the upper bound. More agents = more noise, more cost, more deduplication headaches. Diffray uses 11 agents but they're a cautionary tale — their "Documentation Reviewer" and "SEO Expert" are noise factories.

**What to do instead:**

### 1. Add a Pre-Validator Deduplication Step

Before the validator runs, deduplicate findings across agents. Same file + same line + overlapping description = same root cause. Pick the highest-severity version, drop the rest. This is cheap (no LLM call needed — string similarity + file/line matching).

### 2. Upgrade the Validator Architecture

The validator is your most important agent. Two improvements:

**a) Give it the actual diff, not just finding descriptions.**
Currently (from `validator.py`), it gets the findings list + truncated diff. Make sure the diff is presented in a way where the validator can easily cross-reference finding line numbers against actual changed lines.

**b) Add a "burden of proof" framework:**

```markdown
For each finding, apply this test:
1. Can I find the exact line in the diff that this finding refers to? (If no → dismiss)
2. Is that line a NEW line (+ prefix)? (If no → dismiss unless new code creates risk with it)
3. Is the issue concrete and specific? (If "might"/"could" → dismiss)
4. Would a senior engineer at this company agree this needs fixing before merge? (If uncertain → dismiss)
```

### 3. Add Repo-Level Learnings Context

Create a mechanism where the system maintains a `learnings.json` per repository:

```json
{
  "patterns_to_ignore": [
    "This repo uses raw SQL intentionally — do not flag parameterized query suggestions",
    "logger.info with user IDs is acceptable per privacy policy"
  ],
  "past_dismissals": {
    "code_quality_observability": {"magic_numbers": 12, "naming_style": 8},
    "security_privacy": {"pii_in_logs": 3}
  }
}
```

Inject this into agent context. Over time, agents learn what this specific repo considers noise.

### 4. Add Confidence Scoring

Your certainty model (detected/suspected/uncertain) is categorical. Add a numeric **confidence score (0.0-1.0)** to each finding and filter at 0.7. This gives you a knob to turn — if a repo is getting too much noise, raise the threshold to 0.8.

### 5. Context Engineering > Prompt Engineering

The biggest gap between you and CodeRabbit isn't prompts — it's **context**. They use:
- Code graph (function call chains, dependency trees)
- Past PR history (what was this file's last 5 changes?)
- Linked ticket content (what was the intent?)
- Team conventions (extracted from codebase analysis)

You have token-budgeted context with priority ranking, which is good. But you're only passing diff + some surrounding files. Consider:
- Passing the PR description/title to all agents (intent context)
- Passing file git history summary (is this a hotspot? what broke here last time?)
- Passing linked work item description if available

---

## What You DON'T Need

- **Multi-model consensus**: Too expensive for your scale. The validator is your "second opinion" — that's enough.
- **More agent types**: Six is plenty. Resist the urge to add "Documentation Reviewer" or "Naming Convention Checker."
- **RAG/vector search**: Overkill unless you're processing massive monorepos. Simple file-based learnings work fine.
- **Full repo cloning/sandboxing**: CodeRabbit does this because they run static analyzers. Your mechanical gates already handle linters.

---

## Priority Order (What to Do First)

| Priority | Change | Type | Impact |
|----------|--------|------|--------|
| 1 | Add "Do NOT report" sections to all prompts | Prompt | Highest noise reduction |
| 2 | Add diff-reading instructions to all agents | Prompt | Stops pre-existing code findings |
| 3 | Add few-shot calibration examples | Prompt | Best accuracy improvement |
| 4 | Kill UNCERTAIN certainty (or make it auto-dismiss) | Prompt + Code | Removes hedging noise |
| 5 | Add output constraints (max 5, must cite line) | Prompt | Caps noise per agent |
| 6 | Upgrade validator with burden-of-proof framework | Prompt | Better filtering |
| 7 | Add pre-validator deduplication | Code | Reduces redundancy |
| 8 | Add repo-level learnings system | Architecture | Long-term noise reduction |
| 9 | Add numeric confidence scoring | Prompt + Code | Tunable noise control |
| 10 | Upgrade persona descriptions | Prompt | Better calibration |

---

## The Bottom Line

Your architecture is sound — you don't need more agents or a fundamentally different system. What you need is:

1. **Prompts that teach restraint** (what NOT to flag, explicit anti-patterns, few-shot examples)
2. **A verification mindset** (prove it before you post it)
3. **A feedback loop** (learn from dismissed findings)
4. **Kill the hedging** (uncertain findings are noise, full stop)

The 20% rule from research: **if more than 1 in 5 comments get dismissed, you've lost the team's trust.** Everything above is aimed at getting you well under that threshold.

---

## Research Sources

- CodeRabbit architecture, context engineering, LanceDB integration docs
- Greptile graph-based codebase context documentation
- CrashOverride — "How to Prompt LLMs for Security Reviews" (five-element framework)
- Datadog — "Using LLMs to Filter False Positives in SAST"
- Academic: WirelessCar/SANER study on RAG-enhanced code review
- Diffray multi-agent architecture documentation
- baz-scm/awesome-reviewers — 8,000+ curated review prompts
- HN/Reddit developer feedback threads on AI code review tools
- Propel, CodeAnt, Graphite guides on false positive reduction
- Semgrep + Claude customer story (learnings/feedback loops)
- State of AI Code Review Tools 2025/2026 roundups
