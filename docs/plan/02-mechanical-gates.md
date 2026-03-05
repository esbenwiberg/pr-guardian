# PR Guardian — Stage 1: Mechanical Gates

Deterministic, fast (<2 min), no AI needed. Any hard fail blocks the PR before
agents are invoked.

---

## CI-Owned Checks (Not Guardian's Scope)

Build, tests, and SonarCloud are handled by the CI pipeline before Guardian
runs. Guardian does not duplicate, poll, or consume results from these checks.
They are independent required PR status checks.

---

## Guardian's Mechanical Checks

### 1.1 Security SAST — Semgrep

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

### 1.2 Secret Detection — Gitleaks

```yaml
# Pre-commit AND CI
# Scans diff only (not full history on every PR)
# Hard-fail on any detected secret
```
- **Catches**: API keys, passwords, tokens, connection strings, private keys
- **Gate**: HARD BLOCK — no exceptions
- **Time**: ~5s

### 1.3 Supply Chain — Socket.dev or Snyk

```yaml
# Only runs when package.json / requirements.txt / *.csproj changes
# Flags: known CVEs, typosquatting, install scripts, excessive permissions
```
- **Catches**: Vulnerable deps, malicious packages, AI-hallucinated package names
- **Gate**: BLOCK on critical CVE, WARN on medium
- **Time**: ~15s

### 1.4 API Breaking Change Detection (warn-only)

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

### 1.5 PII / Data Classification Scanner

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
