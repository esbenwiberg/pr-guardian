# PR Guardian — Stage 3: AI Agent Specifications

Each agent is a focused LLM call with a specific system prompt, the PR diff as
context, and relevant repo context. They run in parallel. The LLM provider is
determined by repo config (see [07-architecture.md](07-architecture.md)).

---

## Agent Output Schema

All agents return the same structure:

```json
{
  "verdict": "pass | warn | flag_human",
  "languages_reviewed": ["python", "typescript"],
  "findings": [
    {
      "severity": "low | medium | high | critical",
      "certainty": "detected | suspected | uncertain",
      "category": "...",
      "language": "python",
      "file": "src/api/users.py",
      "line": 42,
      "description": "...",
      "suggestion": "...",
      "cwe": "CWE-89 | null",
      "evidence_basis": {
        "saw_full_context": true,
        "pattern_match": true,
        "cwe_id": "CWE-89",
        "similar_code_in_repo": false,
        "suggestion_is_concrete": true,
        "cross_references": 1
      }
    }
  ],
  "cross_language_findings": []
}
```

**No `risk_score` field.** Agents return structured findings with severity, certainty,
and evidence. The decision engine derives a numeric score deterministically from
findings — see [05-decision-engine.md](05-decision-engine.md). This prevents LLMs
from producing unreliable numeric confidence values while still enabling weighted
scoring.
```

**Certainty** is an enum, not a float:
- `detected` — concrete pattern, CWE, specific fix
- `suspected` — looks problematic, needs more context
- `uncertain` — area is risky, can't point to specific issue

The decision engine validates certainty against evidence (see [05-decision-engine.md](05-decision-engine.md)).

---

## 3.1 Security + Privacy Agent

**Trigger**: security-surface files touched, new deps, data model changes, HIGH tier

**Context fed to agent**:
- PR diff + security surface map
- Existing auth/authz patterns from codebase
- OWASP checklist relevant to changes
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

**Privacy / Data Checks**:
- PII flowing into log statements (names, emails, IPs, device IDs)
- New data fields without classification (is this PII? sensitive? public?)
- Data sent to third-party services without noting it in data flow docs
- Missing data retention considerations (storing data with no TTL/cleanup path)
- Consent implications (new data collection that may require user consent)
- Right-to-deletion impact (new PII storage must be deletable on request)
- Cross-border data flow (data leaving region via new external API calls)
- Test fixtures with realistic PII (use synthetic data instead)

**Example output**:
```json
{
  "verdict": "flag_human",
  "languages_reviewed": ["python"],
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
      "cwe": "CWE-89"
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
      "compliance": "GDPR Art. 5(1)(c) — data minimization"
    }
  ]
}
```

---

## 3.2 Performance Agent

**Trigger**: data-access files touched, API handlers, >100 lines, HIGH tier

**Context fed to agent**:
- PR diff + database schema (if available)
- Existing query patterns + performance-sensitive file list
- Languages detected in diff

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

---

## 3.3 Architecture + Intent Verification Agent

**Trigger**: multiple modules touched, new files created, new deps, HIGH tier

**Context fed to agent**:
- PR diff + CLAUDE.md / architecture docs + module boundary map
- Existing patterns from codebase (sampled) + decisions.md
- **Linked work item title + description** (ADO work items or GitHub Issues, via platform adapter)
- Languages detected in diff

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

**Intent Verification Checks**:
- Does the PR accomplish what the linked work item describes?
- Does the PR introduce functionality that already exists elsewhere?
- Does the approach conflict with recent architectural decisions?
- Is the scope appropriate? (doing more than work item asks → risk)
- Is the scope complete? (work item says X, Y, Z but PR only does X)

**Intent verification output**:
```json
{
  "intent_verification": {
    "work_item_id": "AB#12345",            // ADO: "AB#12345", GitHub: "#42"
    "work_item_source": "azure-devops",    // or "github"
    "work_item_title": "Add email notification for password reset",
    "alignment": "partial",
    "assessment": "PR adds notification but sends to hardcoded address instead of user's email.",
    "scope_match": "under — work item also mentions audit logging which is missing",
    "duplicate_detection": "Similar notification logic exists in src/services/alerts.ts:45"
  }
}
```

---

## 3.4 Code Quality + Observability Agent

**Trigger**: all tiers except TRIVIAL

**Context fed to agent**:
- PR diff + surrounding code context (full files, not just diff)
- Project conventions from CLAUDE.md + existing patterns in same module
- Existing logging/tracing patterns in the codebase (sampled)
- Languages detected in diff

**Code Quality Checks**:
- Readability (confusing naming, unclear intent, magic numbers)
- Error handling (swallowed errors, missing catch, unclear error messages)
- Edge cases (null/undefined handling, empty arrays, boundary conditions)
- DRY violations (copy-paste from elsewhere in codebase)
- Dead code introduction
- TODO/FIXME/HACK without linked issue
- Documentation gaps for public APIs

**Observability Checks**:
- New API endpoints without request/response logging
- New error paths without structured error context
- New service methods without trace span creation
- Missing correlation ID propagation in new async flows
- New background jobs/workers without health check endpoints
- New external API calls without timeout + retry logging
- Missing metrics for new business operations
- Error handling that loses stack traces (catch + rethrow without cause chain)

---

## 3.5 Test Quality Agent

**Trigger**: all tiers except TRIVIAL where code changes are present (not test-only PRs)

**Context fed to agent**:
- PR diff (both source and test files)
- Full content of new/modified test files + source files being tested
- Test coverage report if available (from Stage 1)
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
- **Missing negative tests**: Only happy-path tests → warn
- **Copy-paste tests**: Near-identical test methods → suggest parameterized test
- **Implementation coupling**: Tests that mirror implementation step-by-step instead of testing behavior → brittle
- **Untested new paths**: New code branches without corresponding test cases
- **Snapshot/golden file abuse**: Snapshot tests that nobody reads when they update → warn

**Extra output — test quality summary**:
```json
{
  "test_quality_summary": {
    "new_code_paths": 8,
    "tested_paths": 5,
    "untested_paths": ["error handler in process_payment:67", "retry logic in send_email:45"],
    "weak_assertions": 3,
    "assertion_quality_score": 0.6,
    "overall": "Tests exist but 3 of 8 new code paths are untested, and 3 assertions are trivially weak."
  }
}
```

---

## 3.6 Hotspot Agent

**Trigger**: any file with hotspot_score > 80th percentile touched

**Context fed to agent**:
- PR diff + git history for touched hotspot files
- Bug history correlation (if available)
- Complexity metrics + full file content for hotspot files

**Checks**:
- Is the change making the hotspot worse (adding complexity)?
- Is the change properly tested given the file's bug history?
- Should this file be refactored instead of extended?
- Are there related hotspot files that should have been changed too?
- Risk assessment given historical churn rate

**Extra output — hotspot context**:
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
