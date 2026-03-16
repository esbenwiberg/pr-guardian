from __future__ import annotations

import json
import re

import structlog

from pr_guardian.agents.context_builder import build_agent_context
from pr_guardian.agents.prompt_composer import build_agent_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.persistence import storage
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
IMPORTANT — EVIDENCE RULES:
- Only report findings based on code you can actually see in the diff below.
- If a file is marked "[diff content not available]" or "[diff truncated]", do NOT guess what the unseen code contains.
- Never infer file contents from filenames or common patterns. If you cannot cite a specific line or pattern from the visible diff, do not report a finding.
- Findings that speculate about code you have not seen will be discarded.

IMPORTANT — SCOPE RULES:
- Only flag issues in lines the PR ADDS or MODIFIES (lines starting with `+` in the diff).
- Do NOT flag pre-existing patterns, style issues, or code smells in context lines (lines starting with ` ` or `-`). These are handled by separate scheduled scans.
- Exception: you MAY flag surrounding context if the NEW code in this PR creates a risk that did not exist before — e.g., new code that misuses an existing variable in an unsafe way. In that case, explain in the description why this is a NEW risk introduced by this change, not a pre-existing issue.
- Findings about pre-existing code that is not affected by this change will be discarded.

Respond with ONLY raw valid JSON (no markdown fences, no commentary) matching this schema:
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

    async def review(self, context: ReviewContext, *, dismissal_context: str | None = None) -> AgentResult:
        """Run the agent review. Override for custom behavior."""
        languages = list(context.language_map.languages.keys())
        override = await storage.get_prompt_override(self.agent_name)
        system_prompt = build_agent_prompt(self.prompt_dir, languages, base_override=override)
        system_prompt += f"\n\n{AGENT_OUTPUT_SCHEMA}"

        user_message = build_agent_context(
            context, self.agent_name,
            max_context_tokens=self.config.agents.max_context_tokens,
            dismissal_context=dismissal_context,
        )

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

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON from potentially markdown-wrapped LLM response."""
        stripped = raw.strip()
        # Try raw string first
        if stripped.startswith("{"):
            return stripped
        # Extract from ```json ... ``` or ``` ... ``` fences
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Handle truncated response where closing fence is missing
        match = re.search(r"```(?:json)?\s*\n?(.*)", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Try to find a JSON object anywhere in the text
        match = re.search(r"\{.*", stripped, re.DOTALL)
        if match:
            return match.group(0).strip()
        return stripped

    @staticmethod
    def _repair_truncated_json(text: str) -> str:
        """Best-effort repair of truncated JSON by closing unclosed brackets."""
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # Walk the string tracking brackets/braces and comma positions
        in_string = False
        escape_next = False
        stack: list[str] = []
        comma_positions: list[int] = []

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in "}]" and stack:
                stack.pop()
            elif ch == ",":
                comma_positions.append(i)

        if not stack and not in_string:
            return text  # Structurally complete; parse error is something else

        # Strategy 1: close open string, strip trailing comma, close brackets
        repaired = text
        if in_string:
            repaired += '"'
        closers = "".join(reversed(stack))
        candidate = repaired.rstrip().rstrip(",") + closers
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

        # Strategy 2: trim back to last few commas, re-close brackets
        for pos in reversed(comma_positions[-5:]):
            trimmed = text[:pos]
            stk: list[str] = []
            s, e = False, False
            for ch in trimmed:
                if e:
                    e = False
                    continue
                if ch == "\\" and s:
                    e = True
                    continue
                if ch == '"':
                    s = not s
                    continue
                if s:
                    continue
                if ch == "{":
                    stk.append("}")
                elif ch == "[":
                    stk.append("]")
                elif ch in "}]" and stk:
                    stk.pop()
            candidate = trimmed + "".join(reversed(stk))
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

        return text

    def _parse_response(self, raw: str, languages: list[str]) -> AgentResult:
        """Parse LLM JSON response into AgentResult."""
        extracted = self._extract_json(raw)
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            # Attempt repair for truncated responses
            repaired = self._repair_truncated_json(extracted)
            try:
                data = json.loads(repaired)
                log.info("agent_json_repaired", agent=self.agent_name)
            except json.JSONDecodeError:
                log.warning(
                    "agent_invalid_json",
                    agent=self.agent_name,
                    raw_preview=raw[:500],
                )
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
