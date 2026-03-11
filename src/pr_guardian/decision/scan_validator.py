"""Adversarial validator for scan findings.

Mirrors the PR review validator (validator.py) but operates on ScanAgentResult /
ScanFinding models and uses the scan-specific validator prompt.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import structlog

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.findings import Severity, Verdict
from pr_guardian.models.scan import ScanAgentResult, ScanContext, ScanFinding

log = structlog.get_logger()


def _find_prompts_dir() -> Path:
    source_dir = Path(__file__).parent.parent.parent.parent / "prompts"
    if source_dir.is_dir():
        return source_dir
    app_dir = Path("/app/prompts")
    if app_dir.is_dir():
        return app_dir
    return source_dir


def _load_scan_validator_prompt() -> str:
    path = _find_prompts_dir() / "scan_validator" / "base.md"
    if path.exists():
        return path.read_text().strip()
    return "You are a scan finding validator. For each finding, decide: keep, dismiss, or downgrade."


def _flatten_findings(
    agent_results: list[ScanAgentResult],
) -> list[tuple[str, int, ScanFinding]]:
    flat: list[tuple[str, int, ScanFinding]] = []
    for result in agent_results:
        for i, finding in enumerate(result.findings):
            flat.append((result.agent_name, i, finding))
    return flat


def _build_findings_text(flat: list[tuple[str, int, ScanFinding]]) -> str:
    lines: list[str] = []
    for global_idx, (agent_name, _, finding) in enumerate(flat):
        lines.append(
            f"[{global_idx}] agent={agent_name} "
            f"severity={finding.severity.value} "
            f"certainty={finding.certainty.value} "
            f"category={finding.category}\n"
            f"  file: {finding.file or 'repository-wide'}\n"
            f"  description: {finding.description}\n"
            f"  suggestion: {finding.suggestion}"
        )
    return "\n\n".join(lines)


def _build_scan_context_text(context: ScanContext, max_chars: int = 30_000) -> str:
    """Build a compact scan context summary for the validator."""
    parts: list[str] = []
    parts.append(f"Scan type: {context.scan_type.value}")
    parts.append(f"Repository: {context.repo}")

    if context.scan_type.value == "recent_changes":
        parts.append(f"Time window: {context.time_window_days} days")
        parts.append(f"Merged PRs: {len(context.merged_prs)}")
        if context.change_summary:
            parts.append(f"\n{context.change_summary}")
        if context.merged_prs:
            parts.append("\nMerged PRs:")
            for pr in context.merged_prs[:30]:
                title = pr.get("title", "untitled")
                number = pr.get("number", "?")
                parts.append(f"- #{number}: {title}")
    else:
        parts.append(f"Staleness threshold: {context.staleness_months} months")
        parts.append(f"Stale files: {len(context.stale_files)}")
        if context.stale_files:
            parts.append("\nStale files:")
            for sf in context.stale_files[:30]:
                parts.append(f"- {sf.get('path', '?')} (last modified: {sf.get('last_modified', '?')})")

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


def _build_user_message(
    flat: list[tuple[str, int, ScanFinding]],
    context: ScanContext,
) -> str:
    findings_text = _build_findings_text(flat)
    context_text = _build_scan_context_text(context)
    return (
        f"## Findings to validate ({len(flat)} total)\n\n"
        f"{findings_text}\n\n"
        f"---\n\n"
        f"## Scan Context\n\n{context_text}"
    )


def _extract_json(raw: str) -> str:
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
    agent_results: list[ScanAgentResult],
    flat: list[tuple[str, int, ScanFinding]],
    validations: list[dict],
) -> tuple[list[ScanAgentResult], int, int]:
    actions: dict[int, dict] = {}
    for v in validations:
        idx = v.get("index")
        if isinstance(idx, int) and 0 <= idx < len(flat):
            actions[idx] = v

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

    new_results: list[ScanAgentResult] = []
    for result in agent_results:
        new_findings: list[ScanFinding] = []
        for local_idx, finding in enumerate(result.findings):
            key = (result.agent_name, local_idx)
            if key in dismiss_set:
                continue
            if key in downgrade_map:
                finding = replace(finding, severity=Severity(downgrade_map[key]))
            new_findings.append(finding)

        new_result = replace(
            result,
            findings=new_findings,
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


async def validate_scan_findings(
    agent_results: list[ScanAgentResult],
    context: ScanContext,
    config: GuardianConfig,
    llm_client: LLMClient | None = None,
) -> tuple[list[ScanAgentResult], dict]:
    """Run the adversarial validator on scan findings.

    Returns:
        (validated_agent_results, metadata_dict)
    """
    validator_cfg = config.validator
    meta: dict = {"validator_ran": False, "dismissed": 0, "downgraded": 0}

    if not validator_cfg.scan_enabled:
        return agent_results, meta

    flat = _flatten_findings(agent_results)
    if len(flat) < validator_cfg.min_findings_to_validate:
        return agent_results, meta

    system_prompt = _load_scan_validator_prompt()
    user_message = _build_user_message(flat, context)

    model = validator_cfg.model_override or resolve_model(config, "scan_validator")
    llm = llm_client or create_llm_client(config)

    try:
        response = await llm.complete(
            system=system_prompt,
            user=user_message,
            model=model,
            max_tokens=config.llm.max_tokens,
            temperature=0.0,
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
            "scan_validator_complete",
            total_findings=len(flat),
            dismissed=dismissed,
            downgraded=downgraded,
        )
        return new_results, meta

    except Exception as e:
        log.error("scan_validator_failed", error=str(e))
        meta["error"] = str(e)
        return agent_results, meta
