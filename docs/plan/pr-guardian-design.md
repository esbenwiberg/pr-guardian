# PR Guardian - Automated Review & Merge Pipeline

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
┌──────────────────────────────────────────────────────────────┐
│  STAGE 1: MECHANICAL GATES (deterministic, fast, <2min)      │
│                                                              │
│  Existing:          New:                    Gap fills:        │
│  ├─ Build           ├─ Semgrep (SAST)       ├─ API breaking │
│  ├─ Tests           ├─ Gitleaks (secrets)   │  change detect│
│  └─ SonarCloud      ├─ dep-cruiser (arch)   ├─ Migration    │
│                     ├─ Socket/Snyk (SCA)    │  safety check │
│                     ├─ bundle-size/limit     ├─ Observability│
│                     ├─ fitness tests        │  fitness tests│
│                     └─ lang-specific tools   └─ PII scanner │
│                       (per-language, see                     │
│                        multi-lang section)                   │
│                                                              │
│  ANY HARD FAIL → Block PR, no agents needed                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ all pass
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 2: TRIAGE (classify PR before spending agent $)       │
│                                                              │
│  Inputs:                                                     │
│  ├─ Diff size (lines added/removed/modified)                 │
│  ├─ Files touched (count + paths)                            │
│  ├─ Languages detected (see multi-lang section)              │
│  ├─ Hotspot score (change frequency × complexity)            │
│  ├─ Security surface (auth/crypto/input handling paths)      │
│  ├─ Architecture surface (crosses module boundaries?)        │
│  ├─ Author context (human? agent? non-dev?)                  │
│  └─ Linked work item (for intent verification)               │
│                                                              │
│  Output: risk_tier = trivial | low | medium | high           │
│          agent_set = which agents to run                      │
│          language_map = languages present in diff             │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 3: AI AGENT REVIEW (parallel, specialized)            │
│                                                              │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────────┐      │
│  │  SECURITY   │ │ PERFORMANCE │ │  ARCHITECTURE     │      │
│  │  + PRIVACY  │ │ AGENT       │ │  + INTENT VERIFY  │      │
│  │  AGENT      │ │             │ │  AGENT             │      │
│  │             │ │ - O(n²)    │ │                    │      │
│  │ - Auth flow │ │ - N+1 query│ │ - Layer viol.     │      │
│  │ - Injection │ │ - Memory   │ │ - Dep direction   │      │
│  │ - Crypto    │ │ - Caching  │ │ - Pattern drift   │      │
│  │ - Authz     │ │ - Indexing │ │ - API surface     │      │
│  │ - Data exp. │ │ - Blocking │ │ - Coupling        │      │
│  │ - PII leaks │ │ - Payload  │ │ - Intent vs work  │      │
│  │ - GDPR      │ │   size     │ │   item match      │      │
│  │ - Logging   │ │            │ │                    │      │
│  └──────┬──────┘ └──────┬─────┘ └────────┬───────────┘      │
│         │               │                │                   │
│  ┌──────┴──────┐ ┌──────┴──────┐ ┌───────┴──────┐          │
│  │  CODE       │ │  HOTSPOT    │ │  TEST        │          │
│  │  QUALITY    │ │  AGENT      │ │  QUALITY     │          │
│  │  + OBSERVE  │ │             │ │  AGENT       │          │
│  │  AGENT      │ │ - Risk map  │ │              │          │
│  │             │ │ - History   │ │ - Assertion  │          │
│  │ - Readabil. │ │ - Bug freq  │ │   quality    │          │
│  │ - Naming    │ │ - Churn     │ │ - Edge cases │          │
│  │ - Complex.  │ │ - Ownership │ │ - Mock abuse │          │
│  │ - DRY       │ │             │ │ - Coverage   │          │
│  │ - Error hdl │ │             │ │   meaning    │          │
│  │ - Logging   │ │             │ │ - Test names │          │
│  │ - Tracing   │ │             │ │              │          │
│  │ - Metrics   │ │             │ │              │          │
│  └──────┬──────┘ └──────┬──────┘ └──────┬───────┘          │
│         │               │               │                   │
│         └───────────────┼───────────────┘                   │
│                         ▼                                    │
│     Each agent returns:                                      │
│     {                                                        │
│       verdict: "pass" | "warn" | "flag_human",               │
│       findings: [{                                           │
│         severity, certainty, evidence_basis, ...             │
│       }],                                                    │
│       risk_score: 0-10,                                      │
│       languages_reviewed: [...]                              │
│     }                                                        │
│                                                              │
│     certainty is an enum, not a float:                       │
│       "detected"  — concrete pattern, CWE, specific fix     │
│       "suspected" — looks problematic, needs more context    │
│       "uncertain" — area is risky, can't point to specific   │
│                     issue                                    │
│                                                              │
│     Each finding includes evidence_basis:                    │
│     {                                                        │
│       saw_full_context: bool,                                │
│       pattern_match: bool,                                   │
│       cwe_id: str | null,                                    │
│       similar_code_in_repo: bool,                            │
│       suggestion_is_concrete: bool,                          │
│       cross_references: int                                  │
│     }                                                        │
│                                                              │
│     Decision engine validates certainty against evidence —   │
│     agents can't claim "detected" without showing work.      │                                                        │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  STAGE 4: DECISION ENGINE (deterministic rules)              │
│                                                              │
│  Inputs:                                                     │
│  ├─ risk_tier from triage                                    │
│  ├─ All agent verdicts + scores                              │
│  ├─ Mechanical check results                                 │
│  └─ PR metadata (author, branch, target)                     │
│                                                              │
│  Rules:                                                      │
│                                                              │
│  AUTO-APPROVE when ALL of:                                   │
│  ├─ All mechanical gates pass                                │
│  ├─ risk_tier ∈ {trivial, low}                               │
│  ├─ repo_risk_class = standard                               │
│  ├─ No finding with certainty="detected" + severity>=medium  │
│  ├─ No finding with certainty="suspected" + severity=high    │
│  ├─ Total detected findings ≤ 2 (all low severity)           │
│  └─ All agents saw_full_context = true (silence is trusted)  │
│                                                              │
│  Note: auto-approve = vote approve + summary comment.        │
│  Author still clicks merge. No auto-merge.                   │
│                                                              │
│  HUMAN REVIEW when ANY of:                                   │
│  ├─ Any "detected" finding with severity >= medium           │
│  ├─ ≥3 "suspected" findings at any severity                  │
│  ├─ risk_tier = high                                         │
│  ├─ repo_risk_class ∈ {elevated, critical}                   │
│  ├─ Any agent verdict = "flag_human"                         │
│  ├─ Any agent saw_full_context = false (can't trust silence) │
│  ├─ Intent verification failed (PR doesn't match work item)  │
│  └─ Targets protected branch AND risk_tier != trivial        │
│                                                              │
│  Output: { decision, summary, agent_reports }                │
└──────────────────────┬───────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
     ┌──────────────┐  ┌───────────────┐
     │ AUTO-APPROVE │  │ HUMAN REVIEW  │
     │              │  │               │
     │ - Approve PR │  │ - Tag reviewer│
     │ - Add summary│  │ - Add summary │
     │ - Notify     │  │ - Agent report│
     │ - Author     │  │ - Risk brief  │
     │   merges     │  │ - Focus areas │
     │ - Log to     │  │ - Log to      │
     │   feedback   │  │   feedback    │
     │   store      │  │   store       │
     └──────────────┘  └───────────────┘


═══════════════════════════════════════════════════════════════
  COMPLEMENTARY SYSTEMS (not per-PR, runs alongside)
═══════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────┐
│  CODEBASE HEALTH DASHBOARD (scheduled: weekly/monthly)       │
│                                                              │
│  ├─ Complexity trends over time                              │
│  ├─ Test coverage trends (is it drifting down?)              │
│  ├─ Dependency freshness (stale deps accumulating?)          │
│  ├─ Hotspot evolution (are hotspots getting worse?)          │
│  ├─ Architecture boundary violations trend                   │
│  ├─ Duplication trends                                       │
│  └─ Mutation testing scores (scheduled, not per-PR)          │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  FEEDBACK LOOP (continuous)                                  │
│                                                              │
│  ├─ Log every PR decision (agent verdicts + human outcome)   │
│  ├─ Track human overrides (approved what agents flagged,     │
│  │   rejected what agents passed)                            │
│  ├─ Weekly disagreement analysis                             │
│  ├─ Per-repo false positive tracking                         │
│  ├─ Prompt refinement pipeline (feed overrides → improve)    │
│  └─ Threshold auto-tuning recommendations                    │
└──────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Mechanical Gates (Detail)

### Already Running
| Check | Tool | Gate Type |
|-------|------|-----------|
| Build compiles | CI pipeline | Hard fail |
| Tests pass | CI pipeline | Hard fail |
| SonarCloud | Quality gate | Configurable |

### New Mechanical Checks

#### 1.1 Security SAST — Semgrep
```yaml
# Runs custom + community rulesets
# Hard-fail on HIGH severity, warn on MEDIUM
rules:
  - p/owasp-top-ten
  - p/cwe-top-25
  - custom/our-auth-patterns
  - custom/our-input-validation
```
- **Catches**: SQL injection, XSS, path traversal, insecure deserialization, hardcoded secrets patterns
- **Gate**: BLOCK on high-severity, WARN on medium
- **Time**: ~30s for most repos

#### 1.2 Secret Detection — Gitleaks
```yaml
# Pre-commit AND CI
# Scans diff only (not full history on every PR)
# Hard-fail on any detected secret
```
- **Catches**: API keys, passwords, tokens, connection strings, private keys
- **Gate**: HARD BLOCK — no exceptions
- **Time**: ~5s

#### 1.3 Supply Chain — Socket.dev or Snyk
```yaml
# Only runs when package.json / requirements.txt / *.csproj changes
# Flags: known CVEs, typosquatting, install scripts, excessive permissions
```
- **Catches**: Vulnerable deps, malicious packages, AI-hallucinated package names
- **Gate**: BLOCK on critical CVE, WARN on medium
- **Time**: ~15s

#### 1.4 API Breaking Change Detection (warn-only)
```yaml
# Runs when OpenAPI/Swagger specs, protobuf, or GraphQL schemas change
# Compares schema between PR branch and target branch
tools:
  openapi: oasdiff          # REST API breaking changes
  protobuf: buf breaking     # gRPC breaking changes
  graphql: graphql-inspector # GraphQL schema breaking changes
```
- **Catches**: Removed fields, changed types, removed endpoints, renamed parameters, changed required/optional status
- **Gate**: WARN on breaking changes (informational — does not block)
- **Time**: ~10s
- **Example catches**:
  - Response field `user.name` renamed to `user.fullName` → breaks all consumers
  - Query parameter changed from optional to required → breaks existing callers
  - Enum value removed → breaks clients that send that value

#### 1.5 PII / Data Classification Scanner
```yaml
# Scans for personally identifiable information in logs, comments, test fixtures
# Semi-mechanical: regex-based patterns + some heuristics
patterns:
  - log.*(email|password|ssn|social.security|credit.card|phone.number)
  - console.log.*(user\.|customer\.|patient\.)
  - logger.info.*(name|address|birth|gender)
  - test fixtures with real-looking PII (email patterns, phone patterns)
# Also scans new database columns for PII-like names without encryption annotations
```
- **Catches**: PII in logs, hardcoded real data in test fixtures, new PII storage without encryption/classification
- **Gate**: BLOCK on password/SSN/credit card exposure, WARN on other PII patterns
- **Time**: ~10s

---

## Multi-Language Support

### The Reality

A single PR can touch:
- Python backend (FastAPI, Django)
- TypeScript/JavaScript frontend (React, Angular)
- C# services (.NET)
- SQL migrations
- Terraform / Bicep infrastructure
- Dockerfiles
- YAML pipelines
- Shell scripts
- Go microservices

Every stage must handle this. The design principle: **detect languages from the diff, then adapt each stage to the languages present.**

### Language Detection (runs in Triage, feeds everything)

```
Triage step 1: parse diff → group files by language

Input:  git diff --name-only target..HEAD
Output: LanguageMap

{
  "languages": {
    "python":     ["src/api/users.py", "src/services/billing.py", "tests/test_billing.py"],
    "typescript": ["frontend/src/components/Dashboard.tsx", "frontend/src/hooks/useAuth.ts"],
    "sql":        ["migrations/V042__add_audit_columns.sql"],
    "terraform":  ["infra/modules/api-gateway/main.tf"],
    "csharp":     ["Services/NotificationService/Handlers/EmailHandler.cs"],
    "dockerfile": ["Dockerfile"],
    "yaml":       ["azure-pipelines.yml"]
  },
  "primary_language": "python",        # most lines changed
  "language_count": 7,
  "cross_stack": true                   # touches >1 runtime language
}
```

Detection is based on file extension mapping — dead simple, no heuristics:

```python
LANG_MAP = {
    ".py":    "python",
    ".ts":    "typescript",  ".tsx":   "typescript",
    ".js":    "javascript",  ".jsx":   "javascript",
    ".cs":    "csharp",
    ".go":    "go",
    ".java":  "java",        ".kt":    "kotlin",
    ".rs":    "rust",
    ".sql":   "sql",
    ".tf":    "terraform",   ".bicep": "bicep",
    ".sh":    "shell",       ".bash":  "shell",
    ".ps1":   "powershell",
    ".yaml":  "yaml",        ".yml":   "yaml",
    ".json":  "json",
    ".xml":   "xml",         ".csproj":"xml",
    ".md":    "markdown",
    # Dockerfile has no extension — match by filename
}
```

### How Language Affects Each Stage

#### Stage 1: Mechanical Gates — Language-Conditional Tools

Not every tool runs on every PR. The triage output's `languages` map determines which mechanical checks fire:

```
┌──────────────┬────────────────────────────────────────────────┐
│ Language     │ Mechanical Tools to Run                        │
├──────────────┼────────────────────────────────────────────────┤
│ python       │ semgrep (python rules), ruff, mypy/pyright,   │
│              │ bandit, deptry, pip-audit                      │
├──────────────┼────────────────────────────────────────────────┤
│ typescript   │ semgrep (ts rules), biome/eslint,             │
│              │ tsc --noEmit, dependency-cruiser, npm audit    │
├──────────────┼────────────────────────────────────────────────┤
│ javascript   │ same as typescript (minus type check)          │
├──────────────┼────────────────────────────────────────────────┤
│ csharp       │ semgrep (csharp rules), dotnet build,         │
│              │ dotnet format --verify, security-scan          │
├──────────────┼────────────────────────────────────────────────┤
│ go           │ semgrep (go rules), golangci-lint, govulncheck│
├──────────────┼────────────────────────────────────────────────┤
│ java/kotlin  │ semgrep (java rules), spotbugs, checkstyle    │
├──────────────┼────────────────────────────────────────────────┤
│ sql          │ sqlfluff (lint + format), semgrep SQL rules    │
├──────────────┼────────────────────────────────────────────────┤
│ terraform    │ tflint, checkov, trivy config                  │
├──────────────┼────────────────────────────────────────────────┤
│ bicep        │ bicep build (validate), checkov               │
├──────────────┼────────────────────────────────────────────────┤
│ dockerfile   │ hadolint, trivy config                        │
├──────────────┼────────────────────────────────────────────────┤
│ shell        │ shellcheck                                     │
├──────────────┼────────────────────────────────────────────────┤
│ yaml         │ yamllint (if not pipeline yaml)               │
├──────────────┼────────────────────────────────────────────────┤
│ ALL (always) │ gitleaks, semgrep (universal rules)           │
└──────────────┴────────────────────────────────────────────────┘
```

Pipeline implementation — conditional jobs per language:

```yaml
# Stage 1 becomes dynamic based on detected languages
jobs:
  - job: DetectLanguages
    steps:
      - script: |
          pr-guardian detect-languages \
            --diff-target=$(System.PullRequest.TargetBranch) \
            --output=languages.json
      - script: |
          LANGS=$(jq -r '.languages | keys | join(",")' languages.json)
          echo "##vso[task.setvariable variable=LANGUAGES;isOutput=true]$LANGS"
        name: detect

  - job: PythonChecks
    dependsOn: DetectLanguages
    condition: contains(dependencies.DetectLanguages.outputs['detect.LANGUAGES'], 'python')
    steps:
      - script: pip install ruff bandit deptry pip-audit
      - script: ruff check --output-format=json > ruff-results.json
      - script: bandit -r src/ -f json -o bandit-results.json
      - script: pip-audit --format=json > pip-audit-results.json

  - job: TypeScriptChecks
    dependsOn: DetectLanguages
    condition: contains(dependencies.DetectLanguages.outputs['detect.LANGUAGES'], 'typescript')
    steps:
      - script: npm ci
      - script: npx tsc --noEmit 2>&1 | tee tsc-results.txt
      - script: npx biome check --reporter=json > biome-results.json
      - script: npx dependency-cruiser --validate .dependency-cruiser.json src/

  - job: CSharpChecks
    dependsOn: DetectLanguages
    condition: contains(dependencies.DetectLanguages.outputs['detect.LANGUAGES'], 'csharp')
    steps:
      - script: dotnet build --no-restore
      - script: dotnet format --verify-no-changes

  - job: TerraformChecks
    dependsOn: DetectLanguages
    condition: contains(dependencies.DetectLanguages.outputs['detect.LANGUAGES'], 'terraform')
    steps:
      - script: tflint --format=json > tflint-results.json
      - script: checkov -d infra/ -o json > checkov-results.json

  - job: SQLChecks
    dependsOn: DetectLanguages
    condition: contains(dependencies.DetectLanguages.outputs['detect.LANGUAGES'], 'sql')
    steps:
      - script: sqlfluff lint migrations/ --format=json > sqlfluff-results.json

  - job: UniversalChecks    # always runs
    steps:
      - script: gitleaks detect ...
      - script: semgrep --config=auto ...   # semgrep auto-detects languages
```

#### Stage 2: Triage — Language-Aware Risk Scoring

Language mix affects risk tier:

```
Risk amplifiers:
  - cross_stack = true (>1 runtime language) → bump risk tier one level
  - sql present → always trigger security agent (injection risk)
  - terraform/bicep present → always trigger security agent (infra misconfiguration)
  - dockerfile present → always trigger security agent (container security)
  - language_count > 3 → bump to HIGH (too many concerns for one review)
```

#### Stage 3: Agents — Composable Language-Specific Prompts

This is where multi-language matters most. Each agent's system prompt is **composed** from:

```
AGENT PROMPT = base_prompt + Σ(language_section for each language in PR)
```

```
prompts/
├── security/
│   ├── base.md              # universal security concerns
│   ├── python.md            # SQLAlchemy injection, pickle, eval, subprocess
│   ├── typescript.md        # XSS, prototype pollution, RegExp DoS
│   ├── csharp.md            # EF Core injection, XML external entities
│   ├── sql.md               # direct injection, privilege escalation
│   ├── terraform.md         # public S3 buckets, open security groups
│   ├── go.md                # template injection, path traversal
│   └── dockerfile.md        # running as root, secrets in layers
│
├── performance/
│   ├── base.md              # universal performance concerns
│   ├── python.md            # GIL, N+1 with SQLAlchemy, sync in async
│   ├── typescript.md        # bundle size, re-renders, memory leaks
│   ├── csharp.md            # EF Core N+1, async/await deadlocks
│   ├── sql.md               # missing indexes, full table scans, cartesian joins
│   └── go.md                # goroutine leaks, unbuffered channels
│
├── architecture/
│   ├── base.md              # universal architecture concerns
│   ├── python.md            # Django/FastAPI patterns, circular imports
│   ├── typescript.md        # React patterns, module boundaries
│   ├── csharp.md            # .NET patterns, dependency injection
│   └── go.md                # package layout, interface patterns
│
├── code_quality/
│   ├── base.md              # universal quality concerns
│   ├── python.md            # Pythonic patterns, type hints
│   ├── typescript.md        # TS idioms, strict mode compliance
│   ├── csharp.md            # .NET conventions, async patterns
│   └── go.md                # Go idioms, error handling
│
└── hotspot/
    └── base.md              # language-agnostic (git history based)
```

**Prompt composition at runtime:**

```python
def build_agent_prompt(agent_type: str, languages: list[str]) -> str:
    """Compose system prompt from base + language-specific sections."""
    parts = []

    # Always include base
    parts.append(load_prompt(f"{agent_type}/base.md"))

    # Add language-specific sections for each language in the PR
    for lang in languages:
        lang_prompt = load_prompt(f"{agent_type}/{lang}.md")
        if lang_prompt:
            parts.append(f"\n## {lang.upper()}-SPECIFIC REVIEW\n{lang_prompt}")

    # If multi-language, add cross-cutting concerns
    if len(languages) > 1:
        parts.append(CROSS_LANGUAGE_SECTION)

    return "\n\n---\n\n".join(parts)


# Cross-language section — added when PR spans multiple languages
CROSS_LANGUAGE_SECTION = """
## CROSS-LANGUAGE CONCERNS
This PR spans multiple languages/stacks. Pay special attention to:
- Data contracts between layers (API request/response shapes match frontend types?)
- Shared constants/enums that must stay in sync across languages
- Migration + code changes that must deploy atomically
- Error handling across language boundaries (does the frontend handle all backend error shapes?)
- Authentication/authorization applied consistently across all endpoints regardless of language
"""
```

#### Stage 3: Agents — Language-Aware Context Building

The context builder also adapts per language. Each agent gets:

```python
def build_review_context(pr_diff: Diff, languages: LanguageMap, agent_type: str) -> str:
    """Build context document that the agent receives alongside the diff."""
    sections = []

    # 1. The diff itself (always)
    sections.append(format_diff(pr_diff))

    # 2. Project-level context
    sections.append(load_file_if_exists("CLAUDE.md"))
    sections.append(load_file_if_exists("docs/architecture.md"))

    # 3. Per-language: pull relevant patterns from the codebase
    for lang, files in languages.items():
        if lang == "python" and agent_type == "security":
            # Show existing auth patterns so agent knows what's expected
            sections.append(sample_files("**/auth/**/*.py", max_files=3))
            sections.append(sample_files("**/middleware/*.py", max_files=2))

        elif lang == "typescript" and agent_type == "architecture":
            # Show module structure so agent can check boundaries
            sections.append(get_directory_tree("frontend/src/", depth=3))
            sections.append(load_file_if_exists("frontend/tsconfig.json"))

        elif lang == "csharp" and agent_type == "performance":
            # Show existing EF Core patterns
            sections.append(sample_files("**/DbContext.cs", max_files=1))

        elif lang == "sql" and agent_type == "security":
            # Show existing migration patterns
            sections.append(sample_files("migrations/*.sql", max_files=2, newest=True))

        elif lang == "terraform" and agent_type == "security":
            # Show existing infra patterns
            sections.append(sample_files("infra/**/*.tf", max_files=3))

    # 4. Per-language: security surface map
    if agent_type == "security":
        for lang in languages:
            sections.append(get_security_surface(lang))

    return "\n\n".join(sections)
```

#### Agent Output — Per-Language Findings

Agent results now tag each finding with the language, so the decision engine and PR comments can group them:

```json
{
  "verdict": "warn",
  "risk_score": 4,
  "languages_reviewed": ["python", "typescript", "sql"],
  "findings": [
    {
      "severity": "high",
      "certainty": "detected",
      "category": "injection",
      "language": "python",
      "file": "src/api/search.py",
      "line": 67,
      "description": "f-string used in SQL query — use parameterized query instead",
      "suggestion": "Use parameterized query: db.execute('SELECT * FROM items WHERE name = %s', [search_term])",
      "cwe": "CWE-89",
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": "CWE-89",
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    },
    {
      "severity": "medium",
      "certainty": "detected",
      "category": "xss",
      "language": "typescript",
      "file": "frontend/src/components/Comment.tsx",
      "line": 23,
      "description": "dangerouslySetInnerHTML with user content — sanitize with DOMPurify",
      "suggestion": "Wrap content in DOMPurify.sanitize() before rendering",
      "cwe": "CWE-79",
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": "CWE-79",
        "similar_code_in_repo": true,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    },
    {
      "severity": "low",
      "certainty": "suspected",
      "category": "migration_safety",
      "language": "sql",
      "file": "migrations/V042__add_audit_columns.sql",
      "line": 1,
      "description": "ALTER TABLE ADD COLUMN on large table without CONCURRENTLY — may lock table",
      "suggestion": "Use ALTER TABLE ... ADD COLUMN ... CONCURRENTLY if table has >100K rows",
      "cwe": null,
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": null,
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 0
      }
    }
  ],
  "cross_language_findings": [
    {
      "severity": "medium",
      "certainty": "suspected",
      "category": "contract_mismatch",
      "files": ["src/api/search.py:45", "frontend/src/hooks/useSearch.ts:12"],
      "description": "Backend returns 'results' array with 'score' field, but frontend type expects 'relevance' — field name mismatch will cause undefined at runtime",
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": false,
        "cwe_id": null,
        "similar_code_in_repo": false,
        "suggestion_is_concrete": false,
        "cross_references": 2
      }
    }
  ]
}
```

### Updated Package Structure (Language Support)

```
pr-guardian/
├── src/
│   └── pr_guardian/
│       ├── languages/               # NEW: language detection + registry
│       │   ├── __init__.py
│       │   ├── detector.py          # file extension → language mapping
│       │   ├── registry.py          # which tools/rules per language
│       │   └── tool_configs/        # language-specific tool configs
│       │       ├── python.yml       # ruff, bandit, deptry settings
│       │       ├── typescript.yml   # biome, dep-cruiser settings
│       │       ├── csharp.yml       # dotnet format settings
│       │       ├── go.yml           # golangci-lint settings
│       │       ├── sql.yml          # sqlfluff settings
│       │       ├── terraform.yml    # tflint, checkov settings
│       │       └── dockerfile.yml   # hadolint settings
│       │
│       ├── agents/
│       │   ├── base.py
│       │   ├── prompt_composer.py   # NEW: assembles base + language prompts
│       │   ├── context_builder.py   # UPDATED: language-aware context
│       │   ├── security.py
│       │   ├── performance.py
│       │   ├── architecture.py
│       │   ├── code_quality.py
│       │   └── hotspot.py
│       │
│       └── ...
│
├── prompts/                         # RESTRUCTURED: per-agent, per-language
│   ├── security/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   ├── csharp.md
│   │   ├── go.md
│   │   ├── sql.md
│   │   ├── terraform.md
│   │   └── dockerfile.md
│   ├── performance/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   ├── csharp.md
│   │   ├── sql.md
│   │   └── go.md
│   ├── architecture/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   ├── csharp.md
│   │   └── go.md
│   ├── code_quality/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   ├── csharp.md
│   │   └── go.md
│   ├── hotspot/
│   │   └── base.md
│   └── cross_language.md            # NEW: cross-stack concerns
│
└── ...
```

### Per-Repo Language Configuration

```yaml
# review.yml
repo_risk_class: standard   # standard | elevated | critical

languages:
  # Which languages are expected in this repo (optional — auto-detected if omitted)
  expected: [python, typescript, sql]

  # Per-language overrides
  python:
    mechanical:
      ruff: true
      bandit: true
      mypy: false          # this repo doesn't use mypy yet
    security_surface:
      critical: ["**/auth/**", "**/crypto/**"]
      input:    ["**/api/**", "**/views/**"]

  typescript:
    mechanical:
      biome: true
      dependency_cruiser: true
    security_surface:
      critical: ["**/middleware/auth*"]
      input:    ["**/components/**"]

  terraform:
    mechanical:
      checkov: true
      tflint: true
    # terraform changes always trigger security agent
    always_trigger: [security]

  # Adding a new language is just config — no code changes
  # go:
  #   mechanical:
  #     golangci_lint: true
  #     govulncheck: true
```

---

## Stage 2: Triage Engine (Detail)

The triage engine is a **deterministic script** (not AI) that classifies the PR risk and decides which agents to invoke. This keeps costs down — a 1-line typo fix shouldn't trigger 5 agent calls.

### Risk Tier Classification

```
TRIVIAL (skip agents entirely):
  - Only docs/comments/markdown changed
  - Only config files with <5 lines changed
  - Only test files changed (and tests pass)
  - Only auto-generated files (migrations, lockfiles)

LOW (run: code-quality agent only):
  - <50 lines changed
  - Single module touched
  - No security-surface files
  - No architecture-boundary files
  - Not a hotspot file

MEDIUM (run: code-quality + architecture + conditional):
  - 50-300 lines changed
  - 1-5 modules touched
  - + security agent IF auth/crypto/input files touched
  - + performance agent IF db/query/api files touched
  - + hotspot agent IF known high-churn files touched

HIGH (run: all agents):
  - >300 lines changed
  - >5 modules touched
  - OR touches security-critical paths
  - OR crosses architecture boundaries
  - OR new dependencies added
  - OR new API endpoints added
  - OR author is non-dev / new contributor

LANGUAGE-BASED AMPLIFIERS:
  - cross_stack = true (>1 runtime language) → bump tier +1
  - language_count > 3 → force HIGH
  - sql changes present → always add security agent
  - terraform/bicep/dockerfile present → always add security agent
  - new language introduced to repo → force HIGH + flag_human

REPO RISK CLASS AMPLIFIERS:
  - repo_risk_class = elevated → bump tier +1 (low→medium, medium→high)
  - repo_risk_class = critical → force HIGH, all agents always run
```

### Hotspot Detection

Pre-computed on a schedule (nightly or weekly), not per-PR:

```bash
# Git-based hotspot analysis
# For each file: change_frequency × cyclomatic_complexity = hotspot_score
# Store as .pr-guardian/hotspots.json
# Files above 80th percentile are "hotspots" — get extra scrutiny
```

### Security Surface Map

Pre-computed, maps file paths to security relevance:

```json
{
  "security_critical": ["**/auth/**", "**/crypto/**", "**/middleware/auth*"],
  "input_handling": ["**/controllers/**", "**/api/**", "**/handlers/**"],
  "data_access": ["**/repositories/**", "**/models/**", "**/queries/**"],
  "configuration": ["**/config/**", "**/.env*", "**/settings*"],
  "infrastructure": ["**/terraform/**", "**/docker/**", "**/k8s/**"]
}
```

---

## Stage 3: AI Agent Specifications

Each agent is a focused LLM call (provider determined by repo config) with a specific system prompt, the PR diff as context, and relevant repo context. They run in parallel.

### 3.1 Security + Privacy Agent

**Trigger**: security-surface files touched, new deps, data model changes, HIGH tier
**Context fed to agent**:
- PR diff
- Security surface map
- Existing auth/authz patterns from codebase
- OWASP checklist relevant to the changes
- Known vulnerability patterns for the tech stack
- Data classification policy (which fields are PII, sensitive, public)
- Privacy requirements (GDPR, HIPAA, SOC2 as applicable)
- Languages detected in diff (for language-specific security prompts)

**Security Checks**:
- Authentication bypass possibilities
- Authorization gaps (can user A access user B's data?)
- Input validation completeness
- Injection vectors (SQL, command, LDAP, template)
- Cryptographic misuse (weak algorithms, hardcoded IVs)
- Data exposure (PII in logs, verbose errors, excessive API responses)
- CORS / CSRF / header configuration
- Secrets in code that Gitleaks might have missed (encoded, split)

**Privacy / Data Checks** (NEW):
- PII flowing into log statements (names, emails, IPs, device IDs)
- New data fields without classification (is this PII? sensitive? public?)
- Data sent to third-party services without noting it in data flow docs
- Missing data retention considerations (storing data with no TTL/cleanup path)
- Consent implications (new data collection that may require user consent)
- Right-to-deletion impact (new PII storage must be deletable on request)
- Cross-border data flow (data leaving region via new external API calls)
- Test fixtures with realistic PII (use synthetic data instead)

**Output format**:
```json
{
  "verdict": "pass | warn | flag_human",
  "risk_score": 3,
  "languages_reviewed": ["python", "sql"],
  "findings": [
    {
      "severity": "medium",
      "certainty": "detected",
      "category": "input_validation",
      "language": "python",
      "file": "src/api/users.py",
      "line": 42,
      "description": "User input passed to SQL query without parameterization",
      "suggestion": "Use parameterized query: db.query('SELECT * FROM users WHERE id = $1', [userId])",
      "cwe": "CWE-89",
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": "CWE-89",
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    },
    {
      "severity": "high",
      "certainty": "detected",
      "category": "pii_exposure",
      "language": "python",
      "file": "src/services/user_service.py",
      "line": 89,
      "description": "User email address logged at INFO level — PII should not appear in standard logs",
      "suggestion": "Log user ID instead of email, or use DEBUG level with PII-safe log sink",
      "compliance": "GDPR Art. 5(1)(c) — data minimization",
      "cwe": null,
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": null,
        "similar_code_in_repo": true,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    }
  ],
  "cross_language_findings": [],
  "summary": "One injection risk and one PII logging issue found."
}
```

### 3.2 Performance Agent

**Trigger**: data-access files touched, API handlers, >100 lines, HIGH tier
**Context fed to agent**:
- PR diff
- Database schema (if available)
- Existing query patterns
- Performance-sensitive file list
- Known performance patterns for the tech stack
- Languages detected in diff (for language-specific perf prompts)

**Checks**:
- Algorithmic complexity (O(n²) loops, nested iterations over collections)
- N+1 query patterns (ORM lazy loading, loop queries)
- Missing database indexes (new queries on unindexed columns)
- Unbounded queries (SELECT * without LIMIT, missing pagination)
- Memory accumulation (growing arrays in loops, no streaming for large data)
- Missing caching opportunities (repeated identical calls)
- Synchronous blocking in async contexts
- Large payload responses (no field selection, no pagination)
- Missing connection pooling or pool exhaustion risks
- Concurrency hazards (race conditions, shared mutable state, missing locks)
- Resource cleanup (unclosed connections, file handles, streams)

**Output format**: same structure as security agent

### 3.3 Architecture + Intent Verification Agent

**Trigger**: multiple modules touched, new files created, new deps, HIGH tier
**Context fed to agent**:
- PR diff
- Project CLAUDE.md / architecture docs
- Module boundary map
- Existing patterns from codebase (sampled)
- decisions.md if exists
- Languages detected in diff (for language-specific arch prompts)
- **Linked work item title + description** (from Azure DevOps, if available)
- **Recent decisions.md entries** (to check for conflicting directions)

**Architecture Checks**:
- Layer violation (business logic in controllers, DB in handlers)
- Dependency direction (feature modules importing from each other)
- Pattern drift (new code doesn't follow established patterns)
- Abstraction level (too much in one function, God classes forming)
- API surface changes (breaking changes, missing versioning)
- Module cohesion (does the change belong in this module?)
- Naming consistency
- Configuration drift (hardcoded values that should be config)
- Missing abstractions (raw HTTP calls that should use a client)

**Intent Verification Checks** (NEW):
- Does the PR accomplish what the linked work item describes?
- Does the PR introduce functionality that already exists elsewhere in the codebase?
- Does the approach conflict with recent architectural decisions?
- Is the scope appropriate? (doing more than the work item asks for → risk)
- Is the scope complete? (work item says X, Y, Z but PR only does X)

**Output format**: same structure, with added `intent_verification` section:
```json
{
  "intent_verification": {
    "work_item_id": "AB#12345",
    "work_item_title": "Add email notification for password reset",
    "alignment": "partial",
    "assessment": "PR adds notification but sends to hardcoded address instead of user's email. Work item implies user should receive the notification.",
    "scope_match": "under — work item also mentions audit logging which is missing from this PR",
    "duplicate_detection": "Similar notification logic exists in src/services/alerts.ts:45 — consider reusing NotificationService instead of duplicating"
  }
}
```

### 3.4 Code Quality + Observability Agent

**Trigger**: all tiers except TRIVIAL
**Context fed to agent**:
- PR diff
- Surrounding code context (not just the diff, but the full files)
- Project conventions from CLAUDE.md
- Existing patterns in same module
- Languages detected in diff
- Existing logging/tracing patterns in the codebase (sampled)

**Code Quality Checks**:
- Readability (confusing naming, unclear intent, magic numbers)
- Error handling (swallowed errors, missing catch, unclear error messages)
- Edge cases (null/undefined handling, empty arrays, boundary conditions)
- DRY violations (copy-paste from elsewhere in codebase)
- Dead code introduction
- TODO/FIXME/HACK without linked issue
- Documentation gaps for public APIs

**Observability Checks** (NEW):
- New API endpoints without request/response logging
- New error paths without structured error context
- New service methods without trace span creation
- Missing correlation ID propagation in new async flows
- New background jobs/workers without health check endpoints
- New external API calls without timeout + retry logging
- Missing metrics for new business operations (e.g., "user signup" should be counted)
- Error handling that loses stack traces (catch + rethrow without cause chain)

**Output format**: same structure as security agent

### 3.5 Test Quality Agent

**Trigger**: all tiers except TRIVIAL where code changes are present (not test-only PRs)
**Context fed to agent**:
- PR diff (both source and test files)
- Full content of new/modified test files
- Full content of the source files being tested
- Test coverage report if available (from Stage 1)
- Languages detected in diff
- Existing test patterns in the module

**The Problem This Solves**:
AI-generated and non-dev code often comes with tests that "pass" but don't
test anything meaningful. 100% coverage with `assert result is not None` is
worse than no tests — it gives false confidence.

**Checks**:
- **Assertion quality**: Are assertions specific? (`assertEqual(result.name, "Alice")` good, `assertIsNotNone(result)` weak)
- **Edge case coverage**: Does the test suite cover error paths, empty inputs, boundary values, null cases?
- **Mock appropriateness**: Are mocks hiding real bugs? (mocking the thing being tested is a red flag)
- **Test independence**: Do tests depend on execution order or shared mutable state?
- **Test naming**: Do test names describe the scenario and expected outcome?
- **Missing negative tests**: Only happy-path tests → warn (where are the failure scenarios?)
- **Copy-paste tests**: Test methods that are near-identical with one value changed → suggest parameterized test
- **Implementation coupling**: Tests that mirror the implementation step-by-step instead of testing behavior → brittle
- **Untested new paths**: New code branches (if/else, try/catch, switch cases) without corresponding test cases
- **Snapshot/golden file abuse**: Snapshot tests that nobody reads when they update → warn

**Output format**: same structure, with `test_quality_summary`:
```json
{
  "verdict": "warn",
  "risk_score": 5,
  "languages_reviewed": ["python"],
  "test_quality_summary": {
    "new_code_paths": 8,
    "tested_paths": 5,
    "untested_paths": ["error handler in process_payment:67", "retry logic in send_email:45", "empty cart check in checkout:23"],
    "weak_assertions": 3,
    "assertion_quality_score": 0.6,
    "overall": "Tests exist but 3 of 8 new code paths are untested, and 3 assertions are trivially weak (assertIsNotNone). The retry logic has no test coverage at all."
  },
  "findings": [
    {
      "severity": "medium",
      "certainty": "detected",
      "category": "weak_assertion",
      "language": "python",
      "file": "tests/test_payment.py",
      "line": 34,
      "description": "Assertion only checks `is not None` — should verify specific payment amount, status, and recipient",
      "suggestion": "assert result.amount == Decimal('99.99')\nassert result.status == PaymentStatus.COMPLETED\nassert result.recipient_id == recipient.id",
      "cwe": null,
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": null,
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    },
    {
      "severity": "high",
      "certainty": "detected",
      "category": "untested_path",
      "language": "python",
      "file": "src/services/payment.py",
      "line": 67,
      "description": "New error handler for InsufficientFundsError has no test — this is the most likely failure mode",
      "suggestion": "Add test: test_process_payment_insufficient_funds_returns_error",
      "cwe": null,
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": false,
        "cwe_id": null,
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    }
  ]
}
```

### 3.6 Hotspot Agent

**Trigger**: any file with hotspot_score > 80th percentile touched
**Context fed to agent**:
- PR diff
- Git history for touched hotspot files (recent changes, who changed them)
- Bug history correlation (if available)
- Complexity metrics for the files
- Full file content for hotspot files

**Checks**:
- Is the change making the hotspot worse (adding complexity)?
- Is the change properly tested given the file's bug history?
- Should this file be refactored instead of extended?
- Are there related hotspot files that should have been changed too?
- Risk assessment given historical churn rate

**Output format**: same structure but includes:
```json
{
  "hotspot_context": {
    "file": "src/services/billing.ts",
    "change_frequency": "47 changes in 90 days",
    "complexity_score": 82,
    "recent_bugs": 3,
    "recommendation": "This file is high-churn, high-complexity. Consider refactoring before adding more logic."
  }
}
```

---

## Stage 4: Decision Engine (Detail)

The decision engine is deterministic — no AI. It applies rules to agent outputs.

### Certainty Validation

Agents return a `certainty` enum per finding, but the decision engine **validates it against evidence** — agents can't claim high certainty without showing their work.

```python
def validated_certainty(finding: Finding) -> str:
    """Override agent's claimed certainty based on evidence.

    Agents can't just say "detected" — they must have concrete evidence.
    The decision engine downgrades unsupported claims automatically.
    """
    evidence = finding.evidence_basis

    if finding.certainty == "detected":
        # Must have at least 2 of these to stay "detected"
        signals = [
            evidence.pattern_match and evidence.cwe_id is not None,
            evidence.suggestion_is_concrete,
            evidence.saw_full_context,
            evidence.cross_references >= 1,
        ]
        if sum(signals) < 2:
            return "suspected"  # downgrade — not enough evidence

    if finding.certainty == "suspected":
        # Must have at least 1 signal to stay "suspected"
        signals = [
            evidence.pattern_match,
            evidence.saw_full_context,
            evidence.suggestion_is_concrete,
        ]
        if sum(signals) < 1:
            return "uncertain"  # downgrade — purely speculative

    return finding.certainty
```

### Repo Risk Class

Static per-repo classification, separate from per-PR triage:

```yaml
# review.yml (per-repo)
repo_risk_class: standard   # standard | elevated | critical

# standard:  auto-approve allowed, normal thresholds
#            (internal tools, docs sites, non-customer-facing)
# elevated:  auto-approve only for trivial tier, stricter thresholds
#            (customer-facing apps, APIs)
# critical:  never auto-approve, all agents always run
#            (payment, auth, PII processing, infrastructure)
```

### Scoring Model

```
total_risk = Σ(agent_risk_score × agent_weight) / Σ(agent_weight)

Weights:
  security_privacy_agent:   3.0  (highest — security + compliance most costly)
  test_quality_agent:       2.5  (bad tests = false confidence, very dangerous)
  architecture_intent_agent:2.0  (drift is expensive long-term)
  performance_agent:        1.5
  hotspot_agent:            1.5
  code_quality_obs_agent:   1.0  (least critical for auto-approve decision)

Thresholds:
  auto_approve_max:   4.0  (combined score must be below this)
  human_review_min:   4.0  (above this, humans must review)
  hard_block:         8.0  (above this, block merge entirely)
```

### Decision Matrix

```
┌─────────────┬──────────────┬───────────────────┬──────────────────────────────┐
│ Risk Tier   │ Repo Class   │ Agent Result      │ Decision                     │
├─────────────┼──────────────┼───────────────────┼──────────────────────────────┤
│ TRIVIAL     │ standard     │ (no agents)       │ AUTO-APPROVE                 │
│ TRIVIAL     │ elevated     │ (no agents)       │ AUTO-APPROVE                 │
│ TRIVIAL     │ critical     │ (no agents)       │ HUMAN REVIEW                 │
│ LOW         │ standard     │ all pass          │ AUTO-APPROVE                 │
│ LOW         │ standard     │ warn only         │ AUTO-APPROVE + comment       │
│ LOW         │ standard     │ any flag          │ HUMAN REVIEW                 │
│ LOW         │ elevated     │ all pass          │ AUTO-APPROVE + comment       │
│ LOW         │ elevated     │ any warn/flag     │ HUMAN REVIEW                 │
│ LOW         │ critical     │ any               │ HUMAN REVIEW                 │
│ MEDIUM      │ standard     │ all pass          │ AUTO-APPROVE + comment       │
│ MEDIUM      │ standard     │ warn, score < 4   │ AUTO-APPROVE + comment       │
│ MEDIUM      │ standard     │ warn, score ≥ 4   │ HUMAN REVIEW                 │
│ MEDIUM      │ elevated     │ any               │ HUMAN REVIEW                 │
│ MEDIUM      │ critical     │ any               │ HUMAN REVIEW                 │
│ HIGH        │ any          │ any               │ HUMAN REVIEW                 │
└─────────────┴──────────────┴───────────────────┴──────────────────────────────┘

Override rules (always HUMAN REVIEW regardless of above):
  - Any finding with validated certainty = "detected" and severity >= medium
  - ≥3 findings with validated certainty = "suspected" at any severity
  - Any agent verdict = "flag_human"
  - Any agent saw_full_context = false (can't trust silence from blind agent)
  - New external dependency added
  - Author's first PR to this repo
  - Intent verification alignment = "misaligned" or "partial"
  - Test quality agent finds >50% untested new code paths
  - Privacy agent flags new PII storage or processing
```

Note: auto-approve = Guardian votes "approve" and posts a summary comment.
**The author still clicks merge.** Guardian never merges automatically.
This keeps a human in the loop for the final action while eliminating
the review bottleneck for non-dev authors.

### Auto-Approve Behavior

When auto-approving:
1. Add PR comment with full summary:
   - Which checks ran and their status
   - Which agents ran and their verdicts
   - Certainty-validated findings (if any, all low severity)
   - Risk tier, repo risk class, and score
2. Vote approve on the PR
3. Post notification (Teams/Slack) — "PR approved by Guardian, ready to merge"
4. Author clicks merge when ready

### Human Review Behavior

When escalating:
1. Add PR comment with:
   - Why it was escalated (which finding triggered, which rule fired)
   - Full agent reports as collapsible sections
   - Certainty-validated findings grouped by severity
   - Specific areas the human should focus on
   - Risk tier, repo risk class, and score breakdown
2. Tag appropriate reviewer(s) based on:
   - File ownership (CODEOWNERS or git blame)
   - Security team if security agent flagged
   - Architecture owner if architecture agent flagged
3. Set PR label: `needs-human-review` with reason tag

---

## Implementation Components

### What to Build

```
pr-guardian/
├── mechanical/                  # Stage 1: deterministic checks
│   ├── semgrep-rules/          # Custom Semgrep rules
│   │   ├── auth-patterns.yml
│   │   ├── input-validation.yml
│   │   ├── pii-exposure.yml    # PII in logs/output patterns
│   │   └── our-antipatterns.yml
│   ├── architecture/           # Architecture enforcement
│   │   ├── .dependency-cruiser.json
│   │   ├── fitness-tests/      # Layer rules, observability requirements
│   │   └── observability-tests/# Logging/tracing fitness tests
│   ├── api-contracts/          # API breaking change detection
│   │   ├── oasdiff-config.yml
│   │   └── buf.yaml            # protobuf breaking changes
│   ├── migrations/             # Database migration safety
│   │   ├── squawk.toml         # PostgreSQL migration linter config
│   │   └── rules.yml           # Custom migration rules
│   ├── gitleaks.toml           # Secret detection config
│   ├── pii-scanner.yml         # PII pattern definitions
│   └── runner.py               # Mechanical check orchestrator
│
├── triage/                      # Stage 2: PR classification
│   ├── classifier.py           # Risk tier classification
│   ├── hotspot-analyzer.py     # Git history analysis
│   ├── security-surface.json   # File-to-risk mapping
│   ├── work-item-linker.py     # Fetch linked ADO work item details
│   └── scorer.py               # Triage scoring engine
│
├── agents/                      # Stage 3: AI review agents
│   ├── base-agent.py           # Shared agent framework
│   ├── security-privacy.py     # Security + privacy/GDPR agent
│   ├── performance-agent.py    # Performance + concurrency agent
│   ├── architecture-intent.py  # Architecture + intent verification agent
│   ├── code-quality-obs.py     # Code quality + observability agent
│   ├── test-quality.py         # Test quality + coverage meaning agent
│   ├── hotspot-agent.py        # Hotspot risk agent
│   └── orchestrator.py         # Parallel agent runner
│
├── decision/                    # Stage 4: merge decision
│   ├── engine.py               # Scoring + rules
│   ├── actions.py              # Auto-approve / escalate
│   ├── notifier.py             # Teams/Slack notifications
│   ├── feedback-logger.py      # Log decisions for feedback loop
│   └── reporter.py             # Decision reporting + notification
│
├── config/                      # Per-repo overrides
│   ├── defaults.yml            # Default thresholds & weights
│   ├── data-classification.yml # PII / sensitive field definitions
│   └── repo-overrides/         # Per-repo config
│       ├── api-service.yml
│       └── frontend-app.yml
│
├── dashboard/                   # Reporting & feedback loop
│   ├── metrics-collector.py    # Track decisions over time
│   ├── report-generator.py     # Weekly review stats
│   ├── override-analyzer.py    # Analyze human vs agent disagreements
│   ├── threshold-tuner.py      # Recommend threshold adjustments
│   └── health-check.py         # Codebase health trends (scheduled)
│
└── shared/                      # Shared utilities
    ├── diff-parser.py          # Parse git diffs
    ├── context-builder.py      # Build agent context from repo
    ├── ado-client.py           # Azure DevOps REST API client
    └── output-schema.py        # Standardized agent output
```

### Agent Orchestration

```python
# Pseudocode for agent orchestration
async def run_agents(pr_context, risk_tier, agent_set):
    """Run selected agents in parallel, collect results."""

    agents = select_agents(risk_tier, agent_set)

    # Build context once, share across agents
    context = await build_context(pr_context)

    # Run all agents in parallel
    results = await asyncio.gather(*[
        agent.review(context) for agent in agents
    ])

    # Validate all outputs conform to schema
    validated = [validate_output(r) for r in results]

    return validated
```

### Per-Repo Configuration

```yaml
# config/defaults.yml

repo_risk_class: standard   # standard | elevated | critical
                             # standard:  auto-approve allowed, normal thresholds
                             # elevated:  auto-approve only for trivial, stricter
                             # critical:  never auto-approve, all agents always run

thresholds:
  auto_approve_max_score: 4.0
  human_review_min_score: 4.0
  hard_block_score: 8.0

weights:
  security_privacy: 3.0
  test_quality: 2.5
  architecture_intent: 2.0
  performance: 1.5
  hotspot: 1.5
  code_quality_observability: 1.0

certainty_validation:
  detected_min_signals: 2    # min evidence signals to keep "detected"
  suspected_min_signals: 1   # min evidence signals to keep "suspected"

triage:
  trivial_max_lines: 10
  low_max_lines: 50
  medium_max_lines: 300
  # above 300 = HIGH

auto_approve:
  enabled: true
  allowed_target_branches: ["develop", "feature/*"]
  blocked_target_branches: ["main", "master", "release/*"]
  require_all_checks_pass: true
  # Note: auto-approve = vote approve + comment. Author still merges.

agents:
  # LLM config inherited from llm: section (see LLM Provider Abstraction)
  # Can be overridden per-repo in review.yml
  max_context_tokens: 32000
  timeout_seconds: 120

intent_verification:
  enabled: true
  work_item_source: azure-devops    # where to fetch linked work items
  require_linked_work_item: false   # if true, unlinked PRs always go to human

privacy:
  data_classification_file: "data-classification.yml"  # PII field definitions
  compliance_frameworks: ["gdpr"]   # which compliance rules to check
  # compliance_frameworks: ["gdpr", "hipaa", "sox"]  # add as needed

test_quality:
  min_assertion_quality_score: 0.5  # below this → flag for human
  max_untested_path_ratio: 0.5     # >50% untested new paths → flag

feedback:
  enabled: true
  log_all_decisions: true
  override_tracking: true
  weekly_report: true

# Repo-specific override example:
# config/repo-overrides/payment-service.yml
# overrides:
#   repo_risk_class: critical         # Never auto-approve payment code
#   weights.security_privacy: 5.0     # Extra security weight
#   privacy.compliance_frameworks: ["gdpr", "pci-dss"]
#   triage.always_high: true          # Always run all agents
```

---

## Complementary Systems (Non-Per-PR)

These systems run alongside the per-PR pipeline but on different schedules.
They catch chronic issues that no individual PR review can detect.

### Codebase Health Dashboard

**Schedule**: Weekly automated run + monthly deep analysis
**Purpose**: Detect slow-moving degradation that passes PR review one commit at a time

**Metrics tracked over time**:
- **Complexity trends**: Average cyclomatic complexity per module — is it creeping up?
- **Test coverage trends**: Is coverage drifting down? Which modules are losing coverage?
- **Dependency freshness**: How many deps are >6 months behind latest? >1 year?
- **Hotspot evolution**: Are the same files still hotspots? Are new ones forming?
- **Architecture boundary violations**: Are they increasing over time?
- **Duplication trends**: Is copy-paste code growing?
- **Code ownership concentration**: Is knowledge concentrated in too few people/areas?
- **Tech debt ratio**: SonarCloud's calculated metric over time

**Implementation**:
```yaml
# Scheduled pipeline — runs weekly on Sunday night
schedules:
  - cron: "0 2 * * 0"    # 2 AM Sunday
    branches: [main, develop]

steps:
  - script: pr-guardian health-check --output=health-report.json
  - script: pr-guardian health-report --format=html --output=health-report.html
  # Publish to Teams channel / wiki / dashboard
```

**Alerts** (threshold-based):
- Test coverage dropped >5% in 30 days → alert tech lead
- Cyclomatic complexity in any module >80th percentile → flag for refactoring sprint
- >10 dependencies with known CVEs → alert security team
- Any module with 0% code ownership (nobody changed it in 6 months) → orphan risk

### Mutation Testing (Scheduled)

**Schedule**: Nightly or weekly (too slow for per-PR)
**Purpose**: Verify that test suites actually catch bugs, not just exercise code paths

**Tools**:
- Python: `mutmut`, `cosmic-ray`
- JavaScript/TypeScript: `Stryker`
- C#: `Stryker.NET`
- Go: `go-mutesting`
- Java: `PITest`

**How it works**:
1. Inject small mutations into the code (change `>` to `>=`, remove a line, flip a boolean)
2. Run the test suite against each mutation
3. If tests still pass with the mutation → the tests don't cover that logic
4. Report mutation survival rate per module

**Integration with PR Guardian**:
- Mutation scores stored per-module in `.pr-guardian/mutation-scores.json`
- If a PR touches files in a module with low mutation score (<60%), the test quality agent gets extra context: "tests in this module are known to be weak — apply extra scrutiny"
- Health dashboard tracks mutation scores over time

### Feedback Loop

**Purpose**: The system must get better over time, not stay static at day-1 accuracy.

**What gets logged** (every PR):
```json
{
  "pr_id": 12345,
  "repo": "api-service",
  "repo_risk_class": "standard",
  "timestamp": "2026-02-28T10:30:00Z",
  "risk_tier": "medium",
  "agents_run": ["security_privacy", "code_quality_obs", "test_quality"],
  "agent_verdicts": {
    "security_privacy": { "verdict": "pass", "score": 2, "detected_count": 0, "suspected_count": 0 },
    "code_quality_obs": { "verdict": "warn", "score": 4, "detected_count": 1, "suspected_count": 2 },
    "test_quality": { "verdict": "pass", "score": 3, "detected_count": 0, "suspected_count": 1 }
  },
  "certainty_downgrades": 1,       # how many findings were downgraded by validation
  "combined_score": 3.1,
  "guardian_decision": "auto_approve",
  "human_outcome": null,           # filled in if human overrides
  "human_override": false,
  "override_reason": null,
  "post_merge_incidents": []       # filled in if bugs reported later
}
```

**Weekly analysis** (automated report):
- How many PRs auto-approved vs escalated?
- How many human overrides? (human approved what guardian blocked, or vice versa)
- Which agents are most/least accurate?
- Which repos have highest false positive rates?
- What types of findings do humans consistently dismiss? (→ tune prompts)
- What types of bugs slipped through? (→ add new checks)

**Prompt refinement process**:
1. Collect the 10 biggest disagreements of the week
2. For each: what did the agent say, what did the human do, what was the right answer?
3. Adjust agent prompts to reduce false positives / increase true positives
4. Run shadow mode for adjusted prompts for a week before deploying

**Threshold auto-tuning**:
- If auto-approve rate is <30% → thresholds too tight, agents too cautious
- If post-merge incident rate >2% → thresholds too loose, agents too lenient
- Recommend per-repo threshold adjustments based on historical data
- Always require human approval before applying threshold changes

### Feedback Storage

Feedback logs need to persist but don't need to be a database:

**Option A: Git-based** (simplest)
- Store feedback JSON in a `feedback/` directory in the pr-guardian repo
- One file per week: `feedback/2026-W09.jsonl`
- Weekly analysis script reads the JSONL files
- History naturally version-controlled

**Option B: Database** (if volume justifies it)
- PostgreSQL tables (same database Guardian already uses)
- Easier querying for dashboards
- Better for cross-repo aggregation
- Works on any deployment profile (cloud or on-prem)

Start with Option A. Move to B when you have >10 repos and >200 PRs/week.

---

## Rollout Strategy

### Phase 0: Foundation (1 week)
- Set up pr-guardian repo with package structure
- Implement language detection + triage engine (no agents yet)
- Deploy mechanical checks (semgrep, gitleaks, dep-cruiser)
- Run on 1 pilot repo, shadow mode only

### Phase 1: Shadow Mode — Mechanical Only (2 weeks)
- All new mechanical checks live on pilot repo
- Reports findings as PR comments
- Does NOT block or auto-approve
- Humans review all PRs normally
- Collect data: false positive rate for mechanical checks
- Tune semgrep rules, fitness tests based on noise level

### Phase 2: Shadow Mode — Full Pipeline (2 weeks)
- Add all 6 AI agents
- Pipeline recommends decision (would-auto-approve / would-escalate)
- Still doesn't act on it
- Humans compare their judgment to the pipeline
- Tune agent prompts based on disagreements
- Start feedback logging

### Phase 3: Auto-approve Trivial (2 weeks)
- Only TRIVIAL PRs auto-approved on standard repos (docs, config, test-only)
- Everything else still goes to humans but with agent reports
- Build trust incrementally
- Roll out to additional repos (still shadow mode on new repos)

### Phase 4: Auto-approve Low (2 weeks)
- LOW tier auto-approves on standard repos if all agents pass
- MEDIUM and HIGH still go to humans
- Monitor false negative rate closely
- Weekly feedback analysis starts

### Phase 5: Auto-approve Low + Medium (ongoing)
- MEDIUM tier auto-approves on standard repos if agents pass and score < threshold
- HIGH tier always goes to human
- Elevated repos: only trivial + low auto-approve
- Critical repos: never auto-approve
- Monitor and tune continuously
- Roll out to all repos

### Phase 6: Full Operation + Health Dashboard
- All tiers operate as designed per repo risk class
- Codebase Health Dashboard deployed
- Mutation testing running nightly
- Feedback loop fully operational
- Weekly metrics review
- Monthly threshold tuning
- Quarterly agent prompt refinement
- Evaluate cross-repo pattern sharing

---

## Metrics to Track

### Per-PR Metrics (real-time)
| Metric | Why |
|--------|-----|
| PRs auto-approved vs escalated (ratio) | Efficiency — are we reducing human review load? |
| Time to merge (auto-approved vs human-reviewed) | Speed — how much faster is auto-approve? |
| Agent cost per PR (tokens × price) | Cost — is it worth it? |
| Pipeline duration per stage | Speed — where are bottlenecks? |
| Certainty downgrades per PR | Calibration — are agents overclaiming? |

### Quality Metrics (weekly)
| Metric | Why |
|--------|-----|
| False negatives (bugs after auto-approve) | Safety — is the pipeline catching enough? |
| False positives (unnecessary escalations) | Noise — are humans wasting time on good PRs? |
| Agent agreement rate with humans | Consistency — calibrate agent accuracy |
| Override rate by agent | Which agents need prompt tuning? |
| Override rate by repo | Which repos need config tuning? |
| Hotspot prediction accuracy | Are flagged files actually producing bugs? |
| Intent verification accuracy | Does work item linking reduce wrong-solution PRs? |

### Trend Metrics (monthly)
| Metric | Why |
|--------|-----|
| Test mutation survival rate | Are test suites getting better or worse? |
| Architecture violation trend | Is the codebase drifting from its design? |
| Complexity trend per module | Is complexity creeping up? |
| Coverage trend per module | Is coverage drifting down? |
| Dependency freshness | Are deps being kept up to date? |
| PR author mix (dev/non-dev/agent) | How is code production shifting? |
| Review effort saved (hours estimated) | ROI — is this worth the investment? |

---

## Cost Estimation

Per PR (worst case — HIGH tier, all 6 agents):
- 6 agent calls × ~10K tokens input × ~2K tokens output
- Using Claude Sonnet (SaaS): ~$0.20-0.40 per PR
- Using Azure AI Foundry GPT-4o: ~$0.20-0.40 per PR
- Using local models (Ollama/vLLM): $0 per PR (hardware cost only)
- Average across all tiers (most are trivial/low): ~$0.06-0.12 per PR (SaaS)
- 100 PRs/week = ~$6-12/week in LLM API costs (SaaS)

Complementary systems:
- Health dashboard: 1 run/week, minimal LLM cost (~$1/week)
- Mutation testing: compute cost only (no LLM), runs on existing pipeline agents
- Feedback analysis: ~$2/week for weekly report generation

Total estimated cost: ~$40-60/month for a team doing 100 PRs/week (SaaS LLM path)

Compare to: 1 senior dev spending 30 min reviewing a PR that could have been auto-approved = ~$50/hour × 0.5h × 60 PRs saved/week = ~$6,000/month saved.

**ROI: 100-150x return on investment in review time saved.**

---

---

# Technical Architecture

## Design Principles

1. **Service-hosted** — runs as a containerized service, triggered by webhooks from ADO and GitHub
2. **Platform-agnostic** — supports Azure DevOps AND GitHub from the same service
3. **LLM-agnostic** — swap between Claude, Azure AI Foundry, Ollama, vLLM via config (per-repo)
4. **Hosting-agnostic** — same Docker image runs on Azure Container App, on-prem Docker, or any Kubernetes cluster. Application code must not import any cloud-provider hosting SDK.
5. **Zero pipeline agents consumed** — all review work runs on our infra, not CI agents
6. **Persistent** — feedback, metrics, dashboards, and learning all in one place
7. **Config-driven** — per-repo behavior without code changes

## Why Service-Hosted (Not Pipeline-Native)

The original design was pipeline-native. Three things changed that equation:

1. **Pipeline agent scarcity** — agents are already constrained. PR Guardian would consume 6-16 agent jobs per PR, competing with builds and deployments. Unacceptable.
2. **Multi-platform** — need to support both Azure DevOps and GitHub. Pipelines lock you to one platform.
3. **Persistent state** — feedback loop, dashboards, cross-repo metrics, learning — all need somewhere to live. Pipelines are stateless.
4. **Deployment flexibility** — some teams can use cloud, some must stay on-prem. A containerized service runs anywhere; pipelines lock you to one CI platform.

| Concern | Pipeline-Native | Service-Hosted |
|---------|----------------|----------------|
| Pipeline agent usage | 6-16 jobs per PR | **Zero** |
| Multi-platform (ADO + GitHub) | No — platform-locked | **Yes** — webhook adapter |
| Dashboard + metrics | Can't serve UI | **Built-in** |
| Feedback loop | Awkward (git commits) | **Native** (database) |
| Cross-repo aggregation | Very hard | **Natural** |
| Cold start | pip install per job | **Always warm** |
| Infra to manage | None (ADO handles it) | Container host (cloud or on-prem) |
| Auth complexity | Built-in ADO token | Need to manage tokens |
| Code access | Has checkout | Needs to clone/fetch diff |
| Deployment flexibility | Locked to CI platform | **Any Docker host** |

**Verdict**: Service-hosted wins given the constraints. The cost is managing infrastructure, but the benefits — zero pipeline impact, multi-platform, persistent state, deployment flexibility — are worth it.

## High-Level Architecture

```
┌──────────────────┐      ┌──────────────────┐
│  Azure DevOps    │      │  GitHub           │
│                  │      │                   │
│  PR Created /    │      │  PR Created /     │
│  Updated         │      │  Updated          │
│       │          │      │       │           │
│  Service Hook    │      │  Webhook          │
│  (webhook)       │      │  (PR event)       │
└───────┬──────────┘      └───────┬───────────┘
        │                         │
        └────────────┬────────────┘
                     │  HTTPS POST
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  PR GUARDIAN SERVICE (containerized — see Deployment Profiles)│
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  WEBHOOK RECEIVER + PLATFORM ADAPTER                   │  │
│  │                                                        │  │
│  │  ├─ ADO adapter: parse ADO webhook → normalize PR      │  │
│  │  ├─ GitHub adapter: parse GH webhook → normalize PR    │  │
│  │  └─ Output: PlatformPR (unified type)                  │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │  REVIEW ORCHESTRATOR                                   │  │
│  │                                                        │  │
│  │  1. Fetch diff (via platform API)                      │  │
│  │  2. Detect languages                                   │  │
│  │  3. Run mechanical checks (in-process, no CI needed)   │  │
│  │  4. Triage → risk tier + agent selection                │  │
│  │  5. Run AI agents (parallel, async)                    │  │
│  │  6. Decision engine → auto-approve or escalate          │  │
│  │  7. Post results back via platform API                 │  │
│  │  8. Log feedback                                       │  │
│  └───────────────────────┬───────────────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │  PLATFORM ACTIONS (writes back to ADO or GitHub)       │  │
│  │                                                        │  │
│  │  ADO:                     GitHub:                      │  │
│  │  ├─ POST /pr/threads      ├─ POST /pulls/comments     │  │
│  │  ├─ POST /pr/reviewers    ├─ POST /pulls/reviews      │  │
│  │  └─ POST /pr/labels       └─ POST /issues/labels      │  │
│  │  (No merge — author merges manually)                   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  PERSISTENT LAYER                                      │  │
│  │                                                        │  │
│  │  ├─ Feedback store (every PR decision logged)          │  │
│  │  ├─ Metrics store (agent costs, durations, scores)     │  │
│  │  ├─ Override tracking (human disagreements)            │  │
│  │  ├─ Hotspot cache (pre-computed, refreshed nightly)    │  │
│  │  ├─ Config cache (per-repo configs, prompt versions)   │  │
│  │  └─ Health snapshots (weekly codebase health data)     │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  DASHBOARD + API                                       │  │
│  │                                                        │  │
│  │  ├─ /dashboard         — review metrics, trends, ROI   │  │
│  │  ├─ /repos             — per-repo health + config      │  │
│  │  ├─ /feedback          — override analysis, tuning     │  │
│  │  ├─ /api/health        — codebase health data          │  │
│  │  ├─ /api/config        — repo config management        │  │
│  │  └─ /api/webhooks      — webhook receiver              │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
└────────┬──────────────────────────────────┬─────────────────┘
         │                                  │
         ▼                                  ▼
┌─────────────────┐                ┌─────────────────────┐
│  LLM Provider   │                │  Database           │
│  (configurable) │                │  (PostgreSQL — any   │
│                 │                │   or Cosmos DB)      │
│  ┌───────────┐  │                │                     │
│  │ Claude    │  │                │  Tables:            │
│  │ Anthropic │  │                │  ├─ reviews         │
│  └───────────┘  │                │  ├─ findings        │
│  ┌───────────┐  │                │  ├─ feedback        │
│  │ Azure     │  │                │  ├─ metrics         │
│  │ OpenAI    │  │                │  ├─ hotspots        │
│  └───────────┘  │                │  ├─ repo_configs    │
│  ┌───────────┐  │                │  ├─ health_snaps    │
│  │ Ollama /  │  │                │  └─ overrides       │
│  │ vLLM      │  │                │                     │
│  └───────────┘  │                └─────────────────────┘
└─────────────────┘
```

## Platform Adapter Pattern

The key to supporting both ADO and GitHub is a thin adapter layer that normalizes
platform-specific webhook payloads and API calls into a unified interface.

```
                    ┌─────────────────────────┐
                    │   PlatformAdapter       │
                    │   (Protocol/Interface)   │
                    │                         │
                    │   fetch_diff(pr) → Diff  │
                    │   post_comment(pr, msg)  │
                    │   approve_pr(pr)         │
                    │   add_label(pr, label)   │
                    │   get_work_item(pr)      │
                    │   list_reviewers(pr)     │
                    └────────┬────────────────┘
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
           ┌──────────────┐  ┌──────────────┐
           │ ADO Adapter  │  │ GitHub       │
           │              │  │ Adapter      │
           │ Uses:        │  │              │
           │ ADO REST API │  │ Uses:        │
           │ PAT or OAuth │  │ GitHub API   │
           │              │  │ App token or │
           │              │  │ PAT          │
           └──────────────┘  └──────────────┘
```

### Webhook Payload Normalization

```python
# Incoming ADO webhook
{
  "eventType": "git.pullrequest.created",
  "resource": {
    "pullRequestId": 123,
    "repository": {"name": "api-service", "project": {"name": "MyProject"}},
    "sourceRefName": "refs/heads/feature/add-login",
    "targetRefName": "refs/heads/develop",
    "createdBy": {"displayName": "Alice", "uniqueName": "alice@company.com"}
  }
}

# Incoming GitHub webhook
{
  "action": "opened",
  "pull_request": {
    "number": 456,
    "head": {"ref": "feature/add-login"},
    "base": {"ref": "develop"},
    "user": {"login": "alice"}
  },
  "repository": {"full_name": "myorg/api-service"}
}

# Both normalize to:
PlatformPR(
    platform="ado" | "github",
    pr_id="123" | "456",
    repo="api-service",
    source_branch="feature/add-login",
    target_branch="develop",
    author="alice",
    # ... plus platform-specific metadata for API callbacks
)
```

## Mechanical Checks: In-Process, Not CI

In the service model, mechanical checks run **inside the service container**, not in CI pipelines. This means:

- The container needs the tools installed: semgrep, gitleaks, etc.
- The service clones/fetches the PR diff, runs tools against it
- No pipeline agents consumed

```
Service container image includes:
├── Python 3.12 + pr-guardian package
├── semgrep (pip install)
├── gitleaks (binary)
├── sqlfluff (pip install)
├── hadolint (binary)
├── oasdiff (binary)
├── squawk (binary)
├── shellcheck (binary)
└── Node.js + dependency-cruiser (for JS/TS repos)
```

For language-specific tools that need the full project (e.g., `dotnet build`, `tsc --noEmit`),
these **still run in the repo's own CI pipeline** as they always have. PR Guardian doesn't
replace the build pipeline — it runs alongside it. The build pipeline handles:
- Compilation / type checking
- Unit tests
- SonarCloud
- Build artifacts

PR Guardian handles everything else (security, architecture, AI review, decision).

## Hosting

The application is a standard FastAPI app in a Docker container. It runs anywhere Docker runs. The key constraint: it must accept inbound HTTPS webhooks and make outbound calls to git platforms + LLM providers.

**Application code must not import any cloud-provider hosting SDK.** All cloud-specific configuration lives in infra templates, not in Python code.

### Deployment Profiles

| Profile | When to use | LLM | Database | Infra templates |
|---------|-------------|-----|----------|-----------------|
| **Cloud (Azure)** | Default. Teams that can use cloud. | SaaS (Anthropic) or Azure AI Foundry | Azure Database for PostgreSQL | `infra/azure/` |
| **Hybrid** | Code can't leave tenant, but cloud hosting is OK. | Azure AI Foundry (your tenant) | Azure Database for PostgreSQL | `infra/azure/` |
| **On-prem** | Code can't leave the building. Future. | Local models (Ollama/vLLM on GPU server) | PostgreSQL on local server | `infra/docker-compose/` or `infra/k8s/` |

All three profiles run the **same Docker image**. The only differences are:
- Environment variables (LLM endpoint, DB connection string, API keys)
- Infrastructure surrounding the container (managed service vs. bare Docker)

### Cloud Profile: Azure Container App

Best fit for cloud teams — scales to zero when idle, auto-scales on PR bursts, managed HTTPS/ingress, supports background jobs.

```yaml
# infra/azure/container-app.bicep
resource: containerApp
  name: pr-guardian
  location: westeurope
  properties:
    configuration:
      ingress:
        external: true          # needs to receive webhooks from ADO/GitHub
        targetPort: 8000
        transport: http
      secrets:
        - name: llm-api-key         # Anthropic or Azure AI Foundry key
        - name: ado-pat
        - name: github-app-private-key
        - name: db-connection-string
      registries:
        - server: prguardian.azurecr.io

    template:
      containers:
        - name: pr-guardian
          image: prguardian.azurecr.io/pr-guardian:latest
          resources:
            cpu: 1.0              # 1 vCPU
            memory: 2Gi           # 2GB RAM
          env:
            - name: LLM_API_KEY
              secretRef: llm-api-key
            - name: DATABASE_URL
              secretRef: db-connection-string

      scale:
        minReplicas: 0            # scale to zero when no PRs
        maxReplicas: 5            # scale up during PR spikes
        rules:
          - name: http-scaling
            http:
              metadata:
                concurrentRequests: 3   # 3 concurrent PR reviews per instance
```

Other Azure options considered:
| Option | Verdict |
|--------|---------|
| Azure Kubernetes Service | Overkill — too much operational overhead for a single service |
| Azure App Service | Would work but less flexible scaling, more expensive at idle |
| Azure Functions | Timeout limits (10 min max) won't work for large PR reviews |

### On-Prem Profile: Docker Compose

For teams that must keep everything on-premises. Same image, local infrastructure.

```yaml
# infra/docker-compose/docker-compose.yml
services:
  pr-guardian:
    image: pr-guardian:latest
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://guardian:${DB_PASSWORD}@db:5432/prguardian
      # Provider credentials — providers are defined in config/defaults.yml
      # Only set env vars for providers you've registered
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}
      - AZURE_OPENAI_KEY=${AZURE_OPENAI_KEY}
      - ADO_PAT=${ADO_PAT}
    depends_on:
      - db
    restart: unless-stopped

  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=prguardian
      - POSTGRES_USER=guardian
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    restart: unless-stopped

volumes:
  pgdata:
```

Notes for on-prem:
- Requires a reverse proxy (nginx/caddy) for HTTPS termination in front of the service
- LLM GPU server must be reachable from the Guardian host
- Webhook URL must be reachable from ADO/GitHub (may need a tunnel or VPN for GitHub)

### Kubernetes Profile

For teams with existing k8s clusters. Templates in `infra/k8s/` — standard Deployment + Service + Ingress manifests. Works with any k8s distribution (AKS, EKS, on-prem, k3s).

### Cost (Cloud Profile — Azure Container App)

```
Idle (no PRs):                $0/month (scales to zero)
Active (100 PRs/week):        ~$15-30/month compute
                              + ~$40-60/month LLM API
                              + ~$5/month database (Basic tier)
                              ≈ $60-95/month total

Compare to pipeline-native:   0 compute cost but
                              16 pipeline agent-hours/week consumed
                              (which you don't have to spare)
```

## Service Architecture (Code)

```
pr-guardian/
├── pyproject.toml
├── Dockerfile                   # Multi-stage: tools + Python app
├── docker-compose.yml           # Local dev (service + DB + mock webhooks)
│
├── src/
│   └── pr_guardian/
│       ├── __init__.py
│       ├── main.py              # FastAPI app entry point
│       │
│       ├── api/                 # HTTP layer
│       │   ├── webhooks.py      # POST /api/webhooks/{platform}
│       │   ├── dashboard.py     # GET /dashboard/* (serves UI)
│       │   ├── config_api.py    # GET/PUT /api/config/{repo}
│       │   ├── health_api.py    # GET /api/health
│       │   ├── feedback_api.py  # GET /api/feedback
│       │   └── metrics_api.py   # GET /api/metrics
│       │
│       ├── platform/            # Platform adapters (ADO + GitHub)
│       │   ├── protocol.py      # PlatformAdapter Protocol
│       │   ├── ado.py           # Azure DevOps adapter
│       │   ├── github.py        # GitHub adapter
│       │   ├── models.py        # PlatformPR, normalized types
│       │   └── factory.py       # create_adapter(webhook) → PlatformAdapter
│       │
│       ├── core/                # Review orchestration (platform-agnostic)
│       │   ├── orchestrator.py  # Main review pipeline
│       │   ├── reviewer.py      # Coordinates stages 1-4
│       │   └── queue.py         # Async job queue for PR reviews
│       │
│       ├── languages/           # Language detection + registry
│       │   ├── detector.py
│       │   ├── registry.py
│       │   └── tool_configs/
│       │
│       ├── mechanical/          # Stage 1: deterministic checks
│       │   ├── runner.py        # Run all applicable tools
│       │   ├── semgrep.py
│       │   ├── gitleaks.py
│       │   ├── pii_scanner.py
│       │   ├── api_contracts.py
│       │   ├── migration_safety.py
│       │   ├── deps.py
│       │   └── results.py
│       │
│       ├── triage/              # Stage 2: classification
│       │   ├── classifier.py
│       │   ├── hotspots.py
│       │   ├── surface_map.py
│       │   └── work_item.py
│       │
│       ├── agents/              # Stage 3: AI review agents
│       │   ├── base.py
│       │   ├── prompt_composer.py
│       │   ├── context_builder.py
│       │   ├── security_privacy.py
│       │   ├── performance.py
│       │   ├── architecture_intent.py
│       │   ├── code_quality_obs.py
│       │   ├── test_quality.py
│       │   └── hotspot.py
│       │
│       ├── decision/            # Stage 4: decision engine
│       │   ├── engine.py
│       │   └── actions.py       # Platform-agnostic decision → platform adapter acts
│       │
│       ├── llm/                 # LLM provider abstraction
│       │   ├── protocol.py
│       │   ├── anthropic.py
│       │   ├── openai_compat.py
│       │   ├── azure_openai.py
│       │   └── factory.py
│       │
│       ├── persistence/         # Database layer
│       │   ├── models.py        # SQLAlchemy / SQLModel models
│       │   ├── repository.py    # Data access
│       │   └── migrations/      # Alembic migrations
│       │
│       ├── feedback/            # Feedback loop
│       │   ├── logger.py
│       │   ├── analyzer.py
│       │   └── tuner.py
│       │
│       ├── health/              # Codebase health (scheduled)
│       │   ├── checker.py
│       │   ├── trends.py
│       │   └── alerts.py
│       │
│       ├── dashboard/           # Dashboard UI
│       │   ├── templates/       # Jinja2 or HTMX templates
│       │   └── static/          # CSS, JS
│       │
│       ├── config/
│       │   ├── schema.py
│       │   ├── loader.py
│       │   └── defaults.yml
│       │
│       └── models/              # Shared domain models
│           ├── pr.py
│           ├── languages.py
│           ├── findings.py
│           ├── feedback.py
│           └── output.py
│
├── prompts/                     # Agent prompts (per-agent, per-language)
│   ├── security_privacy/
│   │   ├── base.md
│   │   ├── python.md
│   │   ├── typescript.md
│   │   └── ...
│   ├── performance/
│   ├── architecture_intent/
│   ├── code_quality_observability/
│   ├── test_quality/
│   ├── hotspot/
│   └── cross_language.md
│
├── tests/
├── infra/                       # Infrastructure as Code (per deployment profile)
│   ├── azure/                   # Cloud profile
│   │   ├── container-app.bicep  # Azure Container App
│   │   ├── database.bicep       # Azure Database for PostgreSQL
│   │   ├── registry.bicep       # Azure Container Registry
│   │   └── keyvault.bicep       # Secrets management
│   ├── docker-compose/          # On-prem profile
│   │   └── docker-compose.yml   # Guardian + PostgreSQL + (optional) Ollama
│   └── k8s/                     # Kubernetes profile (any distro)
│       ├── deployment.yml
│       ├── service.yml
│       ├── ingress.yml
│       └── configmap.yml
│
├── Dockerfile                   # Single image, all profiles
└── docker-compose.dev.yml       # Local dev environment
```

## Request Flow (Webhook → Review → Action)

```
1. Webhook arrives
   POST /api/webhooks/ado  or  POST /api/webhooks/github
   │
2. Platform adapter normalizes → PlatformPR
   │
3. Queue review job (non-blocking, return 200 immediately)
   │
4. Background worker picks up job:
   │
   ├─ 4a. Clone/fetch PR diff via platform API
   │       (shallow clone — only diff, not full repo)
   │
   ├─ 4b. Load repo config (from DB cache or fetch from repo)
   │
   ├─ 4c. Detect languages
   │
   ├─ 4d. Run mechanical checks (in-process)
   │       ├─ semgrep (subprocess)
   │       ├─ gitleaks (subprocess)
   │       ├─ PII scanner (in-process)
   │       ├─ API contract check (subprocess)
   │       └─ migration safety (subprocess)
   │
   ├─ 4e. Triage → risk tier + agent selection
   │
   ├─ 4f. Run AI agents (async, parallel)
   │       ├─ security_privacy → LLM call
   │       ├─ performance → LLM call
   │       ├─ architecture_intent → LLM call
   │       ├─ code_quality_obs → LLM call
   │       ├─ test_quality → LLM call
   │       └─ hotspot → LLM call
   │
   ├─ 4g. Decision engine → auto-approve | escalate | block
   │
   ├─ 4h. Platform adapter executes action:
   │       ├─ Post PR comment (summary + findings)
   │       ├─ Approve / request changes (never auto-merge)
   │       └─ Add labels
   │
   └─ 4i. Log to database (feedback, metrics, cost)

Total time: 1-3 minutes (mostly LLM latency)
```

## Code Access: How the Service Gets the Diff

The service needs the PR code to review it. Options:

```
Option A: Shallow clone (recommended)
  git clone --depth=1 --branch=<source> <repo-url> /tmp/review-<pr-id>
  git fetch --depth=1 origin <target>
  git diff origin/<target>..HEAD
  # Clean up after review

  Pros: Full file access for context building, tools can run against real files
  Cons: Needs git credentials, temporary disk space

Option B: API-based diff only
  ADO: GET /repositories/{id}/diffs?baseVersion=<target>&targetVersion=<source>
  GitHub: GET /repos/{owner}/{repo}/pulls/{pr}/files

  Pros: No clone needed, fast
  Cons: Limited context (just the diff, not surrounding code), mechanical tools
        can't run (no real files)

Option C: Hybrid
  Fetch diff via API for triage (fast)
  Shallow clone only if agents need to run (deeper context)

  Pros: Fast for trivial PRs, full context when needed
  Cons: Two code paths to maintain
```

**Recommendation**: Option A (shallow clone) for simplicity. Disk space is cheap,
clone takes ~5s for most repos, and agents need full file context anyway.
Use a temp directory per review, clean up after.

## Authentication

```
┌────────────────────────────────────────────────────────┐
│  Azure DevOps                                          │
│                                                        │
│  Webhook → Service:                                    │
│    ADO Service Hook sends webhook with basic auth      │
│    or shared secret. Verify in webhook receiver.       │
│                                                        │
│  Service → ADO API:                                    │
│    PAT (Personal Access Token) or OAuth App             │
│    Stored in Key Vault, injected as env var             │
│    Needs: Code (Read), PR (Contribute), Work Items     │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│  GitHub                                                │
│                                                        │
│  Webhook → Service:                                    │
│    GitHub sends webhook with HMAC signature             │
│    Verify using webhook secret                         │
│                                                        │
│  Service → GitHub API:                                 │
│    GitHub App (recommended) or PAT                     │
│    App generates installation tokens per repo           │
│    Needs: Pull Requests (Read/Write), Contents (Read)  │
└────────────────────────────────────────────────────────┘
```

## Webhook Setup (Per Platform)

Replace `<GUARDIAN_URL>` with the actual Guardian service URL for your deployment profile:
- Cloud: `https://pr-guardian.azurecontainerapps.io` (or your custom domain)
- On-prem: `https://pr-guardian.internal.yourcompany.com` (behind reverse proxy)

### Azure DevOps
```
Project Settings → Service Hooks → Create Subscription
  Event: Pull request created / updated
  URL: <GUARDIAN_URL>/api/webhooks/ado
  Auth: Basic (username + password stored in service config)
```

### GitHub
```
Repo Settings → Webhooks → Add webhook
  URL: <GUARDIAN_URL>/api/webhooks/github
  Content type: application/json
  Secret: <shared secret>
  Events: Pull requests

OR (better): Create a GitHub App
  - Install on org/repos
  - Receives webhooks automatically
  - Fine-grained permissions
  - Installation tokens (no long-lived PATs)
```

## LLM Provider Abstraction

### The Question: Local vs SaaS

Supporting both does NOT add significant complexity if we draw the abstraction at the right level. Here's why:

The agents don't care about the LLM — they care about:
1. Send a system prompt + user message
2. Get back structured JSON
3. That's it

Every provider can do this. The differences are just auth + endpoint + model name.

### Architecture: Thin Provider Layer

```
┌──────────────────────────────────┐
│         Agent Code               │
│  (security, perf, arch, etc.)    │
│                                  │
│  agent.review(context) →         │
│    llm.complete(                 │
│      system=SYSTEM_PROMPT,       │
│      user=context,               │
│      response_format=json        │
│    )                             │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│      LLM Client Interface        │
│                                  │
│  class LLMClient(Protocol):      │
│    def complete(                 │
│      system: str,                │
│      messages: list[Message],    │
│      response_format: type       │
│    ) -> CompletionResult         │
└──────────────┬───────────────────┘
               │
     ┌─────────┼─────────┐
     ▼         ▼         ▼
┌─────────┐┌─────────┐┌──────────┐
│Anthropic││ OpenAI- ││  Azure   │
│ Client  ││ Compat  ││  AI      │
│         ││ Client  ││ Foundry  │
│ Claude  ││(Ollama, ││ Client   │
│ Sonnet  ││ vLLM,   ││          │
│ Opus    ││ llama-  ││ GPT-4o   │
│ Haiku   ││ cpp)    ││ etc.     │
└─────────┘└─────────┘└──────────┘
```

### Why NOT LiteLLM

LiteLLM is the obvious "use a library" answer, but:
- It's a heavy dependency (pulls in 50+ packages)
- We only need 3 providers (Anthropic, OpenAI-compatible, Azure AI Foundry)
- Our interface is dead simple (system prompt + user message → JSON)
- LiteLLM adds complexity we don't control (version conflicts, breaking changes)

A ~100 line abstraction with 3 provider implementations is cleaner, lighter, and fully under our control.

### Provider Configuration

Two layers:
1. **Service-level** — `config/defaults.yml` declares which providers exist and which is the default
2. **Per-repo** — `review.yml` selects a provider by name and optionally overrides models per agent

This means **Repo A can use Anthropic SaaS, Repo B can use Azure AI Foundry, and Repo C can use local Ollama** — all served by the same Guardian instance. Repos never specify raw URLs or API keys — they reference providers by name.

#### Service-Level: Provider Registry

The Guardian admin declares **all available providers** at deploy time. Each provider gets a short name. Guardian validates credentials and endpoint health at startup.

```yaml
# ─── SERVICE CONFIG (config/defaults.yml) ───
llm:
  default_provider: anthropic         # which provider repos get if they don't specify

  providers:
    anthropic:
      type: anthropic
      api_key_env: ANTHROPIC_API_KEY
      default_model: claude-sonnet-4-6
      models:                          # models available through this provider
        - claude-opus-4-6
        - claude-sonnet-4-6
        - claude-haiku-4-5

    azure-foundry:
      type: azure-openai
      endpoint_env: AZURE_OPENAI_ENDPOINT
      api_key_env: AZURE_OPENAI_KEY
      default_model: gpt-4o
      models:
        - gpt-4o
        - gpt-4o-mini

    local-ollama:
      type: openai-compatible
      base_url: http://gpu-server.internal:11434/v1
      api_key: not-needed
      default_model: llama3.3:70b
      models:
        - llama3.3:70b
        - qwen2.5-coder:32b

  # Shared settings (apply to all providers unless overridden)
  max_tokens: 4096
  temperature: 0.1
  timeout_seconds: 120

  # Per-agent model overrides (apply to the default provider)
  agent_overrides:
    security_privacy:
      model: claude-opus-4-6   # best model for security + privacy
    test_quality:
      model: claude-sonnet-4-6 # needs good reasoning for test assessment
    architecture_intent:
      model: claude-sonnet-4-6 # needs to understand intent
    code_quality_observability:
      model: claude-haiku-4-5  # cheaper model for style checks
    hotspot:
      model: claude-haiku-4-5  # mostly contextual, less reasoning needed
```

**Startup validation**: env vars resolve, endpoints respond to a health probe. Missing or unhealthy providers are logged as warnings and excluded from the available set. If `default_provider` is unhealthy, startup fails.

#### Per-Repo: Provider Selection

Repos reference a provider **by name** — they never embed raw URLs or credentials.

```yaml
# ─── PER-REPO (review.yml in repo root) ───
# This repo's code must stay in our Azure tenant.
llm:
  provider: azure-foundry             # must match a name in service config
  agent_overrides:
    security_privacy:
      model: gpt-4o                   # must be in that provider's models list
```

```yaml
# ─── PER-REPO: air-gapped repo (review.yml in repo root) ───
# This repo's code cannot leave the network at all.
llm:
  provider: local-ollama
```

If a repo references an unknown or unhealthy provider, Guardian falls back to `default_provider` and logs a warning.

#### Resolution Order

```
1. Load service config  → provider registry + defaults
2. Load repo review.yml → provider name + agent overrides
3. Resolve:
   provider  = repo.llm.provider  ?? service.llm.default_provider
   model     = repo.agent_overrides[agent].model
              ?? service.agent_overrides[agent].model
              ?? provider.default_model
4. Validate: provider exists, model is in provider.models[]
```

### LLM Provider vs Deployment Profile

These are independent axes:

| | SaaS LLM (Anthropic) | Azure AI Foundry | Local LLM (Ollama/vLLM) |
|---|---|---|---|
| **Cloud hosting (Azure Container App)** | Default. Cheapest. | For repos that can't send code to Anthropic. | Unusual but possible (GPU in same VNet). |
| **On-prem hosting (Docker/k8s)** | Works if outbound internet is allowed. | Works if Azure endpoint is reachable. | Full air-gap. Code + model both on-prem. |

### Local Models: When and Why

| Scenario | Use Local | Use SaaS |
|----------|-----------|----------|
| Air-gapped / classified environment | Must | Can't |
| Cost-sensitive, high volume (>500 PRs/week) | Consider | Expensive |
| Need best accuracy (security review) | No | Yes (Claude/GPT-4o) |
| Code can't leave the network | Must | Can't |
| Code can't leave the Azure tenant | No — use Azure AI Foundry | Can't (use Foundry) |
| Quick iteration on prompts | Good for dev | Good for prod |
| General use case | Maybe | Probably |

**Practical recommendation**: Start with SaaS (Claude Sonnet — best quality/cost ratio). Use Azure AI Foundry for repos with data residency constraints in your tenant. Plan for local models only when you have a true air-gap requirement or the volume justifies GPU investment. The abstraction layer means switching is a config change per repo, not a rewrite.

## Trigger Mechanism

Webhooks from both platforms. See "Webhook Setup" section above for configuration.

The service returns 200 immediately on webhook receipt, then processes the review
asynchronously. This means the webhook never times out, even for large PRs.

### How Repos Opt In

Each repo just needs a config file in its root (optional — defaults apply without it):

```yaml
# review.yml (in repo root, optional)
extends: defaults  # inherit from shared defaults

overrides:
  # This repo is a payment service — extra security scrutiny
  weights:
    security_privacy: 5.0
  privacy:
    compliance_frameworks: ["gdpr", "pci-dss"]
  triage:
    always_run: [security_privacy]
  repo_risk_class: critical    # never auto-approve payment code
```

The service reads this file from the repo during review. If no config file exists,
the shared defaults apply. Config can also be managed via the dashboard UI.

## Package Structure

```
pr-guardian/                     # Python package
├── pyproject.toml
├── src/
│   └── pr_guardian/
│       ├── __init__.py
│       ├── cli.py               # CLI entry point (all commands)
│       │
│       ├── llm/                 # LLM provider abstraction
│       │   ├── __init__.py
│       │   ├── protocol.py      # LLMClient Protocol class (~30 lines)
│       │   ├── anthropic.py     # Anthropic provider (~50 lines)
│       │   ├── openai_compat.py # OpenAI-compatible (Ollama/vLLM/OpenAI) (~50 lines)
│       │   ├── azure_foundry.py # Azure AI Foundry / Azure OpenAI provider (~50 lines)
│       │   └── factory.py       # create_client(config) → LLMClient (~20 lines)
│       │
│       ├── languages/           # Language detection + registry
│       │   ├── __init__.py
│       │   ├── detector.py      # file extension → language mapping
│       │   ├── registry.py      # which tools/rules per language
│       │   └── tool_configs/    # per-language tool configurations
│       │       ├── python.yml
│       │       ├── typescript.yml
│       │       ├── csharp.yml
│       │       ├── go.yml
│       │       ├── sql.yml
│       │       ├── terraform.yml
│       │       └── dockerfile.yml
│       │
│       ├── mechanical/          # Stage 1: deterministic checks
│       │   ├── semgrep.py       # Run + parse semgrep results
│       │   ├── gitleaks.py      # Run + parse gitleaks results
│       │   ├── pii_scanner.py   # PII detection in logs/output
│       │   ├── api_contracts.py # OpenAPI/proto breaking change detection
│       │   ├── migration_safety.py # DB migration linting
│       │   ├── deps.py          # dependency-cruiser / deptry / SCA
│       │   └── results.py       # Unified mechanical result type
│       │
│       ├── triage/              # Stage 2: PR classification
│       │   ├── classifier.py    # Risk tier logic (language-aware)
│       │   ├── hotspots.py      # Git history analysis
│       │   ├── surface_map.py   # Security/perf file mapping
│       │   └── work_item.py     # ADO work item linking
│       │
│       ├── agents/              # Stage 3: AI review agents
│       │   ├── base.py          # Base agent class
│       │   ├── prompt_composer.py # Assemble base + language-specific prompts
│       │   ├── context_builder.py # Language-aware context assembly
│       │   ├── security_privacy.py    # Security + privacy/GDPR agent
│       │   ├── performance.py         # Performance + concurrency agent
│       │   ├── architecture_intent.py # Architecture + intent verification
│       │   ├── code_quality_obs.py    # Code quality + observability
│       │   ├── test_quality.py        # Test quality + coverage meaning
│       │   └── hotspot.py             # Hotspot risk agent
│       │
│       ├── decision/            # Stage 4: decision engine
│       │   ├── engine.py        # Scoring + rules
│       │   ├── actions.py       # Auto-approve / escalate actions
│       │   ├── ado_client.py    # Azure DevOps REST API client
│       │   └── feedback.py      # Log decisions for feedback loop
│       │
│       ├── config/              # Configuration
│       │   ├── schema.py        # Config dataclasses / Pydantic models
│       │   ├── loader.py        # Load + merge yaml configs
│       │   └── defaults.yml     # Default config values
│       │
│       ├── health/              # Complementary: codebase health
│       │   ├── dashboard.py     # Weekly health check runner
│       │   ├── trends.py        # Trend analysis over time
│       │   ├── mutation.py      # Mutation testing integration
│       │   └── alerts.py        # Threshold-based alerting
│       │
│       ├── feedback/            # Complementary: feedback loop
│       │   ├── logger.py        # JSONL feedback logging
│       │   ├── analyzer.py      # Weekly disagreement analysis
│       │   ├── tuner.py         # Threshold tuning recommendations
│       │   └── reporter.py      # Generate feedback reports
│       │
│       └── models/              # Shared data models
│           ├── pr.py            # PR metadata types
│           ├── languages.py     # LanguageMap, language detection types
│           ├── findings.py      # Finding, AgentResult types
│           ├── feedback.py      # FeedbackEntry, OverrideRecord types
│           └── output.py        # Decision output types
│
├── tests/
│   ├── test_triage.py
│   ├── test_decision.py
│   ├── test_language_detection.py
│   ├── test_pii_scanner.py
│   ├── test_api_contracts.py
│   ├── test_feedback.py
│   ├── test_agents/
│   └── test_llm/
│
└── prompts/                     # Agent system prompts (per-agent, per-language)
    ├── security_privacy/
    │   ├── base.md              # Universal security + privacy concerns
    │   ├── python.md            # SQLAlchemy injection, pickle, eval, subprocess
    │   ├── typescript.md        # XSS, prototype pollution, RegExp DoS
    │   ├── csharp.md            # EF Core injection, XML external entities
    │   ├── sql.md               # Direct injection, privilege escalation
    │   ├── terraform.md         # Public buckets, open security groups
    │   ├── go.md                # Template injection, path traversal
    │   └── dockerfile.md        # Running as root, secrets in layers
    ├── performance/
    │   ├── base.md
    │   ├── python.md            # GIL, N+1 SQLAlchemy, sync in async
    │   ├── typescript.md        # Bundle size, re-renders, memory leaks
    │   ├── csharp.md            # EF Core N+1, async/await deadlocks
    │   ├── sql.md               # Missing indexes, full table scans
    │   └── go.md                # Goroutine leaks, unbuffered channels
    ├── architecture_intent/
    │   ├── base.md              # Universal architecture + intent verification
    │   ├── python.md            # Django/FastAPI patterns, circular imports
    │   ├── typescript.md        # React patterns, module boundaries
    │   ├── csharp.md            # .NET patterns, DI conventions
    │   └── go.md                # Package layout, interface patterns
    ├── code_quality_observability/
    │   ├── base.md              # Universal quality + observability
    │   ├── python.md            # Pythonic patterns, logging stdlib
    │   ├── typescript.md        # TS idioms, console.log vs structured logging
    │   ├── csharp.md            # .NET conventions, ILogger patterns
    │   └── go.md                # Go idioms, structured logging
    ├── test_quality/
    │   ├── base.md              # Universal test quality concerns
    │   ├── python.md            # pytest patterns, mock abuse, parametrize
    │   ├── typescript.md        # Jest/Vitest patterns, component testing
    │   ├── csharp.md            # xUnit/NUnit patterns, integration tests
    │   └── go.md                # Table-driven tests, test helpers
    ├── hotspot/
    │   └── base.md              # Language-agnostic (git history based)
    └── cross_language.md        # Cross-stack concerns (contract mismatches)
```

## CLI Interface

```bash
# ─── Per-PR Pipeline Commands ───

# Stage 0: Detect languages in diff
pr-guardian detect-languages \
  --diff-target develop \
  --output languages.json

# Stage 1: Mechanical checks (individual)
pr-guardian scan-pii \
  --diff-target develop \
  --config review.yml \
  --output pii-results.json

pr-guardian check-api-contracts \
  --diff-target develop \
  --output api-contract-results.json

# Stage 2: Triage
pr-guardian triage \
  --config review.yml \
  --pr-id 12345 \
  --source-branch feature/add-login \
  --target-branch develop \
  --mechanical-results ./results/ \
  --output triage-result.json

# Stage 3: Run a single agent (with language-aware prompts)
pr-guardian review \
  --agent security_privacy \
  --config review.yml \
  --diff-target develop \
  --languages python,typescript,sql \
  --output security-privacy-result.json

pr-guardian review \
  --agent test_quality \
  --config review.yml \
  --diff-target develop \
  --languages python \
  --output test-quality-result.json

# Stage 4: Make decision + log feedback
pr-guardian decide \
  --config review.yml \
  --risk-tier medium \
  --artifacts-dir ./agent-results/ \
  --pr-id 12345 \
  --ado-org https://dev.azure.com/myorg \
  --ado-project MyProject \
  --log-feedback=true

# ─── Complementary System Commands ───

# Compute hotspots (run on schedule, not per-PR)
pr-guardian hotspots \
  --days 90 \
  --output .pr-guardian/hotspots.json

# Codebase health check (scheduled weekly)
pr-guardian health-check \
  --config review.yml \
  --output health-report.json

pr-guardian health-report \
  --format html \
  --input health-report.json \
  --output health-report.html

# Feedback analysis (run weekly)
pr-guardian feedback-analyze \
  --feedback-dir feedback/ \
  --days 7 \
  --output weekly-feedback.json

pr-guardian feedback-recommend \
  --analysis weekly-feedback.json \
  --output threshold-recommendations.yml

# ─── Developer Utility Commands ───

# Validate config
pr-guardian validate --config review.yml

# Dry-run full pipeline locally (developer testing)
pr-guardian dry-run \
  --config review.yml \
  --diff-target develop

# Show what agents would run + estimated cost
pr-guardian estimate \
  --config review.yml \
  --diff-target develop
```

## Data Flow

```
Webhook → Service (in-memory, single process per review)

  1. Webhook payload → PlatformPR
  2. Clone repo → /tmp/review-{pr-id}/
  3. Detect languages → LanguageMap
  4. Run mechanical checks → MechanicalResults[]
  5. Triage → RiskTier + AgentSet
  6. Run agents (parallel async) → AgentResult[]
  7. Decision engine → Decision
  8. Platform adapter → PR comment / approve (never auto-merge)
  9. Persist to DB → reviews, findings, metrics, feedback
  10. Clean up temp directory

All in one process. No inter-service communication.
No message queues. No pipeline artifacts.
Database is the persistent record of everything.
```

## Platform API Usage

```
Azure DevOps REST API:
  GET  /git/repositories/{id}/pullRequests/{id}     → PR metadata
  GET  /git/repositories/{id}/items?path=...         → Fetch review.yml config
  POST /git/pullRequests/{id}/threads                → Post review comment
  POST /git/pullRequests/{id}/reviewers              → Approve (+10 vote)
  POST /git/pullRequests/{id}/labels                 → Add labels
  # No merge API — author merges manually
  POST /git/pullRequests/{id}/statuses               → Set status check
  GET  /wit/workitems/{id}                           → Fetch linked work item

GitHub REST API:
  GET  /repos/{owner}/{repo}/pulls/{number}          → PR metadata
  GET  /repos/{owner}/{repo}/contents/{path}         → Fetch review.yml config
  POST /repos/{owner}/{repo}/pulls/{number}/reviews  → Post review (approve/comment)
  POST /repos/{owner}/{repo}/issues/{number}/labels  → Add labels
  # No merge API — author merges manually
  POST /repos/{owner}/{repo}/statuses/{sha}          → Set commit status
```

## Network Topology

Same network shape regardless of deployment profile — only the endpoints change.

```
┌─ Container Host (Azure Container App / Docker / k8s) ──────────┐
│                                                                  │
│  pr-guardian service (FastAPI)                                    │
│     │                                                            │
│     ├── Inbound HTTPS (webhooks from ADO + GitHub)               │
│     │   ←── https://dev.azure.com (ADO service hook)             │
│     │   ←── https://github.com (GitHub webhook)                  │
│     │                                                            │
│     ├── Outbound: clone repos (HTTPS + auth)                     │
│     │   ──→ https://dev.azure.com/myorg/_git/repo                │
│     │   ──→ https://github.com/myorg/repo                        │
│     │                                                            │
│     ├── Outbound: LLM providers (per-repo config)                │
│     │   ──→ https://api.anthropic.com (Claude — SaaS)            │
│     │   ──→ https://myorg.openai.azure.com (Azure AI Foundry)    │
│     │   ──→ http://gpu-server.internal:11434 (Ollama — on-prem)  │
│     │   Note: different repos may hit different providers.        │
│     │                                                            │
│     ├── Outbound: platform APIs (post results back)              │
│     │   ──→ https://dev.azure.com/myorg (ADO API)                │
│     │   ──→ https://api.github.com (GitHub API)                  │
│     │                                                            │
│     └── Outbound: database                                       │
│         ──→ PostgreSQL (managed or self-hosted, via DATABASE_URL) │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Cost Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Compute (depends on deployment profile):                │
│                                                          │
│  Cloud (Azure Container App):                            │
│  ├─ Scale to zero when idle: $0                          │
│  ├─ Active (100 PRs/week): ~$15-30/month                │
│  └─ Burst (5 concurrent reviews): auto-scales            │
│                                                          │
│  On-prem (Docker/k8s on existing infra):                 │
│  └─ $0 incremental (uses existing hardware)              │
│                                                          │
│  Database: PostgreSQL (any)                               │
│  ├─ Azure managed: ~$20/month (Burstable B1ms + storage) │
│  └─ Self-hosted: $0 incremental (uses existing hardware)  │
│                                                          │
│  LLM: per-token API costs (varies by repo config)        │
│  ├─ Claude Sonnet: ~$0.08-0.20 per PR (6 agents)        │
│  ├─ Azure AI Foundry GPT-4o: ~$0.08-0.25 per PR         │
│  ├─ Ollama (self-hosted): $0 per PR (hardware cost only) │
│  └─ Average: ~$0.10 per PR if mostly low/medium tier     │
│                                                          │
│  Tooling: free (semgrep, gitleaks, etc. all OSS)         │
│                                                          │
│  Pipeline agents consumed: ZERO                          │
│                                                          │
│  Total for 100 PRs/week:                                 │
│  ├─ Cloud + SaaS LLM: ~$60-95/month all-in              │
│  ├─ Cloud + Azure AI Foundry: ~$55-90/month all-in       │
│  ├─ On-prem + SaaS LLM: ~$40-60/month (LLM only)        │
│  └─ Full on-prem + local LLM: ~$0/month (hardware only)  │
│                                                          │
│  Note: Guardian auto-approves, never auto-merges.        │
│  ROI: saves ~$6,000/month in senior dev review time      │
└─────────────────────────────────────────────────────────┘
```

## Open Questions

1. **Azure DevOps permissions**: Verify build service identity can: post PR comments, approve PRs (+10 vote), add labels, read work items
2. **Context window management**: Large PRs (>500 lines) may exceed context. Strategy: chunk large diffs by module, summarize unchanged context, or force flag for human review. Note: if an agent can't see full context (`saw_full_context: false`), the decision engine treats its silence as untrusted.
3. **Cross-repo patterns**: Should findings from one repo inform reviews in another? (e.g., security patterns learned from payment-service applied to other services)
4. **Escape hatch**: `[skip-guardian]` in commit message for emergencies? Who can use it? Should it require approval from a specific group?
5. **PR re-review**: When a PR is updated after agent review, which stages need to re-run? (mechanical: all, triage: re-classify, agents: only if tier/files changed)
6. **Conflicting PRs**: Two PRs that are individually fine but conflict when both merge. Can the pipeline detect this? (probably not per-PR — needs merge queue)
7. **Data classification bootstrap**: Who defines the initial `data-classification.yml`? Need a process to classify existing data fields before the privacy agent can be effective
8. **Mutation testing baseline**: First mutation testing run on existing code will likely show poor scores everywhere. Need a strategy for progressive improvement, not blocking on legacy code
9. **Repo risk class ownership**: Who sets the initial `repo_risk_class` per repo? Should it be code owners, security team, or a governance process?
10. **Certainty calibration monitoring**: Track downgrade rate over time. If agents consistently overclaim `detected` (>20% downgrade rate), prompts need tuning. Should this trigger automated prompt adjustments or just alerts?
11. **On-prem webhook ingress**: For on-prem deployments, how do ADO/GitHub webhooks reach the Guardian service? Options: VPN, Azure Relay, ngrok-style tunnel, or ADO Server (on-prem ADO) with direct network access.
12. **Local model quality threshold**: When a repo uses local models (Ollama/vLLM), should the decision engine apply stricter rules (e.g., never auto-approve) to compensate for potentially lower model accuracy? Or trust the operator's choice?
13. **Multi-LLM single review**: If Guardian serves repos with different LLM providers, it may hold API keys for all providers simultaneously. Secrets management strategy for on-prem deployments (no KeyVault)?

---

## Realistic Assessment: What This Can and Can't Do

### What PR Guardian Handles Well (machines replace humans)

| Category | Coverage | How |
|----------|----------|-----|
| Style / formatting | ~100% | Mechanical: linters, formatters |
| Known vulnerability patterns | ~90% | Mechanical: Semgrep, CodeQL, SCA |
| Secret exposure | ~95% | Mechanical: Gitleaks + PII scanner |
| Architecture boundary violations | ~90% | Mechanical: dep-cruiser + fitness tests |
| API breaking changes | ~95% | Mechanical: oasdiff, buf |
| Migration safety | ~85% | Mechanical: squawk + agent review |
| Common perf anti-patterns | ~80% | Agent: N+1, O(n²), unbounded queries |
| Test existence + basic quality | ~75% | Agent: assertion quality, coverage gaps |
| PII in logs/output | ~80% | Mechanical scan + agent review |
| Observability gaps | ~70% | Fitness tests + agent review |
| Cross-language contract mismatches | ~60% | Agent review (harder, mostly "suspected" certainty) |

### What Still Needs Humans (machines assist, humans decide)

| Category | Why Machines Struggle | Guardian's Role |
|----------|----------------------|-----------------|
| "Is this the right approach?" | Requires business context, product vision | Intent verification gives a hint, but humans decide |
| Complex business logic correctness | Requires domain knowledge | Agents flag suspicious logic, humans verify |
| Subtle concurrency bugs | Static analysis can't catch most races | Agent flags obvious patterns, humans catch the rest |
| Architectural vision / direction | Requires understanding of where the system is going | Agent checks against documented decisions, humans set direction |
| "Should we build this at all?" | Product decision, not code decision | Completely outside scope |
| Novel security attack vectors | Zero-day patterns not in any ruleset | Agent catches known patterns, humans think adversarially |
| UX / user impact of changes | Requires understanding user workflows | Completely outside scope |

### Honest Numbers

| Metric | Target |
|--------|--------|
| PRs that skip human review (auto-approved) | 50-70% (on standard repos) |
| Reduction in human review time for remaining PRs | 40-60% |
| Total human review effort saved | 70-80% |
| False negative rate (bugs that slip through) | <2% (comparable to human review) |
| False positive rate (unnecessary escalations) | <15% (after tuning) |
| Time from PR creation to auto-approve | <5 minutes |
| Time from PR creation to merge (auto-approved) | <10 minutes (author clicks merge) |
| Time from PR creation to merge (human path) | hours (but with focused brief) |
| Certainty downgrade rate | <20% (if agents overclaim >20%, prompts need tuning) |

### What Makes This Worth Building

The value isn't just "fewer reviews." It's:

1. **Consistency** — Agents don't have bad days, don't get review fatigue, don't rubber-stamp PRs on Friday afternoon
2. **Speed** — 5-minute auto-approve vs 1-3 day review queue
3. **Knowledge distribution** — Security expertise, perf patterns, and architecture rules are encoded in agent prompts and mechanical checks, not locked in one person's head
4. **Non-dev enablement** — Non-devs can ship without waiting days for a dev reviewer. The safety net is automatic, the merge button is theirs.
5. **Agent enablement** — AI coding agents can iterate faster when the feedback loop is automated
6. **Audit trail** — Every PR decision is logged with full reasoning, which human review rarely provides

---

## Component Summary & Priority

| Component | Type | Purpose | Priority |
|-----------|------|---------|----------|
| Language detector | Mechanical | Detect languages in diff, drive everything | P0 |
| Semgrep SAST | Mechanical | Security static analysis | P0 |
| Gitleaks | Mechanical | Secret detection | P0 |
| PII scanner | Mechanical | PII in logs/output | P1 |
| API contract checker | Mechanical | Breaking change detection | P1 |
| Migration safety | Mechanical | DB migration linting | P1 |
| Fitness tests (template) | Mechanical | Architecture + observability enforcement | P1 |
| dep-cruiser / arch rules | Mechanical | Module boundary enforcement | P1 |
| Triage engine | Logic | Risk classification + agent routing | P0 |
| Work item linker | Integration | Fetch ADO work item for intent verification | P2 |
| Security + Privacy agent | AI Agent | Vuln + privacy + GDPR review | P0 |
| Performance agent | AI Agent | Perf anti-pattern review | P1 |
| Architecture + Intent agent | AI Agent | Arch drift + intent verification | P1 |
| Code Quality + Observability agent | AI Agent | Quality + logging/tracing review | P1 |
| Test Quality agent | AI Agent | Test assertion + coverage quality | P1 |
| Hotspot agent | AI Agent | High-risk file extra scrutiny | P2 |
| Decision engine | Logic | Scoring + auto-approve/escalate rules | P0 |
| Feedback logger | Integration | Log all decisions for learning | P1 |
| ADO client (comments/approve/merge) | Integration | Azure DevOps API actions | P0 |
| LLM abstraction (3 providers) | Infra | Anthropic, Azure AI Foundry, OpenAI-compat | P0 |
| Prompt library (per-agent, per-lang) | Content | Agent system prompts | P0 |
| Health dashboard | Complementary | Weekly codebase health trends | P2 |
| Feedback analyzer | Complementary | Weekly override analysis | P2 |
| Threshold tuner | Complementary | Auto-recommend threshold changes | P3 |
| Mutation testing integration | Complementary | Scheduled test quality scoring | P3 |

**P0** = MVP (first pilot repo) — get the core loop working
**P1** = Phase 2 (full single-repo deployment) — all agents, all mechanical checks
**P2** = Phase 3 (multi-repo rollout) — complementary systems, learning loop
**P3** = Phase 4 (optimization) — auto-tuning, mutation testing, cross-repo learning
