# Handover: isolated-rattlesnake (Brief 03 — Implement Intent Verifier)

## What was built

Implemented the `intent` verifier agent as a pure rule-based check (no LLM call in v1):

- **`src/pr_guardian/agents/intent_anchors.py`** — `IntentAnchorContext` dataclass and
  `load_intent_anchors()` async function. Classifies PR intent anchors:
  - `kind="spec"`: a fetchable `specs/...` markdown file referenced in title/body
  - `kind="title_body"`: ≥80 non-template characters with a non-generic claim
  - `kind="missing"`: empty body, generic keywords only, or fewer than 80 concrete chars
  - Work item APIs (GitHub issues `#NNN`, ADO work items `AB#NNN`) are detected and
    explicitly skipped — only `fetch_file_content` is ever called, and only for `specs/`
    paths.

- **`src/pr_guardian/agents/intent.py`** — `IntentAgent(BaseAgent)`. Overrides `review()`
  completely — no LLM, no prompt, no JSON parsing. Accepts `adapter=` in constructor for
  spec file fetching. Emits one `medium/suspected` scope-opacity finding with `line=None`
  and `quote=SCOPE_OPACITY_QUOTE` when: (a) no useful anchor exists AND (b) the PR
  exceeds the configured size gate (`changed_files >= size_gate_files` OR
  `lines_changed >= size_gate_lines`). Returns `Verdict.PASS` otherwise.

- **`prompts/intent/base.md`** — Created for future LLM use; not loaded by the current
  no-LLM override.

- **`src/pr_guardian/config/schema.py`** — Added `size_gate_files: int = 5` and
  `size_gate_lines: int = 150` to `IntentVerificationConfig` (the existing config model
  left intact; `WeightsConfig` not touched).

- **`src/pr_guardian/triage/classifier.py`** — Post-amplifier intent scheduling:
  `intent` is added to the agent set for MEDIUM/HIGH when `enabled=True`, and discarded
  for all other tiers or when `enabled=False`. The discard is critical because `ALL_AGENTS`
  (used for the HIGH path) already contains `"intent"`.

- **`src/pr_guardian/core/orchestrator.py`** — Added `IntentAgent` import and registry
  entry. When instantiating `intent`, passes `adapter=adapter` so spec files can be fetched.

## Exact constants (required for downstream briefs)

| Name | Location | Value |
|------|----------|-------|
| `SCOPE_OPACITY_CATEGORY` | `src/pr_guardian/agents/base.py` | `"scope-opacity"` |
| `SCOPE_OPACITY_QUOTE` | `src/pr_guardian/agents/intent.py` | `"PR title/body lacks a useful intent anchor"` |
| `IntentAnchorContext` | `src/pr_guardian/agents/intent_anchors.py` | see below |

### `IntentAnchorContext` shape (for architecture/dashboard briefs)

```python
@dataclass
class IntentAnchorContext:
    has_useful_anchor: bool
    anchor_kind: Literal["spec", "title_body", "missing"]
    title: str
    body: str
    referenced_specs: dict[str, str]   # spec_path → fetched content
    missing_reason: str | None = None
```

### `load_intent_anchors()` signature

```python
async def load_intent_anchors(
    title: str,
    body: str | None,
    adapter=None,        # PlatformAdapter | None
    repo: str = "",
    head_sha: str = "",
) -> IntentAnchorContext:
```

### Scope-opacity finding shape emitted by `IntentAgent`

```python
Finding(
    severity=Severity.MEDIUM,
    certainty=Certainty.SUSPECTED,
    category="scope-opacity",          # == SCOPE_OPACITY_CATEGORY
    language="",
    file="",
    line=None,
    description="...",
    quote="PR title/body lacks a useful intent anchor",  # == SCOPE_OPACITY_QUOTE
    suggestion="...",
)
```

## Changed interfaces

### `src/pr_guardian/config/schema.py`

- `IntentVerificationConfig` gains two new optional fields with defaults:
  `size_gate_files: int = 5`, `size_gate_lines: int = 150`.
- `WeightsConfig` is unchanged.

### `src/pr_guardian/triage/classifier.py`

- Post-amplifier block at the end of `classify()` (before the final `return`):
  adds or discards `"intent"` depending on tier and `config.intent_verification.enabled`.
- The TRIVIAL early-return path is unaffected (returns before this block).
- `ALL_AGENTS` frozenset is unchanged (still includes `"intent"`).

### `src/pr_guardian/core/orchestrator.py`

- Added `from pr_guardian.agents.intent import IntentAgent`
- `AGENT_REGISTRY["intent"] = IntentAgent`
- Agent instantiation loop: `if agent_name == "intent": agent = agent_cls(config, adapter=adapter)`.
  Brief 04 (architecture) should extend this conditional when it lands.

## Files owned by this brief — do not modify without good reason

- `src/pr_guardian/agents/intent_anchors.py` — `IntentAnchorContext` shape is the
  contract for architecture brief.
- `src/pr_guardian/agents/intent.py` — `SCOPE_OPACITY_QUOTE` constant; `IntentAgent`
  constructor signature.
- `tests/test_intent_agent.py` — required fact tests.

## Discovered constraints

- `ALL_AGENTS` frozenset includes `"intent"`, so the HIGH path's `agent_set = set(ALL_AGENTS)`
  automatically includes it. The post-amplifier classifier block must explicitly `discard("intent")`
  when `enabled=False` or tier is LOW/TRIVIAL to override this.
- `IntentAgent` accepts `adapter=None` gracefully — when no adapter is passed (e.g. in tests
  or when the orchestrator can't supply one), spec fetching is silently skipped and the check
  falls through to the 80-char heuristic.
- `IntentVerificationConfig.enabled` already existed in the codebase as a legacy toggle; the
  new size_gate_* fields are additive. The old `work_item_source` and
  `require_linked_work_item` fields remain untouched (they're inert in v1 intent logic).
- The `SCOPE_OPACITY_CATEGORY = "scope-opacity"` constant lives in `base.py` (Brief 02
  defined it). The intent agent imports it from there; do not redefine it.

## Deviations from brief

None. All advisory-scope files were modified as specified.
