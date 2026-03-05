# PR Guardian — Stage 0: Discovery

Lightweight context-gathering step that runs **before** everything else. Zero
analysis, zero AI — just collect the facts that every downstream stage needs.

**Time**: <5 seconds
**Dependencies**: shallow clone complete
**Output**: `ReviewContext` — a single object consumed by all four stages

---

## Why a Separate Stage

Language detection, config loading, and diff parsing were previously scattered
across the overview (Stage 2), multi-language doc (Triage), and architecture doc
(pre-step). This caused a sequencing contradiction: Stage 1 (Mechanical Gates)
needs the language map to know which tools to run, but language detection was
defined as part of Stage 2 (Triage).

Stage 0 resolves this by making context-gathering explicit and first.

---

## What Discovery Produces

```python
@dataclass
class ReviewContext:
    """Built once in Stage 0, consumed by all downstream stages."""

    # PR identity
    pr: PlatformPR                    # from webhook normalization
    repo_path: Path                   # shallow clone location

    # Diff analysis
    diff: Diff                        # raw diff object
    changed_files: list[str]          # all files in the diff
    lines_changed: int                # total added + removed

    # Language detection
    language_map: LanguageMap         # files grouped by language
    primary_language: str             # most-changed language by line count
    cross_stack: bool                 # >1 runtime language touched

    # Repo configuration
    repo_config: RepoConfig           # parsed review.yml (or defaults)
    repo_risk_class: RepoRiskClass    # standard | elevated | critical

    # Pre-computed lookups (from DB / config)
    hotspots: set[str]                # file paths flagged as hotspots
    security_surface: SecuritySurface # file → security classification map

    # Blast radius (transitive risk)
    blast_radius: BlastRadius         # changed files → their consumers + risk propagation

    # Change profile (semantic classification)
    change_profile: ChangeProfile     # what kind of change this is + implied review needs
```

Every field is deterministic. No heuristics, no scoring, no decisions.

---

## Discovery Steps

```
  Webhook arrives
       │
       ▼
  Platform adapter normalizes → PlatformPR
       │
       ▼
  ┌─────────────────────────────────────────────────┐
  │  STAGE 0: DISCOVERY                    (<5s)     │
  │                                                  │
  │  0.1  Parse diff → changed_files, lines_changed  │
  │  0.2  Detect languages → LanguageMap             │
  │  0.3  Load repo config → RepoConfig              │
  │  0.4  Load hotspots from DB                      │
  │  0.5  Build security surface map                 │
  │  0.6  Compute blast radius (transitive risk)     │
  │  0.7  Build change profile (semantic classify)   │
  │                                                  │
  │  Output: ReviewContext                           │
  └───────────────────────┬─────────────────────────┘
                          │
                          ▼
                  Stage 1: Mechanical Gates
                  (uses ReviewContext.language_map
                   to select which tools to run)
```

---

### 0.1 Parse Diff

```python
diff = git_diff(repo_path, target_branch)
changed_files = diff.file_paths()
lines_changed = diff.lines_added + diff.lines_removed
```

Input is the shallow clone. Output is the raw diff plus summary stats. No
interpretation — just structured data.

---

### 0.2 Detect Languages

File extension mapping. No heuristics, no parsing file contents.

```python
LANG_MAP = {
    ".py":     "python",
    ".ts":     "typescript",   ".tsx":    "typescript",
    ".js":     "javascript",   ".jsx":    "javascript",
    ".cs":     "csharp",
    ".go":     "go",
    ".java":   "java",         ".kt":     "kotlin",
    ".rs":     "rust",
    ".sql":    "sql",
    ".tf":     "terraform",    ".bicep":  "bicep",
    ".sh":     "shell",        ".bash":   "shell",
    ".ps1":    "powershell",
    ".yaml":   "yaml",         ".yml":    "yaml",
    ".json":   "json",
    ".xml":    "xml",          ".csproj": "xml",
    ".md":     "markdown",
    # Dockerfile — matched by filename, not extension
}

def detect_languages(changed_files: list[str]) -> LanguageMap:
    """Group changed files by language. ~0ms, pure dict lookup."""
    groups: dict[str, list[str]] = {}
    for path in changed_files:
        lang = identify_language(path)  # extension lookup + filename match
        groups.setdefault(lang, []).append(path)

    primary = max(groups, key=lambda l: len(groups[l]))
    runtime_langs = {l for l in groups if l in RUNTIME_LANGUAGES}

    return LanguageMap(
        languages=groups,
        primary_language=primary,
        language_count=len(groups),
        cross_stack=len(runtime_langs) > 1,
    )

# Runtime languages (affect cross_stack flag)
# Config/data formats (yaml, json, markdown) don't count
RUNTIME_LANGUAGES = {
    "python", "typescript", "javascript", "csharp", "go",
    "java", "kotlin", "rust", "sql", "shell", "powershell",
}
```

**Primary language**: determined by which language has the most changed files in
the diff. Used for prompt composition (primary language sections loaded first).

---

### 0.3 Load Repo Config

```python
def load_repo_config(repo_path: Path) -> RepoConfig:
    """Load review.yml from repo root, merge with service defaults."""
    repo_config_path = repo_path / "review.yml"
    if repo_config_path.exists():
        repo_overrides = yaml.safe_load(repo_config_path.read_text())
    else:
        repo_overrides = {}

    return merge_config(
        base=load_service_defaults(),   # config/defaults.yml
        overrides=repo_overrides,
    )
```

Config resolution order:
1. Service-level defaults (`config/defaults.yml`) — includes provider registry
2. Per-repo overrides (`review.yml` in repo root) — selects provider by name

Repo config determines: which mechanical tools are enabled per language, risk
class, thresholds, agent weights, auto-approve rules, and which LLM provider
to use (resolved against the service-level provider registry — see
[07-architecture.md](07-architecture.md#provider-configuration)).

---

### 0.4 Load Hotspots

```python
def load_hotspots(repo: str) -> set[str]:
    """Fetch pre-computed hotspot file paths from database."""
    # Hotspots are computed nightly (see 09-operations.md)
    # Not computed per-PR — just a DB lookup
    return db.get_hotspot_paths(repo)
```

Returns the set of file paths above the 80th percentile hotspot score for this
repo. If no hotspot data exists yet (new repo), returns empty set — triage
treats all files as non-hotspots.

---

### 0.5 Build Security Surface Map

```python
def build_security_surface(
    repo_config: RepoConfig, changed_files: list[str]
) -> SecuritySurface:
    """Match changed files against security surface patterns from config."""
    surface = SecuritySurface()
    patterns = repo_config.security_surface  # from review.yml

    for file_path in changed_files:
        for classification, globs in patterns.items():
            if matches_any(file_path, globs):
                surface.classify(file_path, classification)

    return surface
```

Uses the glob patterns from `review.yml` (or defaults). Classifications:
`security_critical`, `input_handling`, `data_access`, `configuration`,
`infrastructure`. Files can have multiple classifications.

---

### 0.6 Compute Blast Radius

A changed file's risk isn't just about the file itself — it's about **what
depends on it**. A 3-line change to `shared/utils/auth-helper.ts` is
security-critical if `middleware/auth.ts` imports it, even though the changed
file path doesn't match `**/middleware/auth*`.

```python
@dataclass
class BlastRadius:
    """Maps changed files to their downstream consumers and propagated risk."""

    # Direct consumers: file → set of files that import/reference it
    consumers: dict[str, set[str]]

    # Propagated classifications: file → set of security classifications
    # inherited from its consumers (not just its own path)
    propagated_surface: dict[str, set[str]]

    # Summary flags
    touches_shared_code: bool         # changed file has >N consumers
    propagates_to_security: bool      # any changed file's consumers are security-critical
    propagates_to_api: bool           # any changed file's consumers are API/input handlers


def compute_blast_radius(
    changed_files: list[str],
    security_surface: SecuritySurface,
    dep_graph: DependencyGraph,       # pre-computed or from repo config
) -> BlastRadius:
    """
    For each changed file, find its consumers in the dep graph.
    If any consumer has a security classification, propagate that
    classification back to the changed file.
    """
    result = BlastRadius(consumers={}, propagated_surface={})

    for file_path in changed_files:
        file_consumers = dep_graph.get_consumers(file_path)
        result.consumers[file_path] = file_consumers

        # Propagate: if consumer is security_critical, the changed file
        # inherits that risk even if the file itself is in shared/utils/
        propagated = set()
        for consumer in file_consumers:
            classifications = security_surface.get_classifications(consumer)
            propagated.update(classifications)
        if propagated:
            result.propagated_surface[file_path] = propagated

    result.touches_shared_code = any(
        len(c) > 3 for c in result.consumers.values()
    )
    result.propagates_to_security = any(
        "security_critical" in cs
        for cs in result.propagated_surface.values()
    )
    result.propagates_to_api = any(
        "input_handling" in cs
        for cs in result.propagated_surface.values()
    )

    return result
```

**Dependency graph sources** (in priority order):
1. **Repo config** — `review.yml` can declare `critical_consumers` mappings
   for repos that don't have static analysis tooling
2. **Pre-computed graph** — nightly job runs language-specific import analysis
   (e.g., `dependency-cruiser` for JS/TS, `pydeps` for Python, Roslyn for C#)
   and stores the graph in DB
3. **Fallback** — if no dep graph is available, blast radius returns empty
   consumers (triage falls back to direct file classification only)

The dependency graph is **not** computed per-PR (too slow). It's a
pre-computed lookup, same as hotspots.

---

### 0.7 Build Change Profile

Classifies **what kind of change this PR is** — not just which files changed
but what the change means. This drives triage decisions: a pure test addition
and a 5-line auth middleware edit are fundamentally different reviews.

```python
@dataclass
class ChangeProfile:
    """Semantic classification of what this PR changes."""

    # File-level classifications (each changed file tagged)
    file_roles: dict[str, set[FileRole]]
    # FileRole enum: production, test, docs, config, infra,
    #                generated, build, dependency

    # Aggregate flags (derived from file_roles + security_surface + blast_radius)
    has_production_changes: bool       # any non-test, non-doc, non-generated files
    has_test_changes: bool
    has_docs_only: bool                # ALL changes are docs/comments/markdown
    has_config_only: bool              # ALL changes are config with no prod code
    has_generated_only: bool           # ALL changes are migrations/lockfiles/generated

    # Risk-relevant signals (NOT line counts)
    touches_security_surface: bool     # direct OR via blast radius
    touches_api_boundary: bool         # controllers, handlers, API specs
    touches_data_layer: bool           # models, repositories, queries
    touches_shared_code: bool          # from blast_radius.touches_shared_code
    adds_dependencies: bool            # new packages added
    adds_api_endpoints: bool           # new routes/controllers
    crosses_architecture_boundary: bool # changes span >1 bounded context

    # What review steps this change implies
    implied_agents: set[str]           # agents that MUST run regardless of tier
    skip_agents: bool                  # true = trivial, skip all agents


def build_change_profile(
    changed_files: list[str],
    diff: Diff,
    security_surface: SecuritySurface,
    blast_radius: BlastRadius,
    repo_config: RepoConfig,
) -> ChangeProfile:
    """
    Classify each file by role, then derive aggregate signals.
    This replaces line-count as the primary triage input.
    """
    file_roles = classify_file_roles(changed_files, repo_config)

    profile = ChangeProfile(file_roles=file_roles)

    # Aggregate from file roles
    profile.has_production_changes = any(
        FileRole.production in roles for roles in file_roles.values()
    )
    profile.has_docs_only = all(
        roles <= {FileRole.docs} for roles in file_roles.values()
    )
    # ... etc

    # Risk signals: combine direct surface + blast radius propagation
    profile.touches_security_surface = (
        security_surface.has_hits()
        or blast_radius.propagates_to_security
    )
    profile.touches_shared_code = blast_radius.touches_shared_code
    # ... etc

    # Implied agents: driven by WHAT changed, not HOW MUCH
    if profile.touches_security_surface:
        profile.implied_agents.add("security_privacy")
    if profile.touches_api_boundary:
        profile.implied_agents.add("security_privacy")
        profile.implied_agents.add("performance")
    if profile.touches_data_layer:
        profile.implied_agents.add("performance")
    if profile.crosses_architecture_boundary:
        profile.implied_agents.add("architecture_intent")

    # Trivial shortcut
    profile.skip_agents = (
        profile.has_docs_only
        or profile.has_generated_only
        or (profile.has_config_only and diff.lines_changed < 5)
    )

    return profile
```

**File role classification** uses repo config patterns + conventions:

```yaml
# review.yml — per-repo file role overrides
file_roles:
  test_patterns: ["**/tests/**", "**/*.test.*", "**/*.spec.*"]
  docs_patterns: ["**/*.md", "**/docs/**", "CHANGELOG*"]
  generated_patterns: ["**/migrations/**", "**/package-lock.json", "**/*.lock"]
  build_patterns: ["**/Dockerfile*", "**/Makefile", "**/*.csproj"]
```

Falls back to language-specific conventions if not configured.

---

## What Consumes ReviewContext

| Stage | What it uses from ReviewContext |
|-------|-------------------------------|
| **Stage 1: Mechanical Gates** | `language_map` (which tools to run), `repo_config` (which tools enabled), `changed_files` (scope), `repo_path` (working dir) |
| **Stage 2: Triage** | `change_profile` (primary signal — what changed, implied agents, skip flag), `blast_radius` (transitive risk), `hotspots` (hotspot hits), `security_surface` (direct surface hits), `repo_risk_class` (tier amplifier), `language_map` (cross-stack amplifier). `lines_changed` used only as minor signal within context. |
| **Stage 3: AI Agents** | `language_map` (prompt composition), `security_surface` + `blast_radius` (agent selection + context), `hotspots` (agent selection), `change_profile.implied_agents` (mandatory agents), `repo_config` (agent weights/models) |
| **Stage 4: Decision Engine** | `repo_risk_class` (auto-approve rules), `repo_config` (thresholds, weights), `change_profile` (context for decision rationale) |

---

## Implementation Notes

- **No new package needed** — discovery logic lives in `core/orchestrator.py`
  as the first step of `run_review()`. The `ReviewContext` dataclass lives in
  `models/context.py`.
- **Language detection** stays in `languages/detector.py` (already exists in the
  package structure). Discovery just calls it.
- **Idempotent** — running discovery twice with the same inputs produces
  identical output.
- **Testable in isolation** — `ReviewContext` can be constructed in tests without
  a real repo clone (inject changed_files + config directly).
