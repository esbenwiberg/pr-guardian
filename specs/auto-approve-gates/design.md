# Design - Auto-approve gates

## Blast radius

Config and consent:

```text
src/pr_guardian/config/schema.py
src/pr_guardian/config/defaults.yml
src/pr_guardian/config/loader.py
src/pr_guardian/core/orchestrator.py
```

Hotspot evaluation and platform history:

```text
src/pr_guardian/triage/hotspots.py
src/pr_guardian/platform/protocol.py
src/pr_guardian/platform/github.py
src/pr_guardian/platform/ado.py
src/pr_guardian/models/context.py
src/pr_guardian/decision/engine.py
```

Final approval gate and trigger contract:

```text
src/pr_guardian/decision/types.py
src/pr_guardian/decision/engine.py
src/pr_guardian/core/orchestrator.py
src/pr_guardian/api/dashboard.py
src/pr_guardian/persistence/storage.py
src/pr_guardian/models/output.py
```

UI:

```text
src/pr_guardian/dashboard/review_detail.html
src/pr_guardian/dashboard/human_wizard.html
tests/browser/auto_approve_trigger_details.spec.mjs
```

Docs and docs proof:

```text
docs/plan/01b-discovery.md
docs/plan/03-triage.md
docs/plan/04-ai-agents.md
docs/plan/08-implementation.md
docs/plan/09-operations.md
docs/plan/pr-guardian-design.md
src/pr_guardian/triage/hotspots.py
tests/test_hotspot_docs.py
```

## Seams

| # | Seam | Contract crossing | Producer | Consumer |
|---|------|-------------------|----------|----------|
| 1 | Platform -> config loader | Target-branch `review.yml` bytes plus provenance | Brief 01 | Briefs 03, 04 |
| 2 | Config schema -> hotspot evaluator | `respect_hotspots`, thresholds, exemptions | Brief 01 | Brief 02 |
| 3 | Platform history -> hotspot evaluator | path commit list with date and message | Brief 02 | Brief 02 evaluator |
| 4 | Hotspot evaluator -> decision/final gate | hotspot result/details or lookup failure | Brief 02 | Brief 03 |
| 5 | Decision/final gate -> storage/API/UI | `StickyTrigger(kind, label, source, reason, details)` | Brief 03 | Brief 04 |
| 6 | Re-review -> platform side effects | candidate auto-approve must pass final gate before `_apply_platform_actions()` | Brief 03 | all review entry points |
| 7 | Implementation -> docs | on-demand hotspot semantics and defaults | Briefs 01-03 | Brief 05 |

Gate ordering:

```text
Gate 1: 01-add-config-consent
Gate 2: 02-add-hotspot-evaluator
Gate 3: 03-add-final-auto-approve-gate
Gate 4: 04-render-trigger-detail-ui and 05-clean-hotspot-docs may run after Gate 3/2 respectively
```

## Contracts

### Auto-approve config shape owned by Brief 01

```python
class HotspotThresholdConfig(BaseModel):
    min_commits_90d: int = 8
    min_fix_ratio: float = 0.3


class HotspotExemptionConfig(BaseModel):
    pattern: str
    reason: str


class AutoApproveConfig(BaseModel):
    enabled: bool = False
    allowed_target_branches: list[str] = Field(default_factory=lambda: ["develop", "feature/*"])
    blocked_target_branches: list[str] = Field(default_factory=lambda: ["release/*"])
    require_all_checks_pass: bool = True
    respect_hotspots: bool = True
    hotspot_threshold: HotspotThresholdConfig = Field(default_factory=HotspotThresholdConfig)
    hotspot_exemptions: list[HotspotExemptionConfig] = Field(default_factory=list)
```

`config/defaults.yml` must also set `auto_approve.enabled: false`.
`repo_risk_class` may remain a model default for non-auto-approve behavior, but
the consent provenance contract below must distinguish explicit target-branch
repo risk from a defaulted value.

### Repo config load result owned by Brief 01

```python
@dataclass(frozen=True)
class RepoConfigLoadResult:
    config: GuardianConfig
    path: str = "review.yml"
    ref: str = ""
    found: bool = False
    valid: bool = True
    error: str | None = None
    explicit_auto_approve_enabled: bool = False
    explicit_repo_risk_class: bool = False

    @property
    def has_auto_approve_consent(self) -> bool:
        return (
            self.valid
            and self.explicit_auto_approve_enabled
            and self.config.auto_approve.enabled
            and self.explicit_repo_risk_class
        )
```

The orchestrator must fetch `review.yml` from `pr.target_branch` via
`PlatformAdapter.fetch_file_content(repo, "review.yml", ref=pr.target_branch)`.
PR-head config content never grants auto-approve consent.

### Platform path-history contract owned by Brief 02

```python
async def fetch_commits_for_path(
    self,
    repo: str,
    path: str,
    *,
    since: str | None = None,
    ref: str = "HEAD",
    per_page: int = 100,
    project: str = "",
) -> list[dict]:
    """Return newest commits for a path, including message and committer date."""
```

Adapters normalize enough shape for the evaluator:

```python
{
    "sha": "abc123",
    "commit": {
        "message": "fix: repair token refresh race",
        "committer": {"date": "2026-05-20T12:00:00Z"},
    },
}
```

Existing call sites such as `core/maintenance.py` must be updated for the new
keyword-only signature.

### Hotspot result owned by Brief 02

```python
@dataclass(frozen=True)
class HotspotFileResult:
    path: str
    is_hotspot: bool
    commit_count_90d: int
    fix_count_90d: int
    fix_ratio: float
    min_commits_90d: int
    min_fix_ratio: float
    cache_status: Literal["miss", "hit"]
    computed_at: str
    reason: str


@dataclass(frozen=True)
class HotspotEvaluation:
    files: list[HotspotFileResult]
    failures: list[dict[str, str]]
```

`respect_hotspots: true` means either a hotspot hit or a lookup failure blocks
candidate auto-approval. Exemptions are evaluated before platform history calls
and require a non-empty reason in config.

### StickyTrigger contract owned by Brief 03

```python
StickyTriggerKind = Literal[
    "new_dep",
    "path_risk",
    "hotspot",
    "trust_tier",
    "repo_risk",
    "high_diff",
    "config_policy",
]

@dataclass(frozen=True)
class StickyTrigger:
    kind: StickyTriggerKind
    label: str
    source: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
```

Hotspot details use stable keys:

```python
{
    "file_path": "src/auth/session.py",
    "window_days": 90,
    "commit_count": 12,
    "fix_count": 5,
    "fix_ratio": 0.42,
    "min_commits_90d": 8,
    "min_fix_ratio": 0.3,
    "cache_status": "hit",
    "computed_at": "2026-05-24T13:42:00Z",
    "reason": "Recent path history crosses hotspot thresholds",
}
```

Config policy details use stable keys:

```python
{
    "policy": "missing_explicit_consent",
    "path": "review.yml",
    "ref": "main",
    "found": false,
    "valid": true,
    "explicit_auto_approve_enabled": false,
    "explicit_repo_risk_class": false,
    "message": "Target branch review.yml did not explicitly opt in to auto-approve",
}
```

For root config edits, use:

```python
{
    "policy": "root_config_changed",
    "path": "review.yml",
    "current_path_only": true,
    "message": "PR changes root review.yml, so auto-approve is blocked",
}
```

### Final gate invariant owned by Brief 03

The implementation may choose the exact function names, but the invariant is
fixed:

```python
def apply_final_auto_approve_gate(
    result: ReviewResult,
    *,
    context: ReviewContext,
    config_load: RepoConfigLoadResult,
    hotspot_evaluation: HotspotEvaluation | None,
) -> ReviewResult:
    """Downgrade candidate AUTO_APPROVE to HUMAN_REVIEW and add sticky triggers."""
```

Every code path that can lead to `adapter.approve_pr(pr)` for automated
approval must pass through this gate first. Current re-review branches that
auto-approve when all findings are dismissed/resolved become candidate
auto-approvals and then call the shared gate. Manual `submit_verdict` approval
is outside this invariant because it is a human action.

The root config edit blocker checks only the current diff path:

```python
any(file.path == "review.yml" for file in diff.files)
```

Do not inspect `old_path` for rename-away/delete cases in v1.

## UX flows

### Flow A - review detail structural trigger details

Entrypoint: `/reviews/{id}`. Loading, empty, and error states remain the
existing page states. When sticky triggers exist, the current Structural
Triggers panel renders each trigger label, reason, and any known details.

Approved wireframe:

```text
+--------------------------------------------------+
| Structural Triggers                              |
|                                                  |
| Hotspot file touched: src/auth/session.py        |
| 12 commits / 90d - 42% fix commits              |
| threshold 8 / 30%                                |
| cache hit - computed 2026-05-24 13:42            |
+--------------------------------------------------+
```

Exit: reviewer opens the wizard, manually reviews, or leaves the detail page.
No banner is added.

### Flow B - wizard trigger card details

Entrypoint: `/reviews/{id}?mode=wizard` or trigger-focus mode. Existing wizard
loading/error behavior remains. Trigger cards display the compact detail rows
for hotspot/config-policy triggers, then keep the existing action model.

Approved wireframe:

```text
+--------------------------------------------------+
| Hotspot file touched                             |
| src/auth/session.py                              |
| 90d: 12 commits - 42% fix commits               |
| threshold: 8 commits / 30% fixes                 |
| reason: high recent fix density                  |
|                                                  |
| [Show code] [Acknowledge & Approve] [Needs Fix]  |
+--------------------------------------------------+
```

Exit: Acknowledge & Approve posts the existing verify endpoint; Needs Fix
keeps the existing wizard behavior.

### Flow C - config-policy details

Entrypoints are the same as A and B. `config_policy` uses the same structural
trigger rendering and shows whether the blocker was missing consent, invalid
config, incomplete explicit fields, or current-path `review.yml` edit. No global
warning treatment is added.

## Reference reading

- `plans/auto-approve-gates.md` - original problem framing and gate list.
- `src/pr_guardian/config/schema.py:63` - `AutoApproveConfig.enabled` currently
  defaults true.
- `src/pr_guardian/config/defaults.yml:46` - service defaults currently enable
  auto-approve.
- `src/pr_guardian/config/loader.py:23` - current loader reads local
  `repo_path / "review.yml"` and loses provenance.
- `src/pr_guardian/core/orchestrator.py:213` - review pipeline uses a temporary
  path and then `load_repo_config(repo_path)`.
- `src/pr_guardian/core/orchestrator.py:736` - dismissed-findings re-review
  direct auto-approve path.
- `src/pr_guardian/core/orchestrator.py:892` - resolved-findings re-review
  direct auto-approve path.
- `src/pr_guardian/core/orchestrator.py:1038` - platform side effects call
  `approve_pr()` when the decision is auto-approve.
- `src/pr_guardian/triage/hotspots.py:4` - hotspot loader is a nightly/DB stub.
- `src/pr_guardian/platform/protocol.py:74` - existing fetch-file API supports
  target-branch `review.yml` reads.
- `src/pr_guardian/platform/protocol.py:108` - path-history API lacks
  since/ref/message guarantees.
- `src/pr_guardian/decision/types.py:6` - closed sticky trigger kind set.
- `src/pr_guardian/decision/engine.py:181` - current decision matrix and
  structural trigger handling.
- `src/pr_guardian/dashboard/review_detail.html:274` - structural trigger UI
  currently renders label/reason only.
- `src/pr_guardian/dashboard/human_wizard.html:836` - wizard trigger cards and
  trigger icon map.
- `docs/decisions/ADR-002-sticky-trigger-split.md` - closed trigger kind set
  being amended by ADR-005.
- `docs/decisions/ADR-003-finding-lifecycle-state-machine.md` - re-review and
  verification lifecycle background.
- `docs/decisions/ADR-004-fix-by-inference.md` - synthetic trigger verification
  signature background.

## Decisions

- ADR-002: Sticky-trigger semantic split (existing, amended by ADR-005).
- ADR-003: Finding lifecycle state machine (existing).
- ADR-004: Fix-by-inference and synthetic trigger verification (existing).
- ADR-005: Final auto-approval gate and config policy trigger (introduced).
