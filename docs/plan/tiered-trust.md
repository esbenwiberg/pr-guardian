# Tiered Trust: Change-Type-Aware Review Governance

> The question isn't "AI or human?" — it's "what level of trust for what kind of change?"

## Problem

PR Guardian currently makes a binary decision: **auto-approve** or **request human review**.
Every human review request looks the same — same label, same reviewer group, same comment format.
A reviewer flagged for an auth middleware change gets the same experience as one flagged for
a new CRUD endpoint. This wastes human attention on low-risk changes and under-prepares
reviewers for high-risk ones.

## Core Idea

Introduce a **trust tier** — a new dimension orthogonal to risk tier — that classifies
*who should review* based on *what kind of code changed*. The AI adapts its role accordingly:
at high trust tiers it acts as the **preliminary reviewer** (catching issues before humans
see the PR), while at low trust tiers it becomes a **review tool** (preparing focused
briefings so the human reviewer is more effective).

```
              Risk Tier                    Trust Tier
              ─────────                    ──────────
              How hard should              Who needs to review,
              the AI look?                 and how should AI help them?

              TRIVIAL → skip agents        AI_ONLY → AI decides
              LOW → minimal agents         SPOT_CHECK → AI approves, human glances
              MEDIUM → standard agents     MANDATORY_HUMAN → AI briefs, human decides
              HIGH → all agents            HUMAN_PRIMARY → human leads, AI assists
```

## How Trust Tiers Are Classified

Trust tier classification uses a **path-first, LLM-escalation** strategy. Path patterns
provide a fast, deterministic, auditable floor. The AI agents can only raise the tier
after analyzing the actual diff content — never lower it.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 0: Path Classification (deterministic, zero-cost)           │
│                                                                     │
│  Each changed file matched against trust tier patterns from config. │
│  PR-level tier = highest (least trusting) tier across all files.    │
│                                                                     │
│  Example:                                                           │
│    README.md           → ai_only                                    │
│    src/api/users.py    → spot_check                                 │
│    src/auth/tokens.py  → human_primary      ← PR tier = this one   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stages 1-3: Normal pipeline runs (mechanical → triage → agents)   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 4: LLM-Assisted Escalation (post-agents, one-way upward)    │
│                                                                     │
│  After agents run, check their findings for trust-relevant signals  │
│  in files that the path rules classified as low-sensitivity.        │
│                                                                     │
│  Escalation triggers:                                               │
│    • Agent finding with category matching security keywords         │
│      (auth, permission, token, credential, encryption, RBAC)        │
│      in a file classified below MANDATORY_HUMAN                     │
│      → escalate that file (and PR) to MANDATORY_HUMAN               │
│                                                                     │
│    • Agent verdict = flag_human on any agent                        │
│      → escalate to at least MANDATORY_HUMAN                         │
│                                                                     │
│    • Critical severity + detected certainty finding                 │
│      → escalate to at least MANDATORY_HUMAN                         │
│                                                                     │
│  Escalation NEVER lowers the tier. A file matched as human_primary  │
│  by path rules stays human_primary even if the AI finds nothing.    │
└─────────────────────────────────────────────────────────────────────┘
```

### Why path-first?

- **Deterministic and auditable.** "Why was this flagged for security review?" →
  "Because it matched `**/auth/**` in your trust tier config." No LLM black box.
- **Fast and free.** Runs in microseconds with zero API calls. Available before any
  agents execute, so it can influence which agents run and how the pipeline behaves.
- **Configurable per-repo.** Teams own their tier patterns in `.pr-guardian.yml`.
  A payments service makes `**/api/**` mandatory-human. A docs repo makes everything
  ai-only.

### Why LLM escalation on top?

Path patterns rely on naming conventions. They fail when:

- Auth logic lives in `src/utils/helpers.py` (not in an `auth/` directory)
- A permission check is added to an existing controller file
- A developer introduces a raw SQL query in a file the path rules consider safe
- Crypto operations appear in a service file not under `crypto/`

The LLM agents already analyze the diff content. After they run, we inspect their
findings for **security-relevant categories** (auth, permissions, tokens, credentials,
encryption, SQL injection, etc.). If an agent flags something security-related in a
file that path rules classified as `AI_ONLY` or `SPOT_CHECK`, the trust tier escalates.

### Escalation rules

| Trigger | Minimum Tier After Escalation |
|---|---|
| Security-category finding in low-tier file | `MANDATORY_HUMAN` |
| Agent verdict `flag_human` | `MANDATORY_HUMAN` |
| Critical severity + detected certainty | `MANDATORY_HUMAN` |
| Repo risk class = `critical` | `MANDATORY_HUMAN` |

Escalation is logged in the pipeline log and shown in the PR comment:

> Trust tier escalated from SPOT_CHECK to MANDATORY_HUMAN: security agent found
> auth-related logic in `src/utils/helpers.py:89` (permission check pattern detected).

### What the LLM does NOT do

- The LLM never **lowers** a trust tier. Path rules set the floor.
- The LLM never **chooses** a trust tier directly. It produces findings with categories
  and severities; the escalation rules (deterministic) interpret those findings.
- There is no separate "trust classification" LLM call. The existing agents already
  produce the signal — we just inspect their output through a trust-tier lens.

This keeps the system predictable: path rules are always respected, and the LLM can only
add caution, never remove it.

---

## Trust Tiers

### Tier 1: AI_ONLY (High trust in AI)

**Change types:** Formatting, dependency lock files, docs, generated code, changelog updates.

**AI role:** Preliminary reviewer — sole decision-maker.

**Behavior:**
- Auto-approve if mechanical checks pass and AI agents find nothing notable.
- Standard PR comment with findings summary.
- No human reviewer requested.

**Label:** `guardian-approved`

---

### Tier 2: SPOT_CHECK (Medium trust)

**Change types:** Standard CRUD operations, test files, new dependencies, simple config changes.

**AI role:** Preliminary reviewer — but flags the PR for optional human attention.

**Behavior:**
- Auto-approve (don't block merge), but request a reviewer and add a spot-check label.
- PR comment includes a short "spot-check summary" — 2-3 sentences on what changed and
  what the AI checked, so a human can glance in 30 seconds.
- If the human doesn't review within a configurable window, that's okay — AI already approved.

**Label:** `guardian-spot-check`

**Spot-check summary example:**
> **Spot-check requested** — This PR adds a new `UserController` endpoint and updates the
> user model with an `email_verified` field. AI verified: input validation present, test
> coverage adequate, no SQL injection patterns. Suggested focus: verify the email
> verification flow matches business requirements.

---

### Tier 3: MANDATORY_HUMAN (Low trust)

**Change types:** Business logic, auth middleware, infrastructure (Terraform, Docker, k8s),
environment configuration, CI/CD pipelines.

**AI role:** Review tool — does a thorough first-pass and prepares a **reviewer briefing**
so the human starts with context instead of from scratch.

**Behavior:**
- Never auto-approve. Always `HUMAN_REVIEW`.
- AI runs full analysis (all agents), then produces a structured briefing for the reviewer.
- PR comment leads with the briefing, not the AI's verdict.
- Merge is blocked until a human approves.

**Label:** `needs-human-review`

---

### Tier 4: HUMAN_PRIMARY (Very low trust)

**Change types:** Security-critical code — auth, crypto, permissions/RBAC, OAuth/JWT,
payment processing, secrets management, security middleware.

**AI role:** Review tool in support mode — AI assists but defers to human expertise.

**Behavior:**
- Never auto-approve. Always `HUMAN_REVIEW`.
- Routes to a specific **reviewer group** (e.g., `security-team`) rather than the default.
- AI produces the most detailed briefing with security-specific checklists.
- PR comment explicitly states this requires specialist review and why.
- Merge blocked until the designated reviewer group approves.

**Label:** `needs-security-review`

---

## Helping the Human Reviewer

This is the highest-leverage part of the feature. When a human is pulled in, the AI should
make their review as fast and focused as possible. The output changes based on trust tier.

### Reviewer Briefing (for MANDATORY_HUMAN and HUMAN_PRIMARY)

Instead of just listing findings, the AI produces a structured briefing:

```markdown
## Reviewer Briefing

### What Changed
3 files modified in `src/auth/` — token validation middleware refactored
to support refresh token rotation.

### Why It Matters
- Changes authentication flow for all authenticated endpoints
- Modifies token expiry logic (previously 1h, now configurable)
- Adds a new `refresh_token` database column

### What the AI Already Checked
- [x] No hardcoded secrets or credentials (gitleaks: pass)
- [x] No SQL injection patterns (semgrep: pass)
- [x] Input validation present on new endpoints
- [x] Token expiry bounds checked (not unbounded)
- [ ] Could not verify: refresh token revocation on password change
- [ ] Could not verify: race condition in concurrent token refresh

### Where to Focus Your Review
1. **src/auth/middleware.py:45-78** — Token rotation logic. AI is uncertain
   whether the old token is invalidated atomically with new token creation.
   If not, there's a window for token reuse.

2. **src/auth/models.py:23** — New `refresh_token` column has no expiry.
   Intentional? Consider adding a TTL.

3. **src/auth/tests/test_refresh.py** — Tests cover happy path but not:
   concurrent refresh, expired refresh token, revoked user.

### Security Checklist (HUMAN_PRIMARY only)
- [ ] Token invalidation is atomic (no reuse window)
- [ ] Refresh tokens have bounded lifetime
- [ ] Token revocation propagates on password/permission change
- [ ] Rate limiting on refresh endpoint
- [ ] Audit logging for token rotation events
```

### Key Principles for Helping Reviewers

**1. Tell them where to look, not what to conclude.**
The AI shouldn't say "this is fine" or "this is wrong" for low-trust changes — it should
say "look at line 45, here's what I noticed, here's what I couldn't verify." The human
makes the judgment call.

**2. Separate "checked" from "couldn't check."**
The most valuable thing AI can do is shrink the review surface. If the AI verified that
there's no SQL injection pattern, the human can skip that concern and focus on the business
logic the AI can't evaluate. Clearly separating "verified" vs "needs human eyes" is the
highest-leverage output format.

**3. Generate domain-specific checklists.**
For HUMAN_PRIMARY reviews, generate a checklist tailored to the change type. Auth changes
get auth-specific items. Payment changes get PCI-relevant items. Infra changes get
blast-radius and rollback items. These checklists are generated from the security surface
classification + the specific files changed, not generic boilerplate.

**4. Provide historical context.**
"This file was last modified 6 months ago. It has been involved in 2 previous security
findings. The last reviewer was @alice." This helps the human understand whether they're
looking at well-trodden code or a dusty corner.

**5. Frame questions, not assertions.**
Instead of "this might be a vulnerability", frame as "does the old token get invalidated
before the new one is issued?" Questions engage the reviewer's expertise. Assertions invite
dismissal ("the AI doesn't understand the context").

**6. Respect the reviewer's time.**
For SPOT_CHECK: 30 seconds to scan. For MANDATORY_HUMAN: 5-minute briefing. For
HUMAN_PRIMARY: thorough briefing with checklist, but still structured so they can
prioritize. Never dump a wall of text.

---

## Configuration

### Trust tier rules in `.pr-guardian.yml`

```yaml
trust_tiers:
  default_tier: spot_check

  rules:
    # Tier 1: AI_ONLY — high trust
    - tier: ai_only
      patterns:
        - "**/*.md"
        - "**/docs/**"
        - "CHANGELOG*"
        - "**/package-lock.json"
        - "**/*.lock"
        - "**/migrations/**"
        - "**/.prettierrc*"
        - "**/.eslintrc*"
        - "**/generated/**"
      reason: "Formatting, docs, or generated files"

    # Tier 2: SPOT_CHECK — medium trust
    - tier: spot_check
      patterns:
        - "**/tests/**"
        - "**/*.test.*"
        - "**/*.spec.*"
        - "**/controllers/**"
        - "**/handlers/**"
        - "**/models/**"
        - "**/repositories/**"
      reason: "Standard CRUD, tests, or data access"

    # Tier 3: MANDATORY_HUMAN — low trust
    - tier: mandatory_human
      patterns:
        - "**/middleware/**"
        - "**/services/**"
        - "**/infra/**"
        - "**/terraform/**"
        - "**/docker/**"
        - "**/k8s/**"
        - "**/config/**"
        - "**/.env*"
        - ".github/workflows/**"
        - "**/Dockerfile*"
      reason: "Business logic, infrastructure, or configuration"

    # Tier 4: HUMAN_PRIMARY — very low trust
    - tier: human_primary
      patterns:
        - "**/auth/**"
        - "**/crypto/**"
        - "**/security/**"
        - "**/middleware/auth*"
        - "**/permissions/**"
        - "**/rbac/**"
        - "**/oauth/**"
        - "**/jwt/**"
        - "**/payments/**"
        - "**/billing/**"
        - "**/secrets/**"
      reason: "Security-critical code"
      reviewer_group: "security-team"

  # Reviewer group overrides per tier
  reviewer_groups:
    mandatory_human: ""           # uses default reviewer group
    human_primary: "security-team"

  # Spot-check grace period — how long before the spot-check is considered stale
  spot_check_window_hours: 48

  # LLM escalation: finding categories that trigger trust tier escalation.
  # If an agent produces a finding whose category matches any of these keywords
  # and the file's path-based tier is below mandatory_human, escalate.
  escalation_keywords:
    - auth
    - permission
    - token
    - credential
    - secret
    - encryption
    - crypto
    - rbac
    - role
    - privilege
    - session
    - injection
    - csrf
    - xss
    - cors
    - oauth
    - jwt
    - certificate
    - password
    - api_key
```

### Zero-config behavior: what happens with no trust tier config

Not every repo will have trust tiers configured. The system resolves this with a
three-layer fallback:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3: Explicit trust_tiers config in .pr-guardian.yml           │
│  Used when: repo defines trust_tiers.rules                          │
│  → Full control. Overrides everything below.                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ not defined?
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2: Derived from security_surface config                      │
│  Used when: repo has security_surface patterns but no trust_tiers   │
│  → Automatic tier inference from existing surface classifications.  │
│                                                                     │
│  Mapping:                                                           │
│    security_critical  → human_primary                               │
│    infrastructure     → mandatory_human                             │
│    configuration      → mandatory_human                             │
│    input_handling     → spot_check                                  │
│    data_access        → spot_check                                  │
│    no classification  → default_tier (spot_check)                   │
│                                                                     │
│  This gives teams tiered trust "for free" if they've already        │
│  configured their security surface for AI agent analysis.           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ not defined?
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: Built-in smart defaults (always available)                │
│  Used when: repo has no .pr-guardian.yml or no relevant config      │
│  → Common conventions that work across most codebases.              │
│                                                                     │
│  Defaults:                                                          │
│    **/*.md, **/docs/**, CHANGELOG*, **/*.lock,                      │
│    **/migrations/**, **/.prettierrc*, **/.eslintrc*                  │
│      → ai_only                                                      │
│                                                                     │
│    **/tests/**, **/*.test.*, **/*.spec.*,                           │
│    **/controllers/**, **/handlers/**, **/models/**,                 │
│    **/repositories/**                                               │
│      → spot_check                                                   │
│                                                                     │
│    **/middleware/**, **/services/**, **/infra/**,                    │
│    **/terraform/**, **/docker/**, **/k8s/**,                        │
│    **/config/**, **/.env*, .github/workflows/**                     │
│      → mandatory_human                                              │
│                                                                     │
│    **/auth/**, **/crypto/**, **/security/**,                        │
│    **/permissions/**, **/rbac/**, **/oauth/**,                      │
│    **/jwt/**, **/payments/**, **/billing/**                         │
│      → human_primary                                                │
│                                                                     │
│    everything else → spot_check (default_tier)                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Result:** A repo with zero config still gets useful tiered trust behavior.
Auth files get routed to human review. Docs get auto-approved. Standard code
gets spot-checked. Teams that already invested in security surface config
get an upgrade without extra work. Teams that want full control define
explicit rules.

**Implementation note:** Layer 2 (security surface derivation) is a one-time
mapping at classification time, not a runtime merge. The trust classifier
checks: "does the repo config have explicit `trust_tiers.rules`?" If yes,
use those. If no, check if `security_surface` has non-default patterns and
derive tier rules from them. If neither, use built-in defaults. This is a
simple priority chain in `classify_trust_tier()`.

### Interaction with existing config

Trust tier rules layer on top of existing security surface patterns:

| Existing Config | Purpose | Continues to... |
|---|---|---|
| `security_surface` | Tells AI agents *what to look at* | Drive agent selection and risk signals |
| `path_risk` | Overrides risk tier for specific paths | Control AI analysis depth |
| **`trust_tiers` (new)** | Controls *who reviews* and *how AI helps* | Govern human involvement |

They can share patterns (e.g., `**/auth/**` appears in both `security_surface.security_critical`
and trust tier `human_primary`), but they serve different purposes. Security surface feeds the
AI's analysis. Trust tier feeds the governance decision.

When Layer 2 derivation is active, the security surface config serves double duty —
driving both AI analysis depth and governance. This is intentional: if you told the AI
"these files are security-critical", it's reasonable to also require human review for them.

---

## Implementation Plan

### Phase 1: Path-Based Trust Tier Classification

**Files to create/modify:**

1. **`models/context.py`** — Add `TrustTier` enum and `TrustTierResult`:
   ```python
   class TrustTier(str, Enum):
       AI_ONLY = "ai_only"
       SPOT_CHECK = "spot_check"
       MANDATORY_HUMAN = "mandatory_human"
       HUMAN_PRIMARY = "human_primary"

   @dataclass
   class TrustTierResult:
       resolved_tier: TrustTier          # PR-level tier (highest across files)
       file_tiers: dict[str, TrustTier]  # per-file classification
       triggering_files: list[str]       # files that set the PR-level tier
       reasons: list[str]                # human-readable explanations
       reviewer_group_override: str | None
       escalated: bool = False           # true if LLM escalated above path tier
       escalation_reasons: list[str]     # why escalation happened
   ```

2. **`config/schema.py`** — Add `TrustTierRule` and `TrustTierConfig` models.

3. **`triage/trust_classifier.py`** (new) — Path-based classification:
   - `classify_trust_tier(changed_files, config) -> TrustTierResult`
   - Match each file against trust tier patterns (highest sensitivity wins per file).
   - PR-level tier = max across all files.
   - Return per-file breakdown so briefings can show which files triggered which tier.

4. **`models/context.py`** — Add `trust_tier_result` field to `ReviewContext`.

5. **`core/orchestrator.py`** — Call trust classifier in Stage 0 (Discovery),
   store result in context. This runs before agents so it's available for logging
   and triage decisions.

### Phase 2: LLM-Assisted Escalation + Decision Engine

6. **`triage/trust_escalation.py`** (new) — Post-agent escalation logic:
   - `maybe_escalate_trust(trust_result, agent_results, config) -> TrustTierResult`
   - Inspects agent findings for security-relevant categories in low-tier files.
   - Deterministic rules (not another LLM call): if a finding's category matches
     security keywords (auth, permission, token, credential, encryption, RBAC,
     injection, etc.) and the file's path-based tier is below MANDATORY_HUMAN,
     escalate the file and PR tier.
   - Sets `escalated=True` and populates `escalation_reasons` on the result.
   - Called in the orchestrator between Stage 3 (agents) and Stage 4 (decision).

7. **`decision/engine.py`** — `decide()` uses the (possibly escalated) trust tier:
   - `AI_ONLY` → allows auto-approve (current behavior).
   - `SPOT_CHECK` → auto-approve but set a flag for spot-check labeling.
   - `MANDATORY_HUMAN` → force `HUMAN_REVIEW` regardless of score.
   - `HUMAN_PRIMARY` → force `HUMAN_REVIEW` + set reviewer group override.
   - Add `trust_tier_result` to `ReviewResult`.

8. **`models/output.py`** — Extend `ReviewResult`:
   ```python
   trust_tier: TrustTier
   trust_tier_reasons: list[str]
   trust_tier_files: dict[str, str]    # file → tier that matched
   reviewer_group_override: str | None
   escalated_from: str | None          # original path-based tier if escalated
   ```

### Phase 3: Reviewer Briefing Generation

9. **`decision/briefing.py`** (new) — Generate trust-tier-appropriate output:
    - `AI_ONLY` / `SPOT_CHECK` → standard findings summary (current behavior).
    - `SPOT_CHECK` → add a 2-3 sentence spot-check summary.
    - `MANDATORY_HUMAN` → structured reviewer briefing (what changed, what AI
      checked, what needs human eyes, focus areas with file:line references).
    - `HUMAN_PRIMARY` → full briefing + domain-specific security checklist.

10. **`prompts/briefing/`** (new directory) — Prompt templates for briefing generation:
    - `reviewer_briefing.md` — system prompt for generating the briefing.
    - `security_checklist.md` — template for security-specific checklists.
    - The briefing is generated by a separate LLM call that takes the agent
      results as input and restructures them for human consumption.

### Phase 4: Comment, Label, and Routing

11. **`decision/actions.py`** — Differentiate PR comment and labels by trust tier:
    - New labels: `guardian-spot-check`, `needs-security-review`.
    - Comment structure changes based on tier (spot-check summary vs. full briefing).
    - Embed trust tier metadata for downstream tooling.

12. **`core/orchestrator.py`** — Route to correct reviewer group:
    - `HUMAN_PRIMARY` → `adapter.request_reviewers(pr, override_group)`.
    - `SPOT_CHECK` → `adapter.request_reviewers(pr, default_group)` without blocking.

13. **`persistence/models.py`** — Add `trust_tier` column to `reviews` table.

14. **Dashboard** — Show trust tier in review detail view with visual indicator.

### Phase 5: Checklist Generation

15. **`agents/checklist.py`** (new) — Generate domain-specific review checklists:
    - Input: security surface classifications + changed files + agent findings.
    - Output: checklist items tailored to the change type.
    - Checklist templates per domain: auth, payments, infra, data access.
    - LLM-generated items supplement static templates based on the specific diff.

16. **`decision/briefing.py`** — Integrate checklist into HUMAN_PRIMARY briefings.

---

## Decision Matrix (Updated)

How trust tier and risk tier interact:

```
                    AI_ONLY         SPOT_CHECK       MANDATORY_HUMAN    HUMAN_PRIMARY
                    ───────         ──────────       ───────────────    ─────────────
TRIVIAL             approve         approve+flag     human-review       human(security)
LOW                 approve         approve+flag     human-review       human(security)
MEDIUM              approve         approve+flag     human-review       human(security)
HIGH                human-review    human-review     human-review       human(security)

Risk tier can escalate trust tier:
  - HIGH risk always requires human review (even for AI_ONLY changes)
  - Agent findings can also escalate: a critical finding in AI_ONLY → MANDATORY_HUMAN
```

Trust tier can be escalated upward by two mechanisms:

**Path rules (Stage 0, before agents):**
- File matches a higher-tier pattern → tier set immediately
- Repo risk class `critical` → floor at MANDATORY_HUMAN

**LLM-assisted escalation (Stage 4, after agents):**
- Agent finding with security-relevant category in a low-tier file → MANDATORY_HUMAN
- Agent verdict `flag_human` → minimum MANDATORY_HUMAN
- Critical severity + detected certainty finding → minimum MANDATORY_HUMAN

Trust tier never lowers — it can only escalate. The LLM cannot override path rules
downward, even if it finds no issues in a security-critical file.

---

## PR Comment Examples

### AI_ONLY (current style, no change needed)

```markdown
## PR Guardian :white_check_mark: Auto-Approved

**Risk** LOW · **Score** 1.2/10 · **Mechanical** passed

- **Code Quality** — :white_check_mark: Pass
- **Test Quality** — :white_check_mark: Pass
```

### SPOT_CHECK

```markdown
## PR Guardian :white_check_mark: Auto-Approved

**Risk** MEDIUM · **Score** 2.8/10 · **Mechanical** passed
**Trust** SPOT_CHECK — reviewer spot-check requested

- **Security & Privacy** — :white_check_mark: Pass
- **Performance** — :warning: Warn
  N+1 query in UserController#index
- **Test Quality** — :white_check_mark: Pass

**Spot-check summary:** This PR adds a paginated user listing endpoint with
email search. AI verified input validation and SQL parameterization. Suggested
focus: verify the search query performance with large datasets — the N+1
pattern in the controller may cause latency under load.

@developers — spot-check requested, non-blocking.
```

### MANDATORY_HUMAN

```markdown
## PR Guardian :eyes: Human Review Required

**Risk** HIGH · **Score** 5.1/10 · **Mechanical** passed
**Trust** MANDATORY_HUMAN — AI first-pass complete, human approval required

### Reviewer Briefing

**What changed:** 4 files in `src/services/billing/` — subscription renewal
logic refactored to support annual billing cycles.

**What AI verified:**
- :white_check_mark: No credential exposure
- :white_check_mark: Input validation on billing amounts
- :white_check_mark: Idempotency key present on charge creation
- :grey_question: Could not verify: proration calculation correctness
- :grey_question: Could not verify: timezone handling for renewal dates

**Focus areas:**
1. `src/services/billing/renewal.py:89-134` — Proration math for mid-cycle
   upgrades. AI found the calculation but can't verify business correctness.
2. `src/services/billing/scheduler.py:45` — Renewal cron uses UTC but
   customer timezone stored separately. Verify alignment.
3. No test for annual→monthly downgrade path.

:mag: [Full findings & export for fix →](https://guardian.example.com/reviews/abc123)
```

### HUMAN_PRIMARY

```markdown
## PR Guardian :rotating_light: Security Review Required

**Risk** HIGH · **Score** 6.3/10 · **Mechanical** passed
**Trust** HUMAN_PRIMARY — security team review required

> **This PR modifies security-critical code.** AI has completed a preliminary
> analysis but this requires approval from @security-team before merge.
> Triggering files: `src/auth/middleware.py`, `src/auth/token_service.py`

### Reviewer Briefing

**What changed:** Token validation middleware refactored to support refresh
token rotation. New `refresh_token` column added to users table.

**What AI verified:**
- :white_check_mark: No hardcoded secrets (gitleaks: pass)
- :white_check_mark: No SQL injection (semgrep: pass)
- :white_check_mark: Token expiry bounds checked (not unbounded)
- :x: Concern: refresh token has no server-side expiry (see finding below)
- :grey_question: Could not verify: atomicity of token rotation
- :grey_question: Could not verify: revocation on password change

**Focus areas:**
1. `src/auth/middleware.py:45-78` — Token rotation. Is the old token
   invalidated atomically with new token issuance? If not, replay window.
2. `src/auth/models.py:23` — `refresh_token` column has no TTL. Intentional?
3. `src/auth/tests/test_refresh.py` — Missing: concurrent refresh, expired
   refresh token, revoked user scenarios.

### Security Checklist
- [ ] Token invalidation is atomic (no reuse window)
- [ ] Refresh tokens have bounded lifetime
- [ ] Token revocation propagates on password/permission change
- [ ] Rate limiting on refresh endpoint
- [ ] Audit logging for token rotation events
- [ ] Refresh token stored hashed, not plaintext

:mag: [Full findings & export for fix →](https://guardian.example.com/reviews/abc123)
```

---

## Open Questions

1. **Can trust tier be overridden in the PR description?**
   e.g., `[trust: ai_only]` to downgrade. Dangerous — an attacker could self-approve
   security changes. Recommendation: only allow upward overrides (requesting *more*
   review), never downward.

2. **How to handle mixed PRs?**
   A PR that touches both `README.md` (ai_only) and `src/auth/middleware.py`
   (human_primary) → entire PR is human_primary. But the briefing should note:
   "Only `src/auth/middleware.py` triggered security review. The README change is
   low-risk." This helps the reviewer focus.

3. **Briefing cost:** The reviewer briefing requires an additional LLM call after
   agents finish. For HUMAN_PRIMARY reviews this is clearly worth it. For
   MANDATORY_HUMAN it's probably worth it. For SPOT_CHECK, a heuristic summary
   (no extra LLM call) may suffice. Need to measure latency/cost impact.

4. **Escalation keyword tuning:** The keyword list for LLM-assisted escalation will
   need tuning per codebase. Too broad (e.g., "role" matches "user_role" column rename)
   and you get noisy escalations. Too narrow and you miss real issues. Consider
   requiring keyword + severity threshold (e.g., only escalate if the finding is
   MEDIUM+ severity, not just any mention of "auth").

5. **Feedback loop:** When a human reviewer overrides the AI's assessment (e.g.,
   AI said "pass" but human requests changes), should this feed back into trust tier
   calibration? Long-term yes, but out of scope for v1. Also: when LLM escalation
   triggers but the human reviewer finds nothing, should that reduce future escalation
   sensitivity? (Probably not in v1 — false negatives are worse than false positives
   for security.)
