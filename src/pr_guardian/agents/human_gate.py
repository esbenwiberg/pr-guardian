from __future__ import annotations

import json
import re
from typing import Literal, cast

import structlog

from pr_guardian.agents.prompt_composer import build_agent_prompt, load_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.context import ReviewContext
from pr_guardian.models.findings import GateResult
from pr_guardian.persistence import storage

log = structlog.get_logger()

# Ordered numeric weights for level and threshold comparisons.
_LEVEL_ORDER: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
_THRESHOLD_ORDER: dict[str, int] = {"low": 1, "medium_plus": 2, "high": 3}

_VALID_LEVELS = frozenset(_LEVEL_ORDER)

GATE_OUTPUT_SCHEMA = """
Respond with ONLY raw valid JSON (no markdown fences, no commentary):
{"level": "none | low | medium | high", "reason": "1-2 sentence explanation of what drove this verdict"}
"""


def _compute_gated(level: str, gate_threshold: str) -> bool:
    """Return True when level meets or exceeds the configured gate threshold."""
    return _LEVEL_ORDER.get(level, 3) >= _THRESHOLD_ORDER.get(gate_threshold, 2)


def _build_gate_context(context: ReviewContext) -> str:
    """Build the user-message context for the gate agent.

    Deliberately excludes findings from other agents — the gate agent must
    judge the nature of the change, not the certainty of other bots' findings.
    Includes: diff, changed-file list, archmap classification, change profile.
    """
    parts: list[str] = []

    # PR metadata
    parts.append(f"## PR: {context.pr.title}")
    parts.append(f"- Author: {context.pr.author}")
    parts.append(f"- Branch: {context.pr.source_branch} → {context.pr.target_branch}")
    parts.append(f"- Repo: {context.pr.repo}")
    parts.append(f"- Files changed: {len(context.changed_files)}")
    parts.append(f"- Lines changed: {context.lines_changed}")

    # Changed files list
    parts.append("\n## Changed Files")
    for path in context.changed_files:
        parts.append(f"- {path}")

    # Archmap classification — hub files are highest architectural risk
    if context.archmap.files:
        parts.append("\n## Architecture Classification (Archmap)")
        for path, archmap_file in context.archmap.files.items():
            hub_marker = " [HUB]" if archmap_file.classification == "hub" else ""
            parts.append(
                f"- {path}: {archmap_file.classification}{hub_marker}"
                f" (ca={archmap_file.ca}, risk={archmap_file.risk})"
            )
        hubs = context.archmap.hub_files()
        if hubs:
            hub_paths = ", ".join(h.path for h in hubs)
            parts.append(f"  Hub files (highest fan-in, highest structural risk): {hub_paths}")

    # Change profile — semantic classification of what the PR touches
    profile = context.change_profile
    traits: list[str] = []
    if profile.touches_security_surface:
        traits.append("security surface")
    if profile.touches_api_boundary:
        traits.append("API boundary")
    if profile.touches_data_layer:
        traits.append("data layer")
    if profile.adds_dependencies:
        traits.append("adds dependencies")
    if profile.crosses_architecture_boundary:
        traits.append("crosses architecture boundary")
    if traits:
        parts.append(f"\n## Change Profile\n- Touches: {', '.join(traits)}")

    # Diff — the concrete evidence for the gate verdict
    parts.append("\n## Diff\n")
    for df in context.diff.files:
        if df.patch:
            parts.append(f"### {df.path} ({df.status})\n```\n{df.patch}\n```")
        else:
            parts.append(
                f"### {df.path} ({df.status})\n"
                "*[diff not available — do not speculate about this file]*"
            )

    return "\n".join(parts)


class HumanGateAgent:
    """Semantic human-gate agent.

    Judges the *nature* of a PR change (danger level) rather than producing
    findings. Returns GateResult, not AgentResult. Blind to other agents'
    findings by design — receives only diff + changed files + archmap.

    Fail-closed: any LLM exception returns GateResult(level='high', gated=True).
    """

    agent_name = "human_gate"
    prompt_dir = "human_gate"

    def __init__(self, config: GuardianConfig, llm_client: LLMClient | None = None):
        self.config = config
        self._llm = llm_client

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm_client(self.config)
        return self._llm

    async def review(self, context: ReviewContext) -> GateResult:
        """Judge the danger level of the PR change.

        Builds context from diff + archmap only (no findings), calls the LLM,
        and returns a GateResult. Fails closed on any exception.
        """
        gate_threshold = self.config.escalation_policy.gate_threshold

        # System prompt: DB admin override takes priority over the file prompt,
        # matching how every other agent handles the override path.
        db_override = await storage.get_prompt_override(self.agent_name)
        file_prompt = load_prompt(f"{self.prompt_dir}/system.md")
        system_prompt = build_agent_prompt(
            self.prompt_dir, [], base_override=db_override or file_prompt
        )
        system_prompt += f"\n\n{GATE_OUTPUT_SCHEMA}"

        # User message: preamble from user.md + structured context (no findings)
        user_preamble = load_prompt(f"{self.prompt_dir}/user.md") or ""
        context_text = _build_gate_context(context)
        user_message = f"{user_preamble}\n\n{context_text}".strip()

        model = resolve_model(self.config, self.agent_name)
        llm = self._get_llm()

        try:
            response = await llm.complete(
                system=system_prompt,
                user=user_message,
                model=model,
                max_tokens=256,
                temperature=self.config.llm.temperature,
                response_format="json",
            )
            return self._parse_gate_response(response.content, gate_threshold)
        except Exception as e:
            # Fail-closed: emit a structured warning so operators can diagnose
            # LLM timeouts in production rather than silently getting a pass.
            log.warning(
                "human_gate_agent_failed",
                agent=self.agent_name,
                error=str(e),
            )
            return GateResult(level="high", reason="", gated=True, error=str(e))

    def _parse_gate_response(self, raw: str, gate_threshold: str) -> GateResult:
        extracted = _extract_json(raw)
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            log.warning("human_gate_invalid_json", raw_preview=raw[:200])
            return GateResult(
                level="high",
                reason="",
                gated=True,
                error="Invalid JSON response from LLM",
            )

        raw_level = data.get("level", "high")
        if raw_level not in _VALID_LEVELS:
            log.warning("human_gate_unknown_level", level=raw_level)
            raw_level = "high"

        level = cast(Literal["none", "low", "medium", "high"], raw_level)
        reason: str = data.get("reason", "")
        gated = _compute_gated(level, gate_threshold)
        return GateResult(level=level, reason=reason, gated=gated)


def _extract_json(raw: str) -> str:
    """Extract JSON object from potentially markdown-wrapped LLM response."""
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
