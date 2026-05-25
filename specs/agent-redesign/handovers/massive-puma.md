# Handover: massive-puma (Brief 04 — Architecture Verifier)

## What was built

`ArchitectureAgent` — a standalone architecture verifier that discovers anchors
cheapest-first, scopes them by path for monorepos, and selects one of three
modes: `full_verifier`, `narrow_local_pattern`, or `skipped`.

Key files:

- `src/pr_guardian/agents/architecture_anchors.py` — anchor discovery, mode
  computation, `ArchitectureAnchor` and `ArchitectureAnchorSet` dataclasses.
- `src/pr_guardian/agents/architecture.py` — `ArchitectureAgent` (subclass of
  `BaseAgent`), accepts `adapter=` for platform I/O and `llm_client=` for
  testing.
- `prompts/architecture/base.md` — agent prompt requiring quote-grounded
  findings backed by an anchor document.
- `src/pr_guardian/config/schema.py` — added `ArchitectureConfig` (with
  `mode_override` and `path_scopes`) and `architecture_docs: list[str]` to
  `GuardianConfig`.
- `src/pr_guardian/core/orchestrator.py` — registered `"architecture"` in
  `AGENT_REGISTRY`; extended adapter-passing logic to cover the architecture
  agent (same pattern as intent).

## Anchor data shapes (consumed by Brief 05)

```python
@dataclass
class ArchitectureAnchor:
    path: str                     # repo-relative path of the anchor source
    rank: int                     # 1–11; lower = stronger signal
    weight: float                 # 1.0 for rank1, 0.9 for rank2, …
    anchor_class: Literal["rule", "convention", "structural"]
    content: str                  # extracted text (section or full file)
    scope_glob: str | None        # None = global; "packages/api/**" = subtree
```

```python
@dataclass
class ArchitectureAnchorSet:
    mode: Literal["full_verifier", "narrow_local_pattern", "skip"]
    anchors_by_path: dict[str, list[ArchitectureAnchor]]  # changed_path → anchors
    status_reason: str | None     # present when mode == "skip"
```

### Status-reason strings (for Brief 05 display)

| Condition | `status_reason` |
|-----------|-----------------|
| No architecture anchor found after full discovery | `"no architecture context found"` |
| `mode_override = "skip"` in config | `"mode forced by config: skip"` |
| All changed paths unmatched by scoped anchors | `"no architecture context found for changed paths"` |
| No adapter provided | `"no architecture context found"` |

The constant `_NO_ANCHOR_REASON = "no architecture context found"` in
`architecture_anchors.py` is the canonical string; `AgentResult.status_reason`
uses it verbatim for the common case. Brief 05 should display this string when
rendering the skipped-agent card.

## Mode selection rules (summary)

| Anchors present | Mode |
|-----------------|------|
| `review.yml` `architecture_docs` loaded (rank 1) | `full_verifier` |
| Any rank 1–3 anchor | `full_verifier` |
| Rank 4–5 **and** rank 7+ | `full_verifier` |
| Rank 4–10 alone | `narrow_local_pattern` |
| Rank 11 only or nothing | `skip` |

`mode_override` in config overrides computed mode:
- `"skip"` → short-circuits discovery entirely (no I/O).
- `"full_verifier"` or `"narrow_local_pattern"` → runs discovery to populate
  anchor content, then forces the declared mode.
- `"auto"` (default) → computed from discovered anchors.

## Local-pattern constraints

In `narrow_local_pattern` mode the agent drops any finding where
`severity != low` or `certainty != suspected` after the LLM call. The verdict
is recomputed to `PASS` if all findings are dropped.

## AgentResult contract

```python
# skipped case
AgentResult(
    agent_name="architecture",
    verdict=Verdict.PASS,
    status="skipped",
    status_reason="no architecture context found",
    findings=[],
)
```

Brief 05 must handle `status == "skipped"` — it must **not** treat this as a
pass for scoring, and must render the skipped-agent card with `status_reason`.

## Files this pod owns (do not modify without good reason)

- `src/pr_guardian/agents/architecture_anchors.py`
- `src/pr_guardian/agents/architecture.py`
- `prompts/architecture/base.md`
- `tests/test_architecture_anchors.py`
- `tests/test_architecture_agent.py`

## Config fields added to GuardianConfig

```python
architecture_docs: list[str] = []          # paths to explicit architecture docs (rank 1)
architecture: ArchitectureConfig = ...     # mode_override + path_scopes
```

`ArchitectureConfig.mode_override` defaults to `"auto"`.

## Deviations from brief

None. All constraints and test expectations were implemented as specified.
`architecture_intent` was not present in this codebase branch (already removed
by prior pods), so no migration was needed.
