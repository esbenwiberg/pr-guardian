# PR Guardian — Multi-Language Support

A single PR can touch Python, TypeScript, C#, SQL, Terraform, Dockerfiles, YAML
pipelines, shell scripts, and more. Every stage must handle this.

**Design principle**: detect languages from the diff, then adapt each stage to
the languages present.

Language detection runs in **Stage 0: Discovery** (see
[01b-discovery.md](01b-discovery.md)). It produces a `LanguageMap` inside the
`ReviewContext` that all downstream stages consume. This document covers how
each stage *uses* the language information.

---

## Stage 1: Language-Conditional Mechanical Tools

Not every tool runs on every PR. The triage output's `languages` map determines
which checks fire:

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

### In-Service Orchestration

Guardian runs language-conditional checks **in-process** (not CI jobs). The
mechanical runner uses the language map to select which tools to invoke:

```python
async def run_mechanical_checks(
    repo_path: Path, language_map: LanguageMap, config: RepoConfig
) -> list[MechanicalResult]:
    """Run language-conditional mechanical checks in parallel."""
    tasks = []

    # Universal checks (always run)
    tasks.append(run_gitleaks(repo_path))
    tasks.append(run_semgrep(repo_path, rules="universal"))

    # Language-conditional checks
    for lang in language_map.languages:
        tool_config = config.get_tools(lang)  # from review.yml
        if lang == "python" and tool_config.ruff:
            tasks.append(run_ruff(repo_path, language_map.files("python")))
        if lang == "python" and tool_config.bandit:
            tasks.append(run_bandit(repo_path, language_map.files("python")))
        if lang == "typescript" and tool_config.biome:
            tasks.append(run_biome(repo_path, language_map.files("typescript")))
        if lang == "terraform" and tool_config.checkov:
            tasks.append(run_checkov(repo_path, language_map.files("terraform")))
        # ... etc per language from tool table above

    return await asyncio.gather(*tasks)
```

Tools that require the full project build (`dotnet build`, `tsc --noEmit`) still
run in the repo's own CI pipeline. Guardian consumes those results via platform
API status checks — see [07-architecture.md](07-architecture.md).

---

## Stage 2: Language-Aware Risk Scoring

Language mix affects risk tier:

```
Risk amplifiers:
  - cross_stack = true (>1 runtime language) → bump risk tier one level
  - sql present → always trigger security agent (injection risk)
  - terraform/bicep present → always trigger security agent (infra misconfig)
  - dockerfile present → always trigger security agent (container security)
  - language_count > 3 → bump to HIGH (too many concerns for one review)
```

---

## Stage 3: Composable Language-Specific Prompts

Each agent's system prompt is **composed** from base + language-specific sections:

```
AGENT PROMPT = base_prompt + Σ(language_section for each language in PR)
```

### Prompt Directory Structure

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
├── performance/
│   ├── base.md
│   ├── python.md            # GIL, N+1 with SQLAlchemy, sync in async
│   ├── typescript.md        # bundle size, re-renders, memory leaks
│   ├── csharp.md            # EF Core N+1, async/await deadlocks
│   ├── sql.md               # missing indexes, full table scans
│   └── go.md                # goroutine leaks, unbuffered channels
├── architecture/
│   ├── base.md
│   ├── python.md            # Django/FastAPI patterns, circular imports
│   ├── typescript.md        # React patterns, module boundaries
│   ├── csharp.md            # .NET patterns, dependency injection
│   └── go.md                # package layout, interface patterns
├── code_quality/
│   ├── base.md
│   └── ...per language
├── test_quality/
│   ├── base.md
│   └── ...per language
├── hotspot/
│   └── base.md              # language-agnostic (git history based)
└── cross_language.md
```

### Prompt Composition at Runtime

```python
def build_agent_prompt(agent_type: str, languages: list[str]) -> str:
    """Compose system prompt from base + language-specific sections."""
    parts = [load_prompt(f"{agent_type}/base.md")]

    for lang in languages:
        lang_prompt = load_prompt(f"{agent_type}/{lang}.md")
        if lang_prompt:
            parts.append(f"\n## {lang.upper()}-SPECIFIC REVIEW\n{lang_prompt}")

    if len(languages) > 1:
        parts.append(CROSS_LANGUAGE_SECTION)

    return "\n\n---\n\n".join(parts)
```

### Cross-Language Concerns

Added when a PR spans multiple languages:

- Data contracts between layers (API request/response shapes match frontend types?)
- Shared constants/enums that must stay in sync across languages
- Migration + code changes that must deploy atomically
- Error handling across language boundaries
- Authentication/authorization applied consistently across all endpoints

### Language-Aware Context Building

The context builder adapts per language. Each agent gets relevant codebase
patterns for the languages present:

- Python + security → show existing auth patterns, middleware
- TypeScript + architecture → show module structure, tsconfig
- C# + performance → show existing EF Core patterns
- SQL + security → show existing migration patterns
- Terraform + security → show existing infra patterns

---

## Per-Repo Language Configuration

```yaml
# review.yml
languages:
  expected: [python, typescript, sql]  # optional — auto-detected if omitted

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

  terraform:
    mechanical:
      checkov: true
      tflint: true
    always_trigger: [security]  # terraform changes always trigger security agent

  # Adding a new language is just config — no code changes
```
