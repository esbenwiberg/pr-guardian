from __future__ import annotations

import structlog

from pr_guardian.agents.base import SCOPE_OPACITY_CATEGORY, BaseAgent
from pr_guardian.agents.intent_anchors import load_intent_anchors
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.context import ReviewContext
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    Finding,
    Severity,
    Verdict,
)

log = structlog.get_logger()

# Quote used for every scope-opacity finding (contract from Brief 02).
SCOPE_OPACITY_QUOTE = "PR title/body lacks a useful intent anchor"


class IntentAgent(BaseAgent):
    """Intent verifier: checks whether a medium/high PR has a useful intent anchor.

    Scheduled for medium/high risk PRs only; never scheduled for low/trivial.
    No LLM call in v1 — purely rule-based anchor classification.

    When no useful anchor is found and the PR meets the configured size gate,
    emits one medium/suspected PR-level scope-opacity finding with line=None.
    """

    agent_name = "intent"
    prompt_dir = "intent"

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
        """Run intent verification without calling an LLM."""
        pr = context.pr
        iv = self.config.intent_verification

        anchor_ctx = await load_intent_anchors(
            title=pr.title,
            body=pr.body,
            adapter=self._adapter,
            repo=pr.repo,
            head_sha=pr.head_commit_sha,
        )

        log.info(
            "intent_anchor_classified",
            pr_id=pr.pr_id,
            has_useful_anchor=anchor_ctx.has_useful_anchor,
            anchor_kind=anchor_ctx.anchor_kind,
        )

        if anchor_ctx.has_useful_anchor:
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.PASS,
                status="ran",
            )

        # No useful anchor — check size gate before emitting scope-opacity finding.
        files_threshold: int = getattr(iv, "size_gate_files", 5)
        lines_threshold: int = getattr(iv, "size_gate_lines", 150)

        meets_gate = (
            len(context.changed_files) >= files_threshold
            or context.lines_changed >= lines_threshold
        )

        if not meets_gate:
            log.info(
                "intent_scope_opacity_skipped",
                pr_id=pr.pr_id,
                reason="PR is below size gate",
                changed_files=len(context.changed_files),
                lines_changed=context.lines_changed,
            )
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.PASS,
                status="ran",
            )

        missing_reason = (
            anchor_ctx.missing_reason
            or "No concrete behavior or scope claim found in the PR title/body."
        )
        description = (
            f"This PR lacks a useful intent anchor. {missing_reason} "
            "Consider adding a description that explains the change scope, or "
            "link to a spec file under specs/... for larger changes."
        )
        suggestion = (
            "Add a PR description with at least 80 characters describing the "
            "concrete behavior or scope change. For large changes, reference a "
            "spec file at specs/<name>.md."
        )

        finding = Finding(
            severity=Severity.MEDIUM,
            certainty=Certainty.SUSPECTED,
            category=SCOPE_OPACITY_CATEGORY,
            language="",
            file="",
            line=None,
            description=description,
            quote=SCOPE_OPACITY_QUOTE,
            suggestion=suggestion,
        )

        return AgentResult(
            agent_name=self.agent_name,
            verdict=Verdict.WARN,
            status="ran",
            findings=[finding],
        )
