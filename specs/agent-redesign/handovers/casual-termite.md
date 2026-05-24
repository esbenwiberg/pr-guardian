# Handover: casual-termite (Brief 01 — Add Review Contracts)

## What was built

Introduced the shared contracts that the remainder of the agent redesign depends on:

- **`Finding.quote: str = ""`** — first-class field on `Finding`. Normal findings will use this to cite an exact added diff line; the intent scope-opacity finding uses it to describe the missing anchor.
- **`AgentResult.status: AgentStatus = "ran"`** — `Literal["ran", "skipped"]` type alias. Skipped agents contribute no score and are excluded from verdict checks, but remain in `ReviewResult.agent_results` for persistence and display.
- **`AgentResult.status_reason: str | None = None`** — human-readable explanation when status is `"skipped"` (e.g., `"no architecture context found"`).
- Split `architecture_intent` into separate `intent` (weight 1.0) and `architecture` (weight 1.0) agents across the config, scoring engine, triage, classifier, and display labels.

## Exact field names (required for downstream briefs)

| Field | Location | Type | Default |
|-------|----------|------|---------|
| `Finding.quote` | `src/pr_guardian/models/findings.py` | `str` | `""` |
| `AgentResult.status` | `src/pr_guardian/models/findings.py` | `AgentStatus` (`Literal["ran", "skipped"]`) | `"ran"` |
| `AgentResult.status_reason` | `src/pr_guardian/models/findings.py` | `str | None` | `None` |

## Changed interfaces

### `src/pr_guardian/models/findings.py`
- `AgentStatus = Literal["ran", "skipped"]` type alias added at module level.
- `Finding.quote: str = ""` inserted after `line: int | None` and before `description`.
- `AgentResult.status: AgentStatus = "ran"` and `AgentResult.status_reason: str | None = None` inserted after `verdict` and before `languages_reviewed`.

### `src/pr_guardian/config/schema.py`
- `WeightsConfig.architecture_intent: float = 2.0` → removed. Replaced with `intent: float = 1.0` and `architecture: float = 1.0`.
- `IntentVerificationConfig` (the old intent-as-a-config toggle) remains untouched — that's a separate toggle, not a weight.

### `src/pr_guardian/decision/engine.py`
- `DEFAULT_AGENT_WEIGHTS` dict updated: `architecture_intent` removed, `intent` and `architecture` added at 1.0 each.
- `combined_score()` now skips agents with `status == "skipped"` before computing weighted average.
- `_apply_matrix()` now computes `ran_results = [r for r in agent_results if r.status != "skipped"]` and uses that for `has_flags`, `has_warns`, `all_pass`, and the HIGH-tier guard. The original `agent_results` list (including skipped) is NOT modified — the guard `ran_results and all_pass` replaces `agent_results and all_pass`.
- `check_overrides()` and `_check_reject()` also skip agents with `status == "skipped"`.

### `src/pr_guardian/decision/actions.py`
- `_AGENT_LABELS` now has `"intent": "Intent"` and `"architecture": "Architecture"` instead of `"architecture_intent": "Architecture & Intent"`.

### `src/pr_guardian/triage/classifier.py`
- `ALL_AGENTS` frozenset: `"architecture_intent"` replaced by `"intent"` and `"architecture"`.

### `src/pr_guardian/discovery/change_profile.py`
- `implied_agents` logic: `crosses_architecture_boundary → "architecture"` (was `"architecture_intent"`).

## Files owned by this brief — do not modify without good reason

- `src/pr_guardian/models/findings.py` — the field names and types are the cross-brief contract
- `src/pr_guardian/config/schema.py` `WeightsConfig` — split weights are the scoring anchor

## Files downstream briefs need to extend

- Brief 02 (prompts/quotes): reads `Finding.quote` and enforces non-empty quote for normal findings. Do not change the field name or default.
- Brief 03 (intent agent): creates `src/pr_guardian/agents/intent.py`. Uses `agent_name="intent"`, `AgentResult.status`, and emits the scope-opacity finding with `quote="PR title/body lacks a useful intent anchor"`, `line=None`.
- Brief 04 (architecture agent): creates `src/pr_guardian/agents/architecture.py`. Sets `status="skipped"` with `status_reason="no architecture context found"` when no architecture anchors are found.
- Brief 05 (persistence/display): reads `Finding.quote`, `AgentResult.status`, `AgentResult.status_reason` from storage and renders them.

## Discovered constraints

- `IntentVerificationConfig` in `schema.py` (field `intent_verification`) is a legacy config toggle unrelated to the new `intent` agent weight. Leave it in place; it does not conflict.
- The `DEFAULT_AGENT_WEIGHTS` dict in `engine.py` is a local convenience mirror of `WeightsConfig`. Both were updated. Any future weight changes must update both.
- pytest `-k split_agents` requires the class name to have an underscore: `class Test_split_agents`. The camelCase `TestSplitAgents` class name won't match because pytest node ID substring matching is case-insensitive but preserves underscores.

## Deviations from brief

None. All files in the advisory scope were modified as specified.
