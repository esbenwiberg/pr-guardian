from __future__ import annotations

import json

import structlog

from pr_guardian.agents.context_builder import build_agent_context
from pr_guardian.agents.prompt_composer import build_agent_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.context import ReviewContext
from pr_guardian.models.findings import (
    AgentResult,
    Certainty,
    EvidenceBasis,
    Finding,
    Severity,
    Verdict,
)

log = structlog.get_logger()

AGENT_OUTPUT_SCHEMA = """
Respond with valid JSON matching this schema:
{
  "verdict": "pass | warn | flag_human",
  "languages_reviewed": ["python", "typescript"],
  "findings": [
    {
      "severity": "low | medium | high | critical",
      "certainty": "detected | suspected | uncertain",
      "category": "string",
      "language": "string",
      "file": "string",
      "line": null or integer,
      "description": "string",
      "suggestion": "string",
      "cwe": "CWE-xxx or null",
      "evidence_basis": {
        "saw_full_context": true/false,
        "pattern_match": true/false,
        "cwe_id": "string or null",
        "similar_code_in_repo": true/false,
        "suggestion_is_concrete": true/false,
        "cross_references": integer
      }
    }
  ],
  "cross_language_findings": []
}
If no issues found, return {"verdict": "pass", "languages_reviewed": [...], "findings": [], "cross_language_findings": []}.
"""


class BaseAgent:
    """Base class for all AI review agents."""

    agent_name: str = "base"
    prompt_dir: str = "base"

    def __init__(self, config: GuardianConfig, llm_client: LLMClient | None = None):
        self.config = config
        self._llm = llm_client

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm_client(self.config)
        return self._llm

    async def review(self, context: ReviewContext) -> AgentResult:
        """Run the agent review. Override for custom behavior."""
        languages = list(context.language_map.languages.keys())
        system_prompt = build_agent_prompt(self.prompt_dir, languages)
        system_prompt += f"\n\n{AGENT_OUTPUT_SCHEMA}"

        user_message = build_agent_context(context, self.agent_name)

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
            result = self._parse_response(response.content, languages)
            result.extras["model"] = model
            result.extras["response_length"] = len(response.content)
            result.extras["input_tokens"] = response.input_tokens
            result.extras["output_tokens"] = response.output_tokens
            if result.verdict == Verdict.FLAG_HUMAN and not result.findings:
                result.extras["raw_response_preview"] = response.content[:1000]
            return result
        except Exception as e:
            log.error("agent_failed", agent=self.agent_name, error=str(e))
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.FLAG_HUMAN,
                error=str(e),
                extras={"model": model},
            )

    def _parse_response(self, raw: str, languages: list[str]) -> AgentResult:
        """Parse LLM JSON response into AgentResult."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("agent_invalid_json", agent=self.agent_name)
            return AgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.FLAG_HUMAN,
                error="Invalid JSON response from LLM",
            )

        verdict = Verdict(data.get("verdict", "flag_human"))
        findings = [self._parse_finding(f) for f in data.get("findings", [])]
        cross_lang = [self._parse_finding(f) for f in data.get("cross_language_findings", [])]

        return AgentResult(
            agent_name=self.agent_name,
            verdict=verdict,
            languages_reviewed=data.get("languages_reviewed", languages),
            findings=findings,
            cross_language_findings=cross_lang,
        )

    def _parse_finding(self, data: dict) -> Finding:
        evidence_data = data.get("evidence_basis", {})
        return Finding(
            severity=Severity(data.get("severity", "low")),
            certainty=Certainty(data.get("certainty", "uncertain")),
            category=data.get("category", ""),
            language=data.get("language", ""),
            file=data.get("file", ""),
            line=data.get("line"),
            description=data.get("description", ""),
            suggestion=data.get("suggestion", ""),
            cwe=data.get("cwe"),
            compliance=data.get("compliance"),
            evidence_basis=EvidenceBasis(
                saw_full_context=evidence_data.get("saw_full_context", False),
                pattern_match=evidence_data.get("pattern_match", False),
                cwe_id=evidence_data.get("cwe_id"),
                similar_code_in_repo=evidence_data.get("similar_code_in_repo", False),
                suggestion_is_concrete=evidence_data.get("suggestion_is_concrete", False),
                cross_references=evidence_data.get("cross_references", 0),
            ),
        )
