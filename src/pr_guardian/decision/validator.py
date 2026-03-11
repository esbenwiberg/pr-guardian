"""Adversarial validator: challenges agent findings before they reach the developer.

Runs a separate LLM call that sees all findings + the diff and decides for each
finding whether to keep, dismiss, or downgrade. This implements the
generator-critic pattern to reduce false positives.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.context import ReviewContext
from pr_guardian.models.findings import AgentResult, Finding, Severity, Verdict

log = structlog.get_logger()


def _find_prompts_dir() -> Path:
    source_dir = Path(__file__).parent.parent.parent.parent / "prompts"
    if source_dir.is_dir():
        return source_dir
    app_dir = Path("/app/prompts")
    if app_dir.is_dir():
        return app_dir
    return source_dir


def _load_validator_prompt() -> str:
    path = _find_prompts_dir() / "validator" / "base.md"
    if path.exists():
        return path.read_text().strip()
    return "You are a finding validator. For each finding, decide: keep, dismiss, or downgrade."


def _flatten_findings(
    agent_results: list[AgentResult],
) -> list[tuple[str, int, Finding]]:
    """Flatten findings across agents into (agent_name, local_index, finding) tuples."""
    flat: list[tuple[str, int, Finding]] = []
    for result in agent_results:
        for i, finding in enumerate(result.findings):
            flat.append((result.agent_name, i, finding))
    return flat


def _build_findings_text(flat: list[tuple[str, int, Finding]]) -> str:
    """Serialize findings into text for the validator prompt."""
    lines: list[str] = []
    for global_idx, (agent_name, _, finding) in enumerate(flat):
        lines.append(
            f"[{global_idx}] agent={agent_name} "
            f"severity={finding.severity.value} "
            f"certainty={finding.certainty.value} "
            f"category={finding.category}\n"
            f"  file: {finding.file}:{finding.line or '?'}\n"
            f"  description: {finding.description}\n"
            f"  suggestion: {finding.suggestion}"
        )
    return "\n\n".join(lines)


def _build_diff_summary(context: ReviewContext, max_chars: int = 60_000) -> str:
    """Build a compact diff for the validator (it needs context, not full detail)."""
    parts: list[str] = []
    total = 0
    for df in context.diff.files:
        if not df.patch:
            continue
        header = f"### {df.path}\n"
        if total + len(header) + len(df.patch) > max_chars:
            parts.append(f"### {df.path}\n[truncated]")
            break
        parts.append(header + df.patch)
        total += len(header) + len(df.patch)
    return "\n\n".join(parts)


def _build_user_message(
    flat: list[tuple[str, int, Finding]],
    context: ReviewContext,
) -> str:
    """Build the user message: findings list + diff."""
    findings_text = _build_findings_text(flat)
    diff_text = _build_diff_summary(context)
    return (
        f"## Findings to validate ({len(flat)} total)\n\n"
        f"{findings_text}\n\n"
        f"---\n\n"
        f"## PR Diff\n\n{diff_text}"
    )


def _extract_json(raw: str) -> str:
    """Extract JSON from potentially markdown-wrapped response."""
    stripped = raw.strip()
    if stripped.startswith("{"):
        return stripped
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{.*", stripped, re.DOTALL)
    if match:
        return match.group(0).strip()
    return stripped


def _apply_validations(
    agent_results: list[AgentResult],
    flat: list[tuple[str, int, Finding]],
    validations: list[dict],
) -> tuple[list[AgentResult], int, int]:
    """Apply validator decisions to agent results.

    Returns (new_agent_results, dismissed_count, downgraded_count).
    """
    # Build a lookup: global_index -> action
    actions: dict[int, dict] = {}
    for v in validations:
        idx = v.get("index")
        if isinstance(idx, int) and 0 <= idx < len(flat):
            actions[idx] = v

    # Track which (agent_name, local_index) to dismiss or downgrade
    dismiss_set: set[tuple[str, int]] = set()
    downgrade_map: dict[tuple[str, int], str] = {}

    for global_idx, (agent_name, local_idx, _) in enumerate(flat):
        action_data = actions.get(global_idx)
        if not action_data:
            continue
        action = action_data.get("action", "keep")
        if action == "dismiss":
            dismiss_set.add((agent_name, local_idx))
        elif action == "downgrade":
            new_sev = action_data.get("downgraded_severity")
            if new_sev and new_sev in ("low", "medium", "high", "critical"):
                downgrade_map[(agent_name, local_idx)] = new_sev

    dismissed_count = len(dismiss_set)
    downgraded_count = len(downgrade_map)

    # Build new agent results
    new_results: list[AgentResult] = []
    for result in agent_results:
        new_findings: list[Finding] = []
        for local_idx, finding in enumerate(result.findings):
            key = (result.agent_name, local_idx)
            if key in dismiss_set:
                continue
            if key in downgrade_map:
                finding = replace(
                    finding,
                    severity=Severity(downgrade_map[key]),
                )
            new_findings.append(finding)

        new_result = replace(
            result,
            findings=new_findings,
            # Downgrade verdict if all findings dismissed and verdict wasn't FLAG_HUMAN
            verdict=(
                Verdict.PASS
                if not new_findings
                and result.findings
                and result.verdict != Verdict.FLAG_HUMAN
                else result.verdict
            ),
        )
        new_results.append(new_result)

    return new_results, dismissed_count, downgraded_count


async def validate_findings(
    agent_results: list[AgentResult],
    context: ReviewContext,
    config: GuardianConfig,
    llm_client: LLMClient | None = None,
) -> tuple[list[AgentResult], dict]:
    """Run the adversarial validator on agent findings.

    Returns:
        (validated_agent_results, metadata_dict)

    If the validator is disabled, has too few findings to justify a call,
    or fails, the original results are returned unchanged.
    """
    validator_cfg = config.validator
    meta: dict = {"validator_ran": False, "dismissed": 0, "downgraded": 0}

    if not validator_cfg.enabled:
        return agent_results, meta

    flat = _flatten_findings(agent_results)
    if len(flat) < validator_cfg.min_findings_to_validate:
        return agent_results, meta

    system_prompt = _load_validator_prompt()
    user_message = _build_user_message(flat, context)

    # Resolve model — use override if configured, else default
    model = validator_cfg.model_override or resolve_model(config, "validator")
    llm = llm_client or create_llm_client(config)

    try:
        response = await llm.complete(
            system=system_prompt,
            user=user_message,
            model=model,
            max_tokens=config.llm.max_tokens,
            temperature=0.0,  # Deterministic for consistency
            response_format="json",
        )

        extracted = _extract_json(response.content)
        data = json.loads(extracted)
        validations = data.get("validations", [])

        new_results, dismissed, downgraded = _apply_validations(
            agent_results, flat, validations,
        )

        meta.update(
            validator_ran=True,
            dismissed=dismissed,
            downgraded=downgraded,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        log.info(
            "validator_complete",
            total_findings=len(flat),
            dismissed=dismissed,
            downgraded=downgraded,
        )
        return new_results, meta

    except Exception as e:
        log.error("validator_failed", error=str(e))
        meta["error"] = str(e)
        return agent_results, meta
