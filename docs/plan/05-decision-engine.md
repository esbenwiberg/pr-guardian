# PR Guardian — Stage 4: Decision Engine

The decision engine is deterministic — no AI. It applies rules to agent outputs.

---

## Certainty Validation

Agents return a `certainty` enum per finding, but the decision engine
**validates it against evidence** — agents can't claim high certainty without
showing their work.

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

---

## Scoring Model — Derived from Findings

Agents do NOT return a numeric risk score. The decision engine computes scores
deterministically from structured findings. This prevents LLMs from producing
unreliable confidence numbers.

### Per-Finding Score

```python
SEVERITY_SCORE = {"low": 1, "medium": 3, "high": 6, "critical": 10}
CERTAINTY_WEIGHT = {"detected": 1.0, "suspected": 0.5, "uncertain": 0.2}

def finding_score(finding: Finding) -> float:
    """Score a single finding based on validated certainty and severity."""
    validated = validated_certainty(finding)  # may downgrade
    return SEVERITY_SCORE[finding.severity] * CERTAINTY_WEIGHT[validated]
```

### Per-Agent Score

```python
def agent_score(result: AgentResult) -> float:
    """Derive agent risk score from its findings. Scale 0-10."""
    if not result.findings:
        return 0.0
    total = sum(finding_score(f) for f in result.findings)
    # Cap at 10, take the highest-impact finding into account
    return min(10.0, max(total / len(result.findings), max_finding_score(result)))
```

### Combined Score

```
combined_risk = Σ(agent_score × agent_weight) / Σ(agent_weight)

Agent weights:
  security_privacy_agent:   3.0  (highest — security + compliance most costly)
  test_quality_agent:       2.5  (bad tests = false confidence, very dangerous)
  architecture_intent_agent:2.0  (drift is expensive long-term)
  performance_agent:        1.5
  hotspot_agent:            1.5
  code_quality_obs_agent:   1.0  (least critical for auto-approve decision)

Thresholds (applied to combined_risk):
  < 4.0    →  auto-approve eligible (subject to decision matrix + overrides)
  >= 4.0   →  human review required
  >= 8.0   →  hard block (merge blocked entirely)
```

---

## Decision Matrix

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
```

---

## Override Rules (always HUMAN REVIEW regardless of above)

- Any finding with validated certainty = "detected" and severity >= medium
- >=3 findings with validated certainty = "suspected" at any severity
- Any agent verdict = "flag_human"
- Any agent saw_full_context = false (can't trust silence from blind agent)
- New external dependency added
- Author's first PR to this repo
- Intent verification alignment = "misaligned" or "partial" (ADO work items or GitHub Issues)
- Test quality agent finds >50% untested new code paths
- Privacy agent flags new PII storage or processing

---

## Auto-Approve Behavior

Note: auto-approve = Guardian votes "approve" and posts a summary comment.
**The author still clicks merge.** Guardian never merges automatically.

When auto-approving:
1. Add PR comment with full summary:
   - Which checks ran and their status
   - Which agents ran and their verdicts
   - Certainty-validated findings (if any, all low severity)
   - Risk tier, repo risk class, and score
2. Vote approve on the PR
3. Post notification (Teams/Slack) — "PR approved by Guardian, ready to merge"
4. Author clicks merge when ready

---

## Human Review Behavior

When escalating:
1. Add PR comment with:
   - Why it was escalated (which finding triggered, which rule fired)
   - Full agent reports as collapsible sections
   - Certainty-validated findings grouped by severity
   - Specific areas the human should focus on
   - Risk tier, repo risk class, and score breakdown
2. Assign the configured reviewer group as required reviewer
   - Default: `Developers` team/group (configurable per repo in `review.yml`)
   - That's it — no CODEOWNERS lookup, no git blame, no routing logic
   - The team decides internally who picks it up
3. Set PR label: `needs-human-review` with reason tag
