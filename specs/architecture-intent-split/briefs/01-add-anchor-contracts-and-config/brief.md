# Brief 01 - Add anchor contracts and config

## Task
Add the shared contracts for split-agent anchor state. The implementation adds top-level `architecture_docs`, an `architecture` config block, split weights for `architecture` and `intent`, and model types for architecture/intent anchors carried on `ReviewContext`.

## Touches
- `src/pr_guardian/config/schema.py`
- `src/pr_guardian/config/defaults.yml`
- `src/pr_guardian/config/loader.py`
- `src/pr_guardian/models/anchors.py`
- `src/pr_guardian/models/context.py`
- `tests/test_agent_split_config.py`
- `README.md`

## Does Not Touch
- `src/pr_guardian/core/orchestrator.py`
- `src/pr_guardian/agents/context_builder.py`
- `src/pr_guardian/dashboard/review_detail.html`
- `alembic/versions/`

## Constraints
- `weights.architecture_intent` remains a deprecated input alias. If `architecture` or `intent` is explicitly set, the new key wins.
- No DB migration in this brief.
- `ArchitectureMode` is exactly `auto | full_verifier | narrow_local_pattern | skip`.
- Anchor model fields must be plain dataclass or Pydantic-serializable primitives.
- README must show the split agents and the new config keys while noting the legacy alias.

## Test Expectations
- Add a focused config test for split weights and legacy alias behavior.
- Add a focused context/model test proving `ReviewContext` accepts architecture and intent anchor sets.
- Do not add integration tests here; discovery and orchestration are later briefs.

## Wrap-up
Record any config compatibility choices in the handover. Do not implement discovery, agent prompts, or UI rendering in this brief.
