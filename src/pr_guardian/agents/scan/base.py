"""Base class for scan agents (recent changes + maintenance)."""
from __future__ import annotations

import json
import re

import structlog

from pr_guardian.agents.prompt_composer import build_agent_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.scan import ScanAgentResult, ScanContext, ScanFinding

log = structlog.get_logger()

SCAN_OUTPUT_SCHEMA = """
Respond with ONLY raw valid JSON (no markdown fences, no commentary) matching this schema:
{
  "verdict": "pass | warn | flag_human",
  "findings": [
    {
      "severity": "low | medium | high | critical",
      "certainty": "detected | suspected | uncertain",
      "category": "string",
      "file": "string",
      "line": null or integer,
      "description": "string",
      "suggestion": "string",
      "priority": 0.0 to 1.0,
      "effort_estimate": "small | medium | large"
    }
  ],
  "summary": "one paragraph overall summary"
}
If no issues found, return {"verdict": "pass", "findings": [], "summary": "No issues found."}.
"""


class ScanBaseAgent:
    """Base class for all scan agents."""

    agent_name: str = "scan_base"
    prompt_dir: str = "scan_base"

    def __init__(self, config: GuardianConfig, llm_client: LLMClient | None = None):
        self.config = config
        self._llm = llm_client

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm_client(self.config)
        return self._llm

    def build_user_message(self, context: ScanContext) -> str:
        """Build the user message for this agent. Override in subclasses for custom context."""
        parts: list[str] = []
        parts.append(f"## Scan: {context.scan_type.value}")
        parts.append(f"- Repository: {context.repo}")
        parts.append(f"- Platform: {context.platform}")

        if context.scan_type.value == "recent_changes":
            parts.append(f"- Time window: {context.time_window_days} days")
            parts.append(f"- Merged PRs: {len(context.merged_prs)}")
            parts.append(f"- Commits: {len(context.commits)}")

            if context.change_summary:
                parts.append(f"\n## Change Summary\n{context.change_summary}")

            if context.merged_prs:
                parts.append("\n## Merged PRs")
                for pr in context.merged_prs[:50]:
                    title = pr.get("title", "untitled")
                    author = pr.get("user", {}).get("login", "unknown") if isinstance(pr.get("user"), dict) else pr.get("author", "unknown")
                    number = pr.get("number", "?")
                    parts.append(f"- #{number}: {title} (by {author})")

            if context.changes_by_module:
                parts.append("\n## Changes by Module")
                for module, files in sorted(context.changes_by_module.items()):
                    parts.append(f"\n### {module} ({len(files)} files)")
                    for f in files[:20]:
                        parts.append(f"  - {f.get('filename', f.get('path', '?'))} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})")
                    if len(files) > 20:
                        parts.append(f"  ... and {len(files) - 20} more files")

        elif context.scan_type.value == "maintenance":
            parts.append(f"- Staleness threshold: {context.staleness_months} months")
            parts.append(f"- Stale files analyzed: {len(context.stale_files)}")

            if context.stale_files:
                parts.append("\n## Stale Files")
                for sf in context.stale_files:
                    path = sf.get("path", "?")
                    last_mod = sf.get("last_modified", "unknown")
                    size = sf.get("size", 0)
                    parts.append(f"- {path} (last modified: {last_mod}, {size} bytes)")

            if context.file_contents:
                parts.append("\n## File Contents")
                for path, content in context.file_contents.items():
                    # Truncate large files
                    if len(content) > 8000:
                        content = content[:8000] + f"\n... [truncated, {len(content)} total chars]"
                    parts.append(f"\n### {path}\n```\n{content}\n```")

        return "\n".join(parts)

    async def analyze(self, context: ScanContext) -> ScanAgentResult:
        """Run the scan agent analysis."""
        system_prompt = build_agent_prompt(self.prompt_dir, [])
        system_prompt += f"\n\n{SCAN_OUTPUT_SCHEMA}"

        user_message = self.build_user_message(context)

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
            result = self._parse_response(response.content)
            result.extras["model"] = model
            result.extras["response_length"] = len(response.content)
            result.extras["input_tokens"] = response.input_tokens
            result.extras["output_tokens"] = response.output_tokens
            return result
        except Exception as e:
            log.error("scan_agent_failed", agent=self.agent_name, error=str(e))
            return ScanAgentResult(
                agent_name=self.agent_name,
                verdict=Verdict.FLAG_HUMAN,
                error=str(e),
                extras={"model": model},
            )

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON from potentially markdown-wrapped LLM response."""
        stripped = raw.strip()
        if stripped.startswith("{"):
            return stripped
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"```(?:json)?\s*\n?(.*)", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
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

        in_string = False
        escape_next = False
        stack: list[str] = []

        for ch in text:
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

        if not stack and not in_string:
            return text

        repaired = text
        if in_string:
            repaired += '"'
        candidate = repaired.rstrip().rstrip(",") + "".join(reversed(stack))
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            return text

    def _parse_response(self, raw: str) -> ScanAgentResult:
        """Parse LLM JSON response into ScanAgentResult."""
        extracted = self._extract_json(raw)
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            repaired = self._repair_truncated_json(extracted)
            try:
                data = json.loads(repaired)
                log.info("scan_agent_json_repaired", agent=self.agent_name)
            except json.JSONDecodeError:
                log.warning(
                    "scan_agent_invalid_json",
                    agent=self.agent_name,
                    raw_preview=raw[:500],
                )
                return ScanAgentResult(
                    agent_name=self.agent_name,
                    verdict=Verdict.FLAG_HUMAN,
                    error="Invalid JSON response from LLM",
                )

        verdict = Verdict(data.get("verdict", "flag_human"))
        findings = [self._parse_finding(f) for f in data.get("findings", [])]
        summary = data.get("summary", "")

        return ScanAgentResult(
            agent_name=self.agent_name,
            verdict=verdict,
            findings=findings,
            summary=summary,
        )

    def _parse_finding(self, data: dict) -> ScanFinding:
        return ScanFinding(
            severity=Severity(data.get("severity", "low")),
            certainty=Certainty(data.get("certainty", "uncertain")),
            category=data.get("category", ""),
            file=data.get("file", ""),
            line=data.get("line"),
            description=data.get("description", ""),
            suggestion=data.get("suggestion", ""),
            agent_name=self.agent_name,
            priority=float(data.get("priority", 0.0)),
            effort_estimate=data.get("effort_estimate"),
        )
