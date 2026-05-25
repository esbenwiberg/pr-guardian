"""Standalone architecture verifier agent.

Discovers anchors cheapest-first, then runs in one of three modes:

    full_verifier        — rank 1-3 anchors present; flag rule deviations.
    narrow_local_pattern — rank 7-10 only; low/suspected pattern findings.
    skip                 — no anchors found; AgentResult.status = "skipped".
"""
from __future__ import annotations

import structlog

from pr_guardian.agents.architecture_anchors import (
    ArchitectureAnchorSet,
    discover_architecture_anchors,
)
from pr_guardian.agents.base import AGENT_OUTPUT_SCHEMA, BaseAgent
from pr_guardian.agents.context_builder import build_agent_context
from pr_guardian.agents.prompt_composer import build_agent_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.context import ReviewContext
from pr_guardian.models.findings import AgentResult, Certainty, Severity, Verdict
from pr_guardian.persistence import storage

log = structlog.get_logger()

_NO_ANCHOR_REASON = "no architecture context found"

_NARROW_MODE_ADDENDUM = """
## NARROW LOCAL-PATTERN MODE
You have only soft conventions (no authoritative ADRs or rule documents).
- Emit ONLY low severity and suspected certainty findings.
- Compare the changed file against nearby sibling files and the conventions provided.
- Do NOT make global architecture claims (e.g. "you are violating Clean Architecture").
- Frame findings as pattern deviations: "this file does X but sibling files do Y".
"""

_FULL_VERIFIER_ADDENDUM = """
## FULL VERIFIER MODE
You have authoritative architecture documents. Flag clear deviations from stated rules.
Cite specific lines or sections from the provided architecture anchors in your findings.
"""


class ArchitectureAgent(BaseAgent):
    """Architecture verifier: discovers anchors before deciding to call the LLM.

    Pass adapter= so the agent can fetch anchor files at review time.
    When no anchor applies to any changed file, returns status="skipped".
    """

    agent_name = "architecture"
    prompt_dir = "architecture"

    def __init__(
        self,
        config: GuardianConfig,
        llm_client: LLMClient | None = None,
        adapter=None,
    ) -> None:
        super().__init__(config, llm_client)
        self._adapter = adapter

    async def review(
        self,
        context: ReviewContext,
        *,
        dismissal_context: str | None = None,
    ) -> AgentResult:
        """Discover anchors, select mode, then run or skip."""
        pr = context.pr
        changed_paths = list(context.changed_files)

        anchor_set = await discover_architecture_anchors(
            changed_paths=changed_paths,
            config=self.config,
            adapter=self._adapter,
            repo=pr.repo,
            head_sha=pr.head_commit_sha,
        )

        log.info(
            "arch_mode_selected",
            pr_id=pr.pr_id,
            mode=anchor_set.mode,
            status_reason=anchor_set.status_reason,
        )

        if anchor_set.mode == "skip":
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.PASS,
                status="skipped",
                status_reason=anchor_set.status_reason or _NO_ANCHOR_REASON,
            )

        return await self._run_llm_review(
            context, anchor_set, dismissal_context=dismissal_context
        )

    async def _run_llm_review(
        self,
        context: ReviewContext,
        anchor_set: ArchitectureAnchorSet,
        *,
        dismissal_context: str | None = None,
    ) -> AgentResult:
        """Call the LLM with anchor context and validate returned findings."""
        languages = list(context.language_map.languages.keys())
        override = await storage.get_prompt_override(self.agent_name)

        system_prompt = build_agent_prompt(
            self.prompt_dir, languages, base_override=override
        )
        system_prompt += f"\n\n{AGENT_OUTPUT_SCHEMA}"
        system_prompt += (
            _NARROW_MODE_ADDENDUM
            if anchor_set.mode == "narrow_local_pattern"
            else _FULL_VERIFIER_ADDENDUM
        )

        user_message = self._build_user_message(context, anchor_set, dismissal_context)
        diff_map = {df.path: df.patch for df in context.diff.files}
        model = resolve_model(self.config, self.agent_name)
        llm = self._get_llm()

        try:
            response = await llm.complete(
                system=system_prompt,
                user=user_message,
                model=model,
                max_tokens=self.config.llm.max_tokens,
                temperature=self.config.llm.temperature,
                response_format="json",
            )
            result = self._parse_response(response.content, languages, diff_map=diff_map)
            result.extras["model"] = model
            result.extras["response_length"] = len(response.content)
            result.extras["input_tokens"] = response.input_tokens
            result.extras["output_tokens"] = response.output_tokens

            if anchor_set.mode == "narrow_local_pattern":
                result = self._enforce_local_pattern_constraints(result)

            return result
        except Exception as e:
            log.error("arch_agent_failed", pr_id=context.pr.pr_id, error=str(e))
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.FLAG_HUMAN,
                error=str(e),
                extras={"model": model},
            )

    def _build_user_message(
        self,
        context: ReviewContext,
        anchor_set: ArchitectureAnchorSet,
        dismissal_context: str | None,
    ) -> str:
        """Combine anchor content with the standard diff context."""
        base_context = build_agent_context(
            context,
            self.agent_name,
            max_context_tokens=self.config.agents.max_context_tokens,
            dismissal_context=dismissal_context,
        )

        # Collect unique anchors across all changed paths
        seen: set[str] = set()
        unique_anchors = []
        for path_anchors in anchor_set.anchors_by_path.values():
            for anchor in path_anchors:
                if anchor.path not in seen:
                    seen.add(anchor.path)
                    unique_anchors.append(anchor)

        if not unique_anchors:
            return base_context

        anchor_block: list[str] = [
            "## Architecture Anchors\n",
            "The following documents define the architecture rules and conventions "
            "for this repository.\n",
        ]
        for anchor in unique_anchors:
            scope_note = (
                f" (scoped to: {anchor.scope_glob})"
                if anchor.scope_glob
                else " (global)"
            )
            anchor_block.append(
                f"### [{anchor.anchor_class.upper()}] {anchor.path}{scope_note}\n"
            )
            content_lines = anchor.content.splitlines()
            if len(content_lines) > 150:
                truncated = "\n".join(content_lines[:150])
                anchor_block.append(f"{truncated}\n... (truncated)")
            else:
                anchor_block.append(anchor.content)
            anchor_block.append("")

        return "\n".join(anchor_block) + "\n" + base_context

    def _enforce_local_pattern_constraints(self, result: AgentResult) -> AgentResult:
        """Keep only low/suspected findings in narrow_local_pattern mode.

        Higher-severity or high-certainty findings imply global architecture claims,
        which are out of scope for this mode.
        """
        filtered = [
            f
            for f in result.findings
            if f.severity == Severity.LOW and f.certainty == Certainty.SUSPECTED
        ]
        dropped = len(result.findings) - len(filtered)
        if dropped:
            log.info(
                "arch_local_pattern_findings_dropped",
                dropped=dropped,
                reason="severity > low or certainty != suspected",
            )
        result.findings = filtered
        if not filtered:
            result.verdict = Verdict.PASS
        return result
