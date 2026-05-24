# Design - Agent redesign

## Blast radius

Agent contracts and parsing:

- `src/pr_guardian/models/findings.py` - add `Finding.quote`,
  `AgentResult.status`, and `AgentResult.status_reason`.
- `src/pr_guardian/agents/base.py` - require quote in the output schema and
  discard ungrounded normal findings.
- `src/pr_guardian/agents/context_builder.py` - include PR body metadata and
  make context blocks easier for agents to distinguish.
- `prompts/security_privacy/base.md` - reinforce verifier-grounded output.
- `prompts/performance/base.md` - reinforce verifier-grounded output.
- `prompts/code_quality_observability/base.md` - reinforce verifier-grounded
  output.
- `prompts/test_quality/base.md` - reinforce verifier-grounded output.
- `prompts/hotspot/base.md` - reinforce verifier-grounded output.
- `prompts/validator/base.md` - preserve and validate quote-grounding.

Agent taxonomy and orchestration:

- `src/pr_guardian/agents/intent.py` - new intent verifier.
- `src/pr_guardian/agents/intent_anchors.py` - new PR/spec anchor helper.
- `src/pr_guardian/agents/architecture.py` - new architecture verifier.
- `src/pr_guardian/agents/architecture_anchors.py` - new architecture anchor
  discovery helper.
- `prompts/intent/base.md` - new intent prompt.
- `prompts/architecture/base.md` - new architecture prompt.
- `src/pr_guardian/core/orchestrator.py` - replace old registry entry, hydrate
  anchor contexts, and preserve skipped agent results.
- `src/pr_guardian/triage/classifier.py` - schedule `intent` for medium/high
  only; schedule `architecture` where the old architecture side was implied and
  on high/all-agent reviews.
- `src/pr_guardian/discovery/change_profile.py` - replace implied
  `architecture_intent` with split agent names.
- `src/pr_guardian/config/schema.py` - split weights, add architecture anchor
  config, and keep intent v1 thresholds configurable.
- `src/pr_guardian/decision/engine.py` - skip skipped agents during scoring and
  avoid treating skipped as pass.
- `src/pr_guardian/decision/actions.py` - update display labels for PR summary.

Persistence and UI:

- `src/pr_guardian/persistence/models.py` - add finding quote and agent status
  fields.
- `src/pr_guardian/persistence/storage.py` - save and load new fields.
- `src/pr_guardian/api/dashboard.py` - expose new fields and keep triage
  enrichment intact.
- `src/pr_guardian/dashboard/review_detail.html` - render quote strips and
  skipped architecture status.
- `src/pr_guardian/dashboard/human_review.html` - render quote strips in
  review callouts.
- `src/pr_guardian/dashboard/human_wizard.html` - render quote strips in wizard
  concerns.
- `tests/test_agent_contracts.py` - new contract tests for split names/status.
- `tests/test_agent_quote_validation.py` - new parser/quote tests.
- `tests/test_intent_agent.py` - new intent-anchor tests.
- `tests/test_architecture_anchors.py` - new architecture discovery tests.
- `tests/test_architecture_agent.py` - new architecture behavior tests.
- `tests/test_storage_agent_contracts.py` - new storage roundtrip tests.
- `tests/test_dashboard_quote_status.py` - new API/UI-data tests.
- `tests/browser/test_review_detail_quote_status.py` - new visible UI browser
  test.

No planned changes:

- `src/pr_guardian/platform/github.py` and `src/pr_guardian/platform/ado.py`
  already expose file-content read methods; use them but do not add issue/work
  item fetching.
- `alembic/` is not touched for this feature because the app is not live and
  there is no backwards-compatibility requirement.

## Seams

| # | Seam | Contract crossing | Producer | Consumer |
|---|------|-------------------|----------|----------|
| 1 | Shared model -> all review stages | `Finding.quote`, `AgentResult.status`, `AgentResult.status_reason` | Brief 01 | Briefs 02, 03, 04, 05 |
| 2 | Parser -> decision/storage | quote-validated findings only | Brief 02 | Briefs 03, 04, 05 |
| 3 | Triage -> orchestrator | split agent set: `intent`, `architecture` | Brief 01 | Briefs 03, 04 |
| 4 | Intent anchor loader -> intent agent | `IntentAnchorContext` from PR metadata and referenced spec files | Brief 03 | Brief 03 |
| 5 | Architecture discovery -> architecture agent | `ArchitectureAnchorSet` with mode and scoped anchors | Brief 04 | Brief 04 |
| 6 | Storage/API -> dashboard | quote and skipped-status payload fields | Brief 05 | Brief 05 |

Run order:

```text
Gate 1: 01-add-review-contracts
Gate 2: 02-harden-prompts-and-quotes
Gate 3: 03-implement-intent-verifier
Gate 4: 04-implement-architecture-verifier
Gate 5: 05-persist-display-quote-status
```

## Contracts

### Finding quote

Owner: Brief 01 defines the model field; Brief 02 enforces it.

```python
@dataclass
class Finding:
    severity: Severity
    certainty: Certainty
    category: str
    language: str
    file: str
    line: int | None
    quote: str
    description: str
    suggestion: str = ""
```

Validation rules:

- Normal findings require `file`, `line`, and `quote`.
- The quote must match an exact visible added line in that file's diff after
  stripping the leading `+` and surrounding whitespace.
- Missing quote, mismatched quote, or a quote from context/deleted lines drops
  the finding before decision/storage.
- The sole v1 exception is the `intent` PR-level scope-opacity category, which
  uses `line: null` and a quote such as `PR title/body lacks a useful intent
  anchor`.

### Agent status

Owner: Brief 01 defines the model field; Brief 04 produces skipped status;
Brief 05 persists and displays it.

```python
AgentStatus = Literal["ran", "skipped"]

@dataclass
class AgentResult:
    agent_name: str
    verdict: Verdict
    status: AgentStatus = "ran"
    status_reason: str | None = None
    findings: list[Finding] = field(default_factory=list)
```

Decision rules:

- `status="ran"` keeps existing scoring and verdict behavior.
- `status="skipped"` contributes no score and is not counted as a pass.
- Skipped results are still persisted and displayed so the review trail is
  honest.

### Intent anchor context

Owner: Brief 03.

```python
@dataclass
class IntentAnchorContext:
    has_useful_anchor: bool
    anchor_kind: Literal["spec", "title_body", "missing"]
    title: str
    body: str
    referenced_specs: dict[str, str]
    missing_reason: str | None = None
```

V1 useful anchor heuristic:

- Useful if the PR references a fetchable `specs/...` markdown file.
- Useful if title/body contains at least 80 non-template characters with a
  concrete behavior/scope claim.
- Missing if the body is empty/template-only or generic text such as
  `misc`, `update`, `refactor`, or `fixes`.
- GitHub issues and ADO work items are not fetched in v1.

Missing-anchor behavior:

- Low risk: `intent` is not scheduled.
- Medium/high risk: emit medium/suspected PR-level scope-opacity finding when
  no useful anchor exists.

### Architecture anchor set

Owner: Brief 04.

```python
@dataclass
class ArchitectureAnchor:
    path: str
    rank: int
    weight: float
    anchor_class: Literal["rule", "convention", "structural"]
    content: str
    scope_glob: str | None = None

@dataclass
class ArchitectureAnchorSet:
    mode: Literal["full_verifier", "narrow_local_pattern", "skip"]
    anchors_by_path: dict[str, list[ArchitectureAnchor]]
    status_reason: str | None = None
```

Mode rules:

- `review.yml` `architecture_docs` wins when present.
- Any rank 1-3 signal, or rank 4-5 corroborated by rank 7+, enables full
  verifier mode for the scoped path.
- Rank 7-10 signals without stronger anchors enable narrow local-pattern mode.
- Sibling-only or no signal skips: `status="skipped"`,
  `status_reason="no architecture context found"`.
- Narrow local-pattern mode emits only low/suspected quote-grounded findings
  and must not make global architecture claims.

### Persistence/API payload

Owner: Brief 05.

`/api/dashboard/reviews/{id}` returns:

```json
{
  "agent_results": [
    {
      "agent_name": "architecture",
      "status": "skipped",
      "status_reason": "no architecture context found",
      "verdict": "pass",
      "findings": []
    },
    {
      "agent_name": "intent",
      "status": "ran",
      "verdict": "warn",
      "findings": [
        {
          "file": "",
          "line": null,
          "quote": "PR title/body lacks a useful intent anchor",
          "severity": "medium",
          "certainty": "suspected"
        }
      ]
    }
  ]
}
```

## UX flows

### Review Detail

Entrypoint: `/reviews/{review_id}` after a completed review.

States:

- Loading: existing page loading behavior remains.
- Empty: agents with no findings still render as cards; skipped agents show the
  skipped state.
- Error: existing review loading error remains.
- Exit: user opens human review, re-runs review, dismisses findings, or returns
  to queue.

Approved wireframe:

```text
Agent: Architecture
Status: skipped
Reason: no architecture context found - agent skipped

Finding card
[severity] [certainty] [category]
src/file.py:42
Diff quote
+ return user.is_admin or allow_all
Description
Suggestion
```

### Human Review

Entrypoint: `/reviews/{review_id}/human-review`.

States:

- Loading: existing diff/review fetch remains.
- Diff line with finding: quote strip appears in the finding callout.
- File-level/PR-level finding: callout appears in the unmatched area with its
  quote strip.
- Exit: reviewer dismisses/accepts findings or opens code snippets.

Approved wireframe:

```text
[severity] [certainty] [category] src/file.py:42
Diff quote
+ return user.is_admin or allow_all
Description / suggestion
[Dismiss]
```

### Human Wizard

Entrypoint: wizard capability flow.

States:

- Briefing: unchanged.
- Concern: quote strip appears with the concern details.
- Source hunk: existing source disclosure remains.
- Exit: final verdict submission remains.

Approved wireframe:

```text
[severity] [certainty] [category] src/file.py:42
Diff quote
+ return user.is_admin or allow_all
Description / suggestion
[Dismiss]
```

### Inline PR Comments

Inline PR comments remain compact and do not include quote strips.

Approved wireframe:

```text
[MEDIUM] Category
Description

> Suggestion
```

## Reference reading

- `plans/agent-redesign.md` - feature source and user-facing behavior.
- `plans/architecture-anchor-discovery.md` - architecture discovery taxonomy,
  ranks, and mode rules.
- `plans/prompt-engineering-patterns.md` - prompt-hardening source material.
- `AGENTS.md` - repo commands and operational notes.
- `src/pr_guardian/models/findings.py` - shared agent/finding dataclasses.
- `src/pr_guardian/agents/base.py` - shared review parser and output schema.
- `src/pr_guardian/agents/context_builder.py` - agent context construction.
- `src/pr_guardian/core/orchestrator.py` - registry, agent execution, decision
  pipeline, storage handoff.
- `src/pr_guardian/triage/classifier.py` - risk tier and agent selection.
- `src/pr_guardian/discovery/change_profile.py` - implied agents.
- `src/pr_guardian/decision/engine.py` - scoring and decision matrix.
- `src/pr_guardian/decision/severity_filter.py` - post-decision display
  filtering.
- `src/pr_guardian/decision/finding_triage.py` - human-review decision/fyi/noise
  tagging.
- `src/pr_guardian/persistence/models.py` - SQLAlchemy rows.
- `src/pr_guardian/persistence/storage.py` - review persistence and serializers.
- `src/pr_guardian/api/dashboard.py` - dashboard API payloads and diff endpoint.
- `src/pr_guardian/dashboard/review_detail.html` - review detail renderer.
- `src/pr_guardian/dashboard/human_review.html` - human review renderer.
- `src/pr_guardian/dashboard/human_wizard.html` - wizard renderer.
- `src/pr_guardian/platform/github.py` - existing PR body/spec file read
  capabilities.
- `src/pr_guardian/platform/ado.py` - existing PR body/spec file read
  capabilities.
- `docs/decisions/ADR-001-inline-comment-mode-tristate.md` - inline comment mode
  precedent.
- `docs/decisions/ADR-002-sticky-trigger-split.md` - clean split precedent for
  result contracts.
- `docs/decisions/ADR-003-finding-lifecycle-state-machine.md` - finding UI
  precedent.
- `docs/decisions/ADR-004-fix-by-inference.md` - clean-break persistence
  precedent.

## Decisions

- No ADR introduced.
- The old `architecture_intent` key is removed directly. No migration or
  compatibility bridge is required because the app is not live.
- `intent` runs for medium/high PRs only in v1.
- Work items/issues are not v1 intent anchors.
- Scope opacity is `medium`/`suspected` and is a PR-level finding with
  `line: null`.
- `architecture` local-pattern mode emits low/suspected findings.
- Skipped architecture review uses explicit status, not a pass-like hidden
  explanation.
