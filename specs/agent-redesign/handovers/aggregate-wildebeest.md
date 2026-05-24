# Handover: aggregate-wildebeest (Brief 02 — Harden Prompts and Quote Validation)

## What was built

Introduced verifier-grounded finding enforcement so that findings reaching
decision, storage, and UI are always backed by an exact added diff line:

- **`SCOPE_OPACITY_CATEGORY = "scope-opacity"`** — module-level constant in
  `src/pr_guardian/agents/base.py`. This is the exact category string for the
  intent PR-level exception. Briefs 03 and 05 must import and use this constant.
- **`BaseAgent._extract_added_lines(patch: str) -> frozenset[str]`** — parses
  a diff patch and returns the stripped text of all visible `+` added lines
  (skips `+++` file-header lines; strips leading `+` and surrounding whitespace).
- **`BaseAgent._is_valid_finding(finding: Finding, diff_map: dict[str, str]) -> bool`** —
  enforces the quote contract:
  - Scope-opacity exception: `category == "scope-opacity"` and `line is None` →
    valid if `finding.quote` is non-empty (no diff match required).
  - Normal findings: require non-empty `file`, non-None `line`, non-empty
    `quote`, and `quote.strip()` must be in `_extract_added_lines(patch)` for
    that file.
- **`BaseAgent._parse_response(raw, languages, diff_map=None) -> AgentResult`** —
  extended with optional `diff_map`; when provided, filters findings through
  `_is_valid_finding` and logs dropped count. Backward-compatible: callers
  without `diff_map` skip validation.
- **`BaseAgent._parse_finding`** — now extracts `quote=data.get("quote", "")`.
- **`BaseAgent.review()`** — builds `diff_map = {df.path: df.patch for df in
  context.diff.files}` and passes it to `_parse_response`.
- **`AGENT_OUTPUT_SCHEMA`** — updated with a `"quote"` field in the findings
  schema and explicit QUOTE RULES block.
- **`context_builder.build_agent_context()`** — adds `## PR Description` block
  from `context.pr.body` when non-empty; renamed `## PR:` to `## PR Metadata`;
  added diff legend line.
- **Prompts** — `security_privacy`, `performance`, `code_quality_observability`,
  `test_quality`, `hotspot` each have a quote requirement in Output Requirements.
  `validator` has an "About quotes" section and a quote-mismatch dismissal
  criterion.

## Exact constant and helper names (required for downstream briefs)

| Name | Location | Purpose |
|------|----------|---------|
| `SCOPE_OPACITY_CATEGORY` | `src/pr_guardian/agents/base.py` | `"scope-opacity"` — the exact category string for the intent PR-level exception |
| `BaseAgent._extract_added_lines` | `src/pr_guardian/agents/base.py` | Parse a diff patch → `frozenset[str]` of stripped added lines |
| `BaseAgent._is_valid_finding` | `src/pr_guardian/agents/base.py` | Validate a Finding against a diff_map |

## Exact scope-opacity category string

```python
SCOPE_OPACITY_CATEGORY = "scope-opacity"
```

The intent agent (Brief 03) must emit findings with `category=SCOPE_OPACITY_CATEGORY`,
`line=None`, and a non-empty `quote` describing the missing/vague PR anchor
(e.g. `"PR title/body lacks a useful intent anchor"`).

## Changed interfaces

### `src/pr_guardian/agents/base.py`
- `SCOPE_OPACITY_CATEGORY` constant exported at module level.
- `_parse_response` signature extended: `diff_map: dict[str, str] | None = None`.
  Existing callers passing only `(raw, languages)` continue to work unchanged.
- `_parse_finding` now passes `quote=data.get("quote", "")` to `Finding(...)`.
- `review()` always passes `diff_map` to `_parse_response`.
- Three new static methods: `_extract_added_lines`, `_is_valid_finding` (used in
  tests and by future agents directly).

### `src/pr_guardian/agents/context_builder.py`
- `build_agent_context()` now includes `## PR Description` from `context.pr.body`
  (when non-empty) between the metadata header and the security surface section.
- Diff section header changed from `"\n## Diff\n"` to `"\n## Diff"` + a legend
  line. Any test that asserts the exact string `"## Diff\n"` would need updating.

## Files owned by this brief — do not modify without good reason

- `src/pr_guardian/agents/base.py` — `SCOPE_OPACITY_CATEGORY`, `_is_valid_finding`,
  `_extract_added_lines`, `_parse_response` signature.
- `tests/test_agent_quote_validation.py` — contract tests for Brief 02.

## Files downstream briefs need to use

- **Brief 03 (intent agent)**: import `SCOPE_OPACITY_CATEGORY` from
  `pr_guardian.agents.base`; emit scope-opacity findings with
  `category=SCOPE_OPACITY_CATEGORY`, `line=None`, non-empty `quote`.
- **Brief 04 (architecture agent)**: normal agent — findings will be filtered by
  `_is_valid_finding`; each finding must carry a real `quote`.
- **Brief 05 (persistence/display)**: `Finding.quote` is populated by the parser;
  read and render it. `SCOPE_OPACITY_CATEGORY` identifies PR-level findings that
  have a non-diff quote and `line=None`.

## Discovered constraints

- `_is_valid_finding` is a static method on `BaseAgent`. Agents that override
  `_parse_response` must call it themselves if they want quote validation.
- The `diff_map` uses `DiffFile.path` as keys (bare relative path, e.g.
  `src/app.py`). Findings must use the same path format in their `file` field.
- `context.pr.body` is `str | None`. `None` means not yet fetched;
  `""` means the PR has no description. Only `None`/falsy is excluded from the
  context; an empty string is also excluded.
- The intent agent should not set `file=""` and expect `_is_valid_finding` to
  pass for normal findings — only the scope-opacity exception with `line=None`
  can use an empty file.

## Deviations from brief

None. All files in the advisory scope were modified as specified.
