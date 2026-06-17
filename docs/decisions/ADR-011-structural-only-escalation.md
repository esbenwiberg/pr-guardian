# ADR-011: Structural-only escalation policy and gate_agent sticky-trigger kind

## Status
Accepted — 2026-06-17. Amends ADR-002's closed sticky-trigger kind set.

**Staged rollout — only §1 is implemented in the initial brief.** §2
(`EscalationPolicyConfig`), §3 (gate agent + `GateResult`), and §4 (engine
branch) are follow-on work delivered by parallel briefs in the
`structural-only-escalation` pod series. The Accepted status reflects the full
design decision; individual implementation briefs track which subsystems are
live.

## Context

ADR-002 split escalation reasons into two disjoint buckets — `sticky_triggers`
(structural) and `finding_reasons` (finding-derived) — and froze the
`StickyTriggerKind` set at six values. The rule: "Adding any further kind
requires another ADR amendment, not a silent enum extension."

Today, human review is triggered by two independent signals:

1. **Structural** — the PR touches trust-tier paths, archmap hubs, or other
   repo-level danger markers (the closed kind set).
2. **Finding-derived** — review bots raised `detected ≥ medium`, `suspected ≥ 3`,
   or an agent emitted `FLAG_HUMAN`.

The matrix at HIGH risk tier also refuses auto-approve unless every agent passes
clean. The result: a PR touching only low-risk paths gets parked for a human
purely because the bots surfaced a pile of low-confidence "suspected" findings —
even though those findings are fixable by the author and never needed a second
pair of human eyes. Finding noise, not structural danger, is gating humans.

A new per-Profile setting — `escalation_policy: structural_only` — fixes this:
findings drive comments and REJECT, not human escalation. Human review is
reserved for structural danger only, including a new semantic gate agent that
judges the *nature* of the change (not its findings).

ADR-005 established precedent for extending the kind set and for adding
structured context to `StickyTrigger` (proposed `details: dict[str, Any]`).
That `details` field remains ADR-005's proposed future work and is not added
here; the gate-agent result carries its explanation through the existing `reason`
field on `StickyTrigger`.

## Decision

### 1. Add `gate_agent` to `StickyTriggerKind`

Extend the closed literal in `decision/types.py`:

```python
StickyTriggerKind = Literal[
    "new_dep", "path_risk", "hotspot", "trust_tier",
    "repo_risk", "high_diff", "archmap_hub",
    "gate_agent",   # <- added by this ADR
]
```

`_VALID_TRIGGER_KINDS` in `api/dashboard.py` derives from
`get_args(StickyTriggerKind)`, so the verify endpoint accepts the new kind
without any further edit.

The `StickyTrigger` dataclass itself is **unchanged** — `details` remains
ADR-005's proposed future work.

### 2. New `escalation_policy` per-Profile setting

`EscalationPolicyConfig` (introduced by the config brief, parallel work) adds:

```python
class EscalationPolicyConfig(BaseModel):
    mode: Literal["standard", "structural_only"] = "standard"
    gate_threshold: Literal["low", "medium_plus", "high"] = "medium_plus"
    reject_threshold: Literal["confident_only", "medium_plus", "any"] = "confident_only"
```

`standard` is the default; repos not opting in are completely unaffected.

### 3. New semantic gate agent (structural_only only)

A new LLM agent (`agents/human_gate.py`) judges the *nature* of the change
— it emits a graded danger level (`none | low | medium | high`) and a short
reason. It is deliberately blind to the other agents' findings so that
finding-certainty cannot leak into the structural gate.

```python
@dataclass
class GateResult:
    level: Literal["none", "low", "medium", "high"]
    reason: str
    gated: bool      # True when level >= gate_threshold OR on LLM error (fail-closed)
    error: str | None = None
```

### 4. Decision engine branch for structural_only

`resolve_decision` gains a `gate_result: GateResult | None = None` parameter
(None in standard mode). In `structural_only`:

- Start from `AUTO_APPROVE`.
- Escalate to `HUMAN_REVIEW` iff any structural trigger fires: trust tier
  (`mandatory_human` / `human_primary`), archmap hub, existing stickies, or
  `gate_result.gated`.
- The matrix pass/warn/flag/score logic and finding-derived human escalation
  are **bypassed**.
- `_check_reject` still runs at the configured `reject_threshold`; if it fires,
  the decision becomes `REJECT`.
- Mechanical `HARD_BLOCK` (score ≥ `hard_block_score`) still applies.
- `standard` mode: unchanged — `gate_result` is `None`, existing path runs
  verbatim.

## Consequences

**Easier:** Auto-approve on `structural_only` repos means "safe structural
change," not "quiet bots." Finding noise stops gating humans. The new
`gate_agent` trigger kind renders in `review_detail.html`'s existing structural-
triggers loop without template changes (it loops `sticky_triggers` generically).

**Harder:** Repos that opt in accept that findings alone will never summon a
human — authors bear responsibility for resolving finding comments before
merge. Misconfiguration (too-low `gate_threshold`) could over-escalate; too-high
could under-escalate. Tuning is the admin's responsibility.

**Committed to:** `gate_agent` is a first-class structural trigger kind. The gate
agent must never key on finding-certainty (`suspected`/`detected`). Fail-closed
semantics are mandatory: an LLM error produces `gated=True`, not a silent pass.
Future structural trigger kinds still require an ADR update (this rule carries
forward from ADR-002). The `escalation_policy` config defaults to `standard`,
making the feature fully reversible by config with no DB migration.
