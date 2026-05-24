# Architecture Anchor Discovery

> Runtime discovery spec for the `architecture` review agent. Walks the repo
> cheapest-first, classifies signals into rules / conventions / structural
> hints, and picks one of three modes: full verifier, narrow local-pattern
> check, or skip.

## Motivation

Per `plans/agent-redesign.md`, the refocused `architecture` agent only produces
useful findings when anchored to a stated architecture. Without an anchor, it
emits subjective noise. This spec defines what "an anchor" means concretely:
which files count, how strongly, and what threshold flips the agent between
full verifier mode, narrow local-pattern check, and skip.

Survey of popular repos confirms the cross-ecosystem reality: hardly anyone
ships a `docs/adr/` tree. Most repos communicate architecture through some mix
of `CONTRIBUTING.md`, folder conventions, build files (`Directory.Build.props`,
`nx.json`, `Cargo.toml` workspaces), and — increasingly — `AGENTS.md` /
`CLAUDE.md`. The agent must read what exists, not what should exist.

---

## Signal Taxonomy

Every anchor candidate falls into one of three classes. The class — not the
file name — drives how the agent uses it.

| Class | Definition | Example | Agent uses it as |
|---|---|---|---|
| **Rule** | Imperative statement: "we do X", "X is forbidden", "every X must Y" | "All write paths go through MediatR commands" in an ADR | Hard ground truth — deviations are findings |
| **Convention** | Descriptive pattern: "controllers look like this", "queries live in `application/queries/`" | `AGENTS.md` style notes, `CONTRIBUTING.md` "How we organize code" sections | Soft ground truth — deviations are flagged but cite the convention |
| **Structural hint** | Folder/config that implies a pattern without naming it | A `Domain/` + `Application/` + `Infrastructure/` triple, `internal/` in a Go repo, `nx.json` with project boundaries | Used only to verify a sibling-file pattern; cannot stand alone as full verifier anchor |

The agent must classify each discovered file before weighting it. A
`CONTRIBUTING.md` that just says "run `make test`" is not a rule. An
`ARCHITECTURE.md` that just embeds a stale 2019 diagram is not a rule either —
it's at best a convention, possibly noise.

---

## Per-File-Type Reference

| Signal | Class | What it tells the agent | Notes |
|---|---|---|---|
| `review.yml :: architecture_docs` | Rule | Explicit team-declared anchor file list | **Always wins.** Team has opted in. |
| `docs/adr/`, `docs/architecture/decisions/`, `doc/adr/`, `adr/` | Rule | One decision per file, dated, typically with Status: Accepted | MADR / Nygard format. Read all with Status != Superseded. |
| `ARCHITECTURE.md` (root) | Rule or Convention | Depends on content — imperative vs. descriptive | Per matklad's pattern: orientation + module map. Treat as rule when imperative. |
| `AGENTS.md` (root) | Convention | Author intent for AI agents — usually mixes architecture, build, test commands | **Filter:** only the architecture-relevant subset counts. Reject if it's only Claude Code instructions. |
| `CLAUDE.md` (root) | Convention | Same as `AGENTS.md`, Claude-specific | Same filtering rule. |
| `.cursorrules`, `.cursor/rules/` | Convention | Cursor IDE prompt; often contains "use pattern X" rules | Read but down-weight — often stale or aspirational. |
| `.github/copilot-instructions.md` | Convention | Copilot prompt | Same as `.cursorrules`. |
| `CONVENTIONS.md`, `docs/conventions/` | Convention | Explicit conventions doc | Usually high quality when present. |
| `CONTRIBUTING.md` (architecture sections) | Convention | "How we organize code", "Project structure" sections | Substring-match on headings; ignore the "how to run tests" parts. |
| `docs/` arbitrary markdown | Convention or structural | Mermaid/PlantUML/C4 diagrams, design docs | High false-positive rate for staleness. Only use when explicitly referenced from a higher-precedence file. |
| C4 / Structurizr DSL (`*.dsl`, `workspace.dsl`) | Rule | Container/component model with relationships | Strong signal when present — but rare in the wild. |
| arc42 templates (`docs/arc42/`, `arc42/`) | Convention | Filled-out arc42 chapters | Treat each chapter independently; many are intentionally empty. |
| `dependency-cruiser` config (`.dependency-cruiser.{js,json,cjs}`) | Rule | Machine-enforced layer/module rules | **Strong rule** — these are CI-enforced. |
| `ts-arch` / ArchUnit / NetArchTest tests | Rule | Machine-enforced architecture tests | **Strongest rule** — already gates merges. |
| `Directory.Build.props`, `Directory.Packages.props` (.NET) | Structural | Centralized build config; project-ref restrictions sometimes encoded | Read for `<ProjectReference>` constraints. |
| `.editorconfig` | Structural | Style only — rarely architectural | Skip unless it pins `dotnet_diagnostic.*` analyzer severities. |
| `.sln` / `.slnx` / `.slnf` structure + folder names matching `Domain` / `Application` / `Infrastructure` / `Core` / `Web` / `Api` | Structural | Strongly suggests Clean Architecture / Onion | Combined with `Directory.Build.props` this is a "convention by structure" — usable for local-pattern mode. |
| `pyproject.toml` `[tool.*]` sections, `src/` vs flat layout | Structural | Package layout convention | Mostly used to disambiguate sibling-file checks. |
| `nx.json`, `turbo.json`, `pnpm-workspace.yaml`, `lerna.json`, root `package.json` `workspaces` | Structural | Monorepo project boundaries | Used to scope sibling-file checks to the right workspace. |
| `tsconfig.json` `paths` / project references | Structural | Module boundaries via path aliases | Disallowed cross-references can sometimes be inferred. |
| Maven `pom.xml` `<modules>`, Gradle `settings.gradle[.kts]` `include(...)` | Structural | Multi-module boundaries | JVM equivalent of Nx projects. |
| Go `go.mod` + `internal/`, `cmd/`, `pkg/` layout | Structural | `internal/` = compiler-enforced privacy. `cmd/` = entrypoints. `pkg/` = library code (controversial; not universal). | `internal/` boundary is a real Go-toolchain rule, not just a convention. |
| Rust `Cargo.toml` `[workspace]`, crate boundaries | Structural | Crate = strongest module boundary in Rust | Cross-crate visibility is compiler-enforced. |

---

## Precedence Order

When multiple signals are present, higher-precedence ones win on conflict.

| Rank | Source | Rationale |
|---|---|---|
| 1 | `review.yml :: architecture_docs` | Explicit opt-in. Team named these files; trust them. |
| 2 | Machine-enforced architecture tests (ArchUnit / ts-arch / NetArchTest / dependency-cruiser) | CI already enforces these. Aligning the LLM with the linter is free correctness. |
| 3 | ADRs with `Status: Accepted` | Versioned, dated, atomic decisions. The gold standard. |
| 4 | `ARCHITECTURE.md` (when imperative) | Single source of truth when it exists and is current. |
| 5 | `CONVENTIONS.md` / `docs/conventions/` | Explicit conventions doc. |
| 6 | C4 / Structurizr DSL files | Formal model. Rare but authoritative when present. |
| 7 | `AGENTS.md` / `CLAUDE.md` (architecture-relevant sections) | Author-stated agent guidance. Filter out non-architecture content. |
| 8 | `CONTRIBUTING.md` architecture sections | Mixed quality; substring-extract only. |
| 9 | `.cursorrules`, `.github/copilot-instructions.md` | Often stale or aspirational. Use to corroborate, not lead. |
| 10 | Folder-structure conventions (Clean Arch layout, Go `internal/`, monorepo project layout) | Structural hints only — sufficient for local-pattern mode, not full verifier mode. |
| 11 | Sibling-file patterns | Last resort. Compare the changed file against its directory peers. |

**Conflict resolution rule:** when signal N contradicts signal N+k, the higher
rank wins, but the agent must cite both in the finding (so reviewers can see
the stale doc). Exception: rank 1 overrides everything silently — the team
already declared what counts.

---

## Weighting (for confidence scoring)

Findings cite at least one anchor. The anchor's weight feeds the finding's
`certainty` field.

| Anchor class | Weight | Certainty floor in finding |
|---|---|---|
| Machine-enforced (rank 2) | 1.0 | high |
| ADR (rank 3) or `review.yml`-declared rule doc | 0.9 | high |
| `ARCHITECTURE.md` / `CONVENTIONS.md` (rank 4–5) | 0.7 | medium |
| C4 / Structurizr (rank 6) | 0.7 | medium |
| `AGENTS.md` / `CLAUDE.md` (rank 7) | 0.5 | medium |
| `CONTRIBUTING.md` section (rank 8) | 0.4 | low–medium |
| `.cursorrules` / Copilot instructions (rank 9) | 0.3 | low |
| Folder-structure convention (rank 10) | 0.3 | low |
| Sibling-file pattern only (rank 11) | 0.2 | low (and only in local-pattern mode) |

Findings with combined weight < 0.4 should be downgraded to comments, not
blocking findings.

---

## "Enough Anchor" Threshold

The agent picks its mode from the union of available signals:

| Available signals | Mode | What the agent does |
|---|---|---|
| Any rank 1–3 signal present, OR rank 4–5 with rank 7+ corroboration | **Full verifier** | Flag deviations from written rules. Cite specific lines from anchor docs. |
| Only rank 7–10 signals present (no ADRs, no `ARCHITECTURE.md`) | **Narrow local-pattern** | Compare changed file to siblings + relevant convention text only. No global "you're violating Clean Architecture" claims. |
| Only rank 11 (sibling files) usable, OR nothing | **Skip** | Emit status line: `"no architecture context found — agent skipped"`. Not a finding. |

**Rationale for the rank-4-needs-corroboration rule:** a lone `ARCHITECTURE.md`
in a repo with no other architecture signals is often a stale onboarding doc.
Pairing it with at least one `AGENTS.md` / `CONTRIBUTING.md` / `.cursorrules`
that mentions overlapping concepts is the cheapest live-ness check.

**Multi-anchor escalation:** if any single rank 1–3 signal exists for the path
being reviewed, that path is in full verifier mode regardless of what other
parts of the repo look like. Monorepos benefit from this — the .NET backend
with ADRs gets full verification while the React frontend with only a
`CONTRIBUTING.md` stays in local-pattern mode.

---

## Discovery Algorithm

Walk cheap-first. Stop early once mode is locked in.

```
function discover_anchors(repo_root, changed_paths) -> AnchorSet:

    # Stage 0 — explicit override (cheapest, decides everything)
    if exists(repo_root / "review.yml"):
        cfg = parse_yaml(...)
        if cfg.architecture_docs:
            return AnchorSet(
                mode = "full_verifier",
                docs = [read(p) for p in cfg.architecture_docs],
                source = "override",
            )

    anchors = []

    # Stage 1 — top-level markers (single stat() each)
    for path in [
        "ARCHITECTURE.md", "CONVENTIONS.md",
        "AGENTS.md", "CLAUDE.md",
        "CONTRIBUTING.md", "CONTRIBUTING.adoc", "CONTRIBUTING.rst",
        ".cursorrules", ".github/copilot-instructions.md",
    ]:
        if exists(repo_root / path):
            anchors.append(classify_and_load(path))

    # Stage 2 — ADR directories (one listdir each)
    for adr_root in [
        "docs/adr", "docs/architecture/decisions",
        "doc/adr", "adr", "architecture/decisions",
    ]:
        if isdir(repo_root / adr_root):
            anchors.extend(load_adrs(adr_root))  # filter Status: Accepted

    # Stage 3 — machine-enforced configs (cheap; massive payoff)
    for path in [
        ".dependency-cruiser.js", ".dependency-cruiser.cjs",
        ".dependency-cruiser.json", "dependency-cruiser.config.js",
    ]:
        if exists(repo_root / path):
            anchors.append(classify_as_rule(path))

    # ArchUnit / ts-arch / NetArchTest — these live in test files; detect by
    # presence of known imports in test directories. Skip if expensive.
    if grep_imports(repo_root, [
        "com.tngtech.archunit", "ts-arch", "NetArchTest",
    ], max_files=200):
        anchors.append(machine_enforced_marker())

    # Stage 4 — formal models (rare but strong)
    for pattern in ["**/workspace.dsl", "**/*.c4", "docs/arc42/*.adoc"]:
        for match in glob(pattern, limit=10):
            anchors.append(classify_and_load(match))

    # Stage 5 — ecosystem-specific structural reads (only if Stage 1–4 thin)
    if total_weight(anchors) < FULL_VERIFIER_THRESHOLD:
        anchors.extend(detect_dotnet_layering(repo_root))     # .sln + folder names
        anchors.extend(detect_node_workspaces(repo_root))     # nx.json, turbo.json, ...
        anchors.extend(detect_jvm_multimodule(repo_root))     # settings.gradle, pom.xml
        anchors.extend(detect_go_layout(repo_root))           # internal/, cmd/, pkg/
        anchors.extend(detect_rust_workspace(repo_root))      # Cargo workspace
        anchors.extend(detect_python_layout(repo_root))       # src/ + pyproject

    # Stage 6 — sibling-file fallback (only computed per-finding, not upfront)
    # The agent runs this *inline* when a changed file has no global anchor:
    #   load 3–5 nearest siblings, compare structure.

    return finalize(anchors, changed_paths)


function finalize(anchors, changed_paths) -> AnchorSet:
    weight = sum(a.weight for a in anchors)
    has_rule = any(a.cls == "rule" for a in anchors)
    has_strong = any(a.weight >= 0.7 for a in anchors)

    if has_rule and (weight >= 0.9 or has_strong):
        mode = "full_verifier"
    elif weight >= 0.3:
        mode = "narrow_local_pattern"
    else:
        mode = "skip"

    # Monorepo refinement: scope anchors to the path subtree they describe.
    # An ADR under packages/api/docs/adr/ only anchors changes under
    # packages/api/. Other subtrees fall back to their own anchor set.
    return scope_anchors_to_paths(anchors, changed_paths)
```

**Cost budget:** Stage 0–2 should hit < 30 stat/read calls in a normal repo.
Stage 5 only fires when prior stages are thin, so it's pay-as-you-go.

---

## Filtering: When `AGENTS.md` Is Not Architecture

`AGENTS.md` and `CLAUDE.md` are increasingly common (Next.js, Nx,
rust-analyzer, kubernetes all ship one as of 2026) but they vary wildly in
content. Some are pure "run `make test`" instructions; some include
architecture; some are aspirational.

Heuristic filter — only treat as a convention-class anchor if the file contains
at least one of:

- Heading matching `/architect|layer|module|boundar|conventions|structure/i`
- Imperative voice paragraph mentioning a folder name that exists in the repo
  (e.g. "All write paths go through `application/commands/`")
- A bulleted list of "do" / "don't" rules

Otherwise, classify as `build-instructions` and exclude from the anchor set.
The filter is applied per-section, not per-file — a file can contribute its
architecture section while its "how to run tests" section is ignored.

---

## Edge Cases

| Case | Behavior |
|---|---|
| **Multi-ecosystem monorepo** (e.g. .NET backend + React frontend) | Path-scope anchors. Run discovery per top-level package directory. A changed file in `apps/api/` uses the .NET-side anchors; `apps/web/` uses the JS-side. |
| **Stale `ARCHITECTURE.md`** referring to renamed folders | Detect: if > 50% of folder names mentioned in the doc don't exist in the repo, demote from rule to "potentially stale convention" (weight × 0.3). Cite the staleness in any finding that uses it. |
| **`AGENTS.md` containing only Claude Code instructions** | Filter rule above catches it. Excluded from anchor set; agent does not skip outright because other signals may still apply. |
| **ADR directory exists but all are `Status: Proposed` or `Superseded`** | No accepted decisions = no rule-class anchor. Demote to convention class (weight 0.5). |
| **Repo has dependency-cruiser config but it's empty/default** | Don't trust the config alone — also check for `forbidden` / `allowed` rules in it. Empty config = no anchor. |
| **`docs/` with old C4 diagrams but no text** | Diagrams without text don't anchor LLM findings (the agent can't reliably read PlantUML/Mermaid). Treat as structural hint only. |
| **Conflicting docs** (ADR says "use CQRS", `AGENTS.md` says "keep it simple, no CQRS") | Higher rank wins, but the finding must mention both sources. Surface the conflict — teams need to know their docs disagree. |
| **`.cursorrules` is aspirational** ("we're moving to hexagonal") | Rank 9 weight is already low; treat as soft convention. Don't flag deviations as findings, only as comments. |
| **Repo with no docs but heavy Clean Architecture folder naming** | Detected by Stage 5 .NET layering. Mode = `narrow_local_pattern`. Findings phrased as "this file is in `Infrastructure/` but imports from `Web/`, which sibling files don't do" — never as "you're violating Clean Architecture". |
| **Sibling-file mode false positives** (e.g. one outlier file is the new pattern) | Require ≥ 3 siblings agreeing before flagging. Single-sibling comparison is too noisy. |
| **Path-scoped anchors with no scope match** (file under `tools/` that no anchor covers) | Skip silently for that file. Status line aggregates: `"3 of 12 changed files had no architecture anchor"`. |

---

## Configuration Surface (`review.yml`)

New optional field, parsed by `src/pr_guardian/config/schema.py`:

```yaml
architecture_docs:
  # Optional. Explicit team-declared anchor files (rank 1, weight 1.0).
  # Overrides automatic discovery entirely when present.
  - docs/architecture.md
  - docs/adr/
  - packages/api/CONVENTIONS.md

architecture:
  # Optional finer-grained controls.
  mode_override: auto  # auto | full_verifier | narrow_local_pattern | skip
  path_scopes:
    "apps/api/**": [docs/adr/, apps/api/ARCHITECTURE.md]
    "apps/web/**": [apps/web/CONVENTIONS.md]
```

`mode_override` exists for the case where a team disagrees with the
auto-classifier — e.g. they want `narrow_local_pattern` even though they have
ADRs, because the ADRs are mid-rewrite.

---

## Open Questions

- **ADR format detection:** MADR vs. Nygard vs. ad-hoc. Worth a tolerant parser
  or just feed the whole file to the LLM and let it figure out? Lean toward
  the latter — robust to format drift, costs only tokens.
- **How to surface the staleness demotion in the verdict?** A finding citing
  a "potentially stale" anchor should be visibly weaker in the UI. Probably a
  badge on the finding card.
- **Caching anchor discovery across reviews of the same repo:** the anchor set
  changes slowly. A per-repo cache keyed on `(repo, head_sha_of_docs_dir)`
  would cut Stage 1–5 cost to zero for most reviews. Defer until measured.
- **C4 / PlantUML / Mermaid extraction:** worth doing? Diagrams encode
  architecture but LLMs read them unreliably. Probably extract text labels
  only, treat as a low-weight convention hint.
