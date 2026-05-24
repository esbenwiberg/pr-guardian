---
description: Review a pending change (branch diff or staged hunks) the way a human reviewer would.
---

Use this on a feature branch before pushing, or on a checkout of someone
else's PR. The output is a verdict + findings, structured like the AI agents
PR Guardian itself runs — so the user can dogfood the product.

Inputs:

- Default: diff between `HEAD` and the merge-base with `main`.
- If the user names a base ref, diff against that instead.
- If only staged changes exist (no commits ahead of main), diff `--staged`.

Steps:

1. `git fetch origin main` (quiet — ignore failures, the user might be
   offline).
2. `git merge-base HEAD origin/main` → base ref.
3. `git diff <base>...HEAD --stat` then `git diff <base>...HEAD` to see
   shape and content. Skim the stat first; read full diff only for files that
   look load-bearing.
4. For each changed file, ask:
   - **Security & Privacy** — new attack surface? auth touched? secrets,
     PII, or input that crosses a trust boundary?
   - **Architecture & Intent** — does the diff match the commit subject? new
     coupling that violates layer boundaries in `ARCHITECTURE.md`?
   - **Test Quality** — code change without test change is a yellow flag;
     test-only change is fine. Mocks where integration would catch more?
   - **Performance** — N+1, unbounded loops, sync IO on async path?
   - **Code Quality** — dead code, swallowed errors, missing structured
     logging, comments that explain *what* not *why*.
5. Produce findings as `{severity, agent, file:line, evidence, suggestion}`.
   Severity is `info | low | medium | high | critical`. No finding without
   evidence — quote the offending line.
6. Final verdict: `approve` | `request_changes` | `block`.
   - `approve` — no high/critical, no medium without justification.
   - `request_changes` — medium+ that the author can fix in-place.
   - `block` — critical, missing auth on a protected route, secret in diff,
     mechanical gate would fail (semgrep, gitleaks).

Do not auto-fix. Reviewers report; authors fix. Exception: typos in comments
you're already reading can be flagged inline without a finding.
