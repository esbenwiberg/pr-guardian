"""Contract tests for review agents and prompt composition."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from pr_guardian.agents import prompt_composer
from pr_guardian.agents.architecture_intent import ArchitectureIntentAgent
from pr_guardian.agents.base import BaseAgent
from pr_guardian.agents.code_quality_obs import CodeQualityObservabilityAgent
from pr_guardian.agents.hotspot import HotspotAgent
from pr_guardian.agents.performance import PerformanceAgent
from pr_guardian.agents.prompt_composer import build_agent_prompt, load_prompt
from pr_guardian.agents.security_privacy import SecurityPrivacyAgent
from pr_guardian.agents.test_quality import TestQualityAgent as QualityAgent
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.llm.protocol import LLMResponse
from pr_guardian.models.context import ChangeProfile, FileRole, ReviewContext
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pr_guardian.persistence import storage


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict[str, object]] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        response_format: str | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        return LLMResponse(
            content=self.content,
            model=model or "fake-model",
            input_tokens=11,
            output_tokens=7,
        )


def _context() -> ReviewContext:
    diff = Diff(
        files=[
            DiffFile(
                path="src/auth.py",
                status="modified",
                additions=2,
                deletions=0,
                patch="@@\n+def authenticate(token):\n+    return token == 'secret'\n",
            )
        ]
    )
    return ReviewContext(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="42",
            repo="acme/service",
            repo_url="https://github.com/acme/service",
            source_branch="feature/auth",
            target_branch="main",
            author="dev",
            title="Tighten auth flow",
            head_commit_sha="abc123",
        ),
        repo_path=Path("/tmp/repo"),
        diff=diff,
        changed_files=["src/auth.py"],
        lines_changed=2,
        language_map=LanguageMap(
            languages={"python": ["src/auth.py"]},
            primary_language="python",
            language_count=1,
        ),
        primary_language="python",
        cross_stack=False,
        change_profile=ChangeProfile(
            file_roles={"src/auth.py": {FileRole.PRODUCTION}},
            has_production_changes=True,
            touches_security_surface=True,
        ),
    )


def test_all_review_agents_declare_stable_names_and_prompt_dirs():
    agent_classes = [
        ArchitectureIntentAgent,
        CodeQualityObservabilityAgent,
        HotspotAgent,
        PerformanceAgent,
        SecurityPrivacyAgent,
        QualityAgent,
    ]

    assert {
        cls(GuardianConfig()).agent_name: cls(GuardianConfig()).prompt_dir for cls in agent_classes
    } == {
        "architecture_intent": "architecture_intent",
        "code_quality_observability": "code_quality_observability",
        "hotspot": "hotspot",
        "performance": "performance",
        "security_privacy": "security_privacy",
        "test_quality": "test_quality",
    }


async def test_security_agent_review_parses_behavioral_finding(monkeypatch):
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))
    llm = _FakeLLM(
        json.dumps(
            {
                "verdict": "warn",
                "verdict_explanation": "The new auth check hard-codes a shared secret.",
                "languages_reviewed": ["python"],
                "findings": [
                    {
                        "severity": "high",
                        "certainty": "detected",
                        "category": "hardcoded_secret",
                        "language": "python",
                        "file": "src/auth.py",
                        "line": 2,
                        "description": "A literal secret is used for auth.",
                        "suggestion": "Load the secret from managed configuration.",
                        "cwe": "CWE-798",
                        "evidence_basis": {
                            "saw_full_context": True,
                            "pattern_match": True,
                            "cwe_id": "CWE-798",
                            "similar_code_in_repo": False,
                            "suggestion_is_concrete": True,
                            "cross_references": 1,
                        },
                    }
                ],
                "cross_language_findings": [],
            }
        )
    )
    agent = SecurityPrivacyAgent(GuardianConfig(), llm_client=llm)

    result = await agent.review(_context())

    assert result.agent_name == "security_privacy"
    assert result.verdict == Verdict.WARN
    assert result.verdict_explanation == "The new auth check hard-codes a shared secret."
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity == Severity.HIGH
    assert finding.certainty == Certainty.DETECTED
    assert finding.file == "src/auth.py"
    assert finding.evidence_basis.cwe_id == "CWE-798"
    assert result.extras["input_tokens"] == 11
    assert llm.calls[0]["response_format"] == "json"


async def test_agent_review_invalid_json_flags_human_with_actionable_error(monkeypatch):
    monkeypatch.setattr(storage, "get_prompt_override", AsyncMock(return_value=None))
    agent = PerformanceAgent(GuardianConfig(), llm_client=_FakeLLM("not json at all"))

    result = await agent.review(_context())

    assert result.agent_name == "performance"
    assert result.verdict == Verdict.FLAG_HUMAN
    assert result.error == "Invalid JSON response from LLM"
    assert result.findings == []


def test_parse_response_repairs_truncated_json_array():
    raw = """
    ```json
    {"verdict":"warn","verdict_explanation":"Needs review","languages_reviewed":["python"],"findings":[{"severity":"medium","certainty":"suspected","category":"bounds","language":"python","file":"src/a.py","line":4,"description":"Loop may be unbounded","suggestion":"Add an upper bound","cwe":null,"evidence_basis":{"saw_full_context":true,"pattern_match":true,"cwe_id":null,"similar_code_in_repo":false,"suggestion_is_concrete":true,"cross_references":1}}
    ```
    """

    result = BaseAgent(GuardianConfig())._parse_response(raw, ["python"])

    assert result.verdict == Verdict.WARN
    assert result.findings[0].category == "bounds"
    assert result.findings[0].evidence_basis.suggestion_is_concrete is True


def test_build_agent_prompt_uses_override_and_cross_language_section():
    prompt = build_agent_prompt(
        "security_privacy",
        ["python", "typescript"],
        base_override="Custom security rubric",
    )

    assert prompt.startswith("Custom security rubric")
    assert "CROSS-LANGUAGE CONCERNS" in prompt
    assert "security privacy review agent" not in prompt


def test_load_prompt_returns_none_for_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt_composer, "PROMPTS_DIR", tmp_path)

    assert load_prompt("missing/base.md") is None
