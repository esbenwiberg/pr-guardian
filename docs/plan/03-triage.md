# PR Guardian — Stage 2: Triage Engine

The triage engine is a **deterministic script** (not AI) that classifies the PR
risk and decides which agents to invoke. This keeps costs down — a 1-line typo
fix shouldn't trigger 5 agent calls.

Triage consumes the `ReviewContext` built by Stage 0 (see
[01b-discovery.md](01b-discovery.md)). All inputs — language map, hotspots,
security surface, blast radius, change profile, repo config — are already
resolved before triage runs. The key inputs are `change_profile` (what kind
of change) and `blast_radius` (transitive risk), not line counts.

---

## Risk Tier Classification

Risk tiers are driven by **what changed**, not how many lines changed.
5 lines in auth middleware is more complex than 500 lines of unit tests.
Line count is a minor signal used within context, never the primary driver.

The primary input is `change_profile` from Stage 0 Discovery (see
[01b-discovery.md](01b-discovery.md#07-build-change-profile)).

```
TRIVIAL (skip agents entirely):
  Primary signal: change_profile.skip_agents = true
  - Only docs/comments/markdown changed (has_docs_only)
  - Only auto-generated files: migrations, lockfiles (has_generated_only)
  - Only config files with no production code (has_config_only + small diff)
  - Only test files changed with no production code
    (and no blast radius to production code)

LOW (run: code-quality agent only):
  Primary signal: no risk flags raised
  - No security surface touched (direct OR transitive)
  - No architecture boundaries crossed
  - No shared code with wide blast radius
  - No hotspot files
  - Not a new contributor
  - Production code changes are localized (single module)

MEDIUM (run: code-quality + change_profile.implied_agents):
  Primary signal: one or more risk flags, but contained
  - Touches data layer (models, queries) → +performance agent
  - Touches API boundary (controllers, handlers) → +security, +performance
  - Touches hotspot file → +hotspot agent
  - Shared code changed with moderate blast radius (3-10 consumers)
  - Multiple modules touched but within same bounded context

HIGH (run: all agents):
  Primary signal: security surface OR wide blast radius OR structural change
  - Touches security-critical paths (direct OR via blast radius)
  - Shared code with wide blast radius (>10 consumers)
  - Crosses architecture boundaries
  - New dependencies added
  - New API endpoints added
  - Author is non-dev / new contributor
  - Blast radius propagates to security-critical consumers

AMPLIFIERS (bump tier upward):
  Language-based:
    - cross_stack = true (>1 runtime language) → bump +1
    - language_count > 3 → force HIGH
    - sql changes present → always add security agent
    - terraform/bicep/dockerfile present → always add security agent
    - new language introduced to repo → force HIGH + flag_human

  Repo risk class:
    - repo_risk_class = elevated → bump +1 (low→medium, medium→high)
    - repo_risk_class = critical → force HIGH, all agents always run

  Repo config path weights (see below):
    - Files matching high-weight paths bump tier
    - Overrides default classification for repo-specific critical areas
```

### Why Not Line Count?

Line count is a **terrible** primary signal:
- 5 lines adding `bypassAuth: true` to middleware config → catastrophic
- 500 lines of new unit tests → near-zero risk
- 200 lines of auto-generated migration → near-zero risk
- 3 lines changing a shared validation function used by 40 files → high risk

Line count is only used as a **tiebreaker within the same tier** — e.g., two
MEDIUM PRs where one is 50 lines and another is 300 may get different agent
timeouts or context windows, but they're both MEDIUM because of what they
touch, not how big they are.

### Transitive Risk (Blast Radius)

The most subtle risk comes from changes to **shared code** that doesn't look
dangerous on its own. Example:

```
Changed file: shared/utils/validate-input.ts  (3 lines changed)
Direct classification: none (not in security surface paths)

But blast radius analysis shows:
  → imported by middleware/auth.ts          (security_critical)
  → imported by controllers/payment.ts      (security_critical)
  → imported by handlers/user-profile.ts    (input_handling)

Propagated classification: security_critical, input_handling
Result: this 3-line change is HIGH tier, security agent MUST run
```

Without blast radius, this change would be classified LOW (3 lines, single
file, no direct security surface match). With blast radius, the transitive
risk is correctly captured.

---

## Hotspot Detection

Pre-computed nightly (see [09-operations.md](09-operations.md)), loaded into
`ReviewContext` by Stage 0. Triage checks whether any changed files are in the
hotspot set.

```bash
# Git-based hotspot analysis (nightly job)
# For each file: change_frequency × cyclomatic_complexity = hotspot_score
# Files above 80th percentile are "hotspots" — get extra scrutiny
```

---

## Security Surface Map

Loaded into `ReviewContext` by Stage 0 from `review.yml` patterns. Triage
checks whether changed files match any security classification:

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

## Repo Risk Class

Loaded from `review.yml` by Stage 0 into `ReviewContext.repo_risk_class`:

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

---

## Repo Config: Path-Level Risk Weights

Beyond the global `repo_risk_class`, repos can declare **path-specific risk
weights** that override default classification. This is how repo-specific
knowledge enters triage — the team knows which files are truly critical even
if they don't match generic patterns.

```yaml
# review.yml — path-level risk configuration
path_risk:
  # Force specific paths to a minimum tier regardless of other signals
  critical_paths:
    - pattern: "src/billing/**"
      min_tier: high
      reason: "Payment processing — always full review"
    - pattern: "src/middleware/auth*"
      min_tier: high
      reason: "Authentication middleware"
    - pattern: "src/core/permissions/**"
      min_tier: medium
      reason: "Authorization logic"

  # Declare consumers for blast radius (supplements auto-detected dep graph)
  # Use this when the repo doesn't have dep-graph tooling or has implicit deps
  critical_consumers:
    "src/shared/validation.ts":
      - "src/middleware/auth.ts"
      - "src/controllers/payment.ts"
    "src/config/feature-flags.ts":
      - "src/middleware/**"

  # Lower risk for paths the team knows are safe to auto-approve
  safe_paths:
    - pattern: "src/scripts/one-off/**"
      max_tier: low
      reason: "One-off scripts, not deployed"
    - pattern: "tests/**"
      max_tier: trivial
      reason: "Test-only changes"
      condition: "no_production_changes"   # only applies if no prod files also changed
```

**Resolution**: `critical_paths` min_tier is applied **after** the base tier
calculation. If the base tier is already higher, it stays. `safe_paths`
max_tier is applied only when the condition is met and can be overridden by
amplifiers. Path risk is a floor/ceiling, not a replacement for the triage
algorithm.
