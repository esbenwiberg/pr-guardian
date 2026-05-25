"""Tests for the architecture verifier agent (Brief 04).

Required fact coverage:
  fact-architecture-skip-status    → skip_status tests
  fact-local-pattern-low-suspected → local_pattern tests
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pr_guardian.agents.architecture import ArchitectureAgent
from pr_guardian.config.schema import ArchitectureConfig, GuardianConfig
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    FileRole,
    RepoRiskClass,
    ReviewContext,
    SecuritySurface,
)
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pr(repo: str = "org/repo") -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="99",
        repo=repo,
        repo_url="",
        source_branch="feature/arch-test",
        target_branch="main",
        author="dev",
        title="Refactor services",
        head_commit_sha="abc123",
        body="Refactors the service layer.",
    )


def _make_context(
    pr: PlatformPR | None = None,
    changed_files: list[str] | None = None,
    patch: str = "",
) -> ReviewContext:
    if changed_files is None:
        changed_files = ["src/service.py"]
    if pr is None:
        pr = _make_pr()
    diff_files = [
        DiffFile(path=f, status="modified", additions=2, deletions=0, patch=patch)
        for f in changed_files
    ]
    return ReviewContext(
        pr=pr,
        repo_path=Path("/tmp/test-arch"),
        diff=Diff(files=diff_files),
        changed_files=changed_files,
        lines_changed=len(changed_files) * 3,
        language_map=LanguageMap(
            languages={"python": changed_files},
            primary_language="python",
            language_count=1,
        ),
        primary_language="python",
        cross_stack=False,
        repo_config={},
        repo_risk_class=RepoRiskClass.STANDARD,
        hotspots=set(),
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        change_profile=ChangeProfile(
            file_roles={f: {FileRole.PRODUCTION} for f in changed_files},
            has_production_changes=True,
        ),
    )


def _config(
    mode_override: str = "auto",
    architecture_docs: list[str] | None = None,
) -> GuardianConfig:
    cfg = GuardianConfig()
    cfg.architecture = ArchitectureConfig(mode_override=mode_override)
    cfg.architecture_docs = architecture_docs or []
    return cfg


class SkipAdapter:
    """Adapter that returns 404 for every file (no architecture anchors)."""

    async def fetch_file_content(self, repo, path, ref="HEAD"):
        raise FileNotFoundError(f"not found: {path}")

    async def list_repo_files(self, repo, ref="HEAD", path=""):
        return []


class AgentsMdAdapter:
    """Adapter that returns architecture-relevant AGENTS.md (rank 7 → narrow mode)."""

    async def fetch_file_content(self, repo, path, ref="HEAD"):
        if path == "AGENTS.md":
            return (
                "# Architecture\n\n"
                "- All service classes must live under services/.\n"
                "- Never import infrastructure modules from domain code.\n"
            )
        raise FileNotFoundError(f"not found: {path}")

    async def list_repo_files(self, repo, ref="HEAD", path=""):
        return []


class FullVerifierAdapter:
    """Adapter that returns ARCHITECTURE.md with imperative rules (rank 4 rule)
    and AGENTS.md with architecture section (rank 7) → full_verifier."""

    async def fetch_file_content(self, repo, path, ref="HEAD"):
        if path == "ARCHITECTURE.md":
            return (
                "# Architecture\n\n"
                "All domain services must use the repository pattern. "
                "Infrastructure code must never import from domain."
            )
        if path == "AGENTS.md":
            return (
                "# Architecture\n\n"
                "- All writes must go through domain services.\n"
            )
        raise FileNotFoundError(f"not found: {path}")

    async def list_repo_files(self, repo, ref="HEAD", path=""):
        return []


class MockLLMClient:
    """Fake LLM that returns a canned JSON response."""

    def __init__(self, response_json: dict):
        self._response = json.dumps(response_json)

    async def complete(self, *, system, user, model, **kwargs):
        class _Resp:
            content = self._response
            input_tokens = 100
            output_tokens = 50

        return _Resp()


def _llm_response(
    verdict: str = "warn",
    findings: list[dict] | None = None,
    verdict_explanation: str | None = "Test explanation",
) -> dict:
    return {
        "verdict": verdict,
        "verdict_explanation": verdict_explanation,
        "languages_reviewed": ["python"],
        "findings": findings or [],
        "cross_language_findings": [],
    }


def _finding(
    severity: str = "low",
    certainty: str = "suspected",
    file: str = "src/service.py",
    line: int = 2,
    quote: str = "from infrastructure import db_connection",
    category: str = "layer-violation",
) -> dict:
    return {
        "severity": severity,
        "certainty": certainty,
        "category": category,
        "language": "python",
        "file": file,
        "line": line,
        "quote": quote,
        "description": "Layer violation detected",
        "suggestion": "Use a repository interface instead",
        "cwe": None,
        "evidence_basis": {
            "saw_full_context": True,
            "pattern_match": True,
            "cwe_id": None,
            "similar_code_in_repo": False,
            "suggestion_is_concrete": True,
            "cross_references": 0,
        },
    }


# A patch that contains the matching + line for our test quote
_PATCH_WITH_QUOTE = (
    "--- a/src/service.py\n"
    "+++ b/src/service.py\n"
    "@@ -1,3 +1,4 @@\n"
    " import os\n"
    "+from infrastructure import db_connection\n"
    " def main():\n"
    "     pass\n"
)


# ---------------------------------------------------------------------------
# skip_status — fact-architecture-skip-status
# ---------------------------------------------------------------------------

class TestSkipStatus:
    """Agent returns status=skipped when no architecture anchor applies."""

    @pytest.mark.asyncio
    async def test_skip_status_when_no_anchors_found(self):
        """No architecture files → status=skipped, no findings, no score contribution."""
        adapter = SkipAdapter()
        agent = ArchitectureAgent(_config(), adapter=adapter)
        context = _make_context()
        result = await agent.review(context)

        assert result.status == "skipped"
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_skip_status_reason_is_no_architecture_context(self):
        """status_reason describes why the agent was skipped."""
        agent = ArchitectureAgent(_config(), adapter=SkipAdapter())
        result = await agent.review(_make_context())
        assert result.status_reason == "no architecture context found"

    @pytest.mark.asyncio
    async def test_skip_status_verdict_is_pass(self):
        """Skipped agent returns verdict=pass (does not block the review)."""
        agent = ArchitectureAgent(_config(), adapter=SkipAdapter())
        result = await agent.review(_make_context())
        assert result.verdict == Verdict.PASS

    @pytest.mark.asyncio
    async def test_skip_status_agent_name_is_architecture(self):
        """AgentResult.agent_name identifies the architecture agent."""
        agent = ArchitectureAgent(_config(), adapter=SkipAdapter())
        result = await agent.review(_make_context())
        assert result.agent_name == "architecture"

    @pytest.mark.asyncio
    async def test_skip_status_no_findings_in_result(self):
        """Skipped result has an empty findings list."""
        agent = ArchitectureAgent(_config(), adapter=SkipAdapter())
        result = await agent.review(_make_context())
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_skip_status_mode_override_skip_bypasses_adapter(self):
        """mode_override=skip → skipped without any adapter calls."""
        fetched: list[str] = []

        class TrackingAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                fetched.append(path)
                raise FileNotFoundError

            async def list_repo_files(self, repo, ref="HEAD", path=""):
                return []

        cfg = _config(mode_override="skip")
        agent = ArchitectureAgent(cfg, adapter=TrackingAdapter())
        result = await agent.review(_make_context())

        assert result.status == "skipped"
        assert fetched == []  # no adapter calls when override=skip

    @pytest.mark.asyncio
    async def test_skip_status_without_adapter_is_skipped(self):
        """No adapter passed → can't discover anchors → skipped."""
        agent = ArchitectureAgent(_config(), adapter=None)
        result = await agent.review(_make_context())
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_skip_status_is_not_counted_as_pass(self):
        """Skipped status is distinct from 'ran' — status field must be 'skipped'."""
        agent = ArchitectureAgent(_config(), adapter=SkipAdapter())
        result = await agent.review(_make_context())
        # status must be exactly "skipped", not "ran"
        assert result.status == "skipped"
        assert result.status != "ran"


# ---------------------------------------------------------------------------
# local_pattern — fact-local-pattern-low-suspected
# ---------------------------------------------------------------------------

class TestLocalPattern:
    """Agent in narrow_local_pattern mode enforces low/suspected findings only."""

    @pytest.mark.asyncio
    async def test_local_pattern_low_suspected_finding_is_kept(self):
        """A low/suspected finding from the LLM is kept in narrow_local_pattern mode."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="low", certainty="suspected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.severity == Severity.LOW
        assert f.certainty == Certainty.SUSPECTED

    @pytest.mark.asyncio
    async def test_local_pattern_high_severity_finding_is_dropped(self):
        """A high-severity finding is dropped in narrow_local_pattern mode."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="high", certainty="suspected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert result.findings == []
        assert result.verdict == Verdict.PASS

    @pytest.mark.asyncio
    async def test_local_pattern_medium_severity_finding_is_dropped(self):
        """A medium-severity finding is dropped in narrow_local_pattern mode."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="medium", certainty="suspected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_detected_certainty_finding_is_dropped(self):
        """A detected-certainty finding is dropped in narrow_local_pattern mode."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="low", certainty="detected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_uncertain_certainty_finding_is_dropped(self):
        """An uncertain-certainty finding is dropped in narrow_local_pattern mode."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="low", certainty="uncertain")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_finding_is_quote_grounded(self):
        """Low/suspected findings must cite a real + diff line (quote)."""
        quote_text = "from infrastructure import db_connection"
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[_finding(severity="low", certainty="suspected", quote=quote_text)],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert len(result.findings) == 1
        assert result.findings[0].quote == quote_text

    @pytest.mark.asyncio
    async def test_local_pattern_unquoted_finding_is_dropped_by_validation(self):
        """A low/suspected finding with a non-matching quote is dropped by the base parser."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="warn",
                findings=[
                    _finding(
                        severity="low",
                        certainty="suspected",
                        quote="this line is not in the diff",
                    )
                ],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        # Quote mismatch → dropped by _is_valid_finding before local-pattern filter
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_mode_is_narrow_not_full_verifier(self):
        """AGENTS.md alone → narrow_local_pattern mode (not full_verifier)."""
        # We verify the mode by checking that high-severity findings are dropped
        # (which only happens in narrow_local_pattern mode)
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="flag_human",
                findings=[_finding(severity="critical", certainty="detected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        # critical/detected would be kept in full_verifier but is dropped in narrow mode
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_verdict_becomes_pass_when_all_dropped(self):
        """All high-severity findings dropped → verdict recomputed to pass."""
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="flag_human",
                findings=[_finding(severity="high", certainty="suspected")],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=AgentsMdAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        assert result.verdict == Verdict.PASS
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_local_pattern_full_verifier_keeps_high_severity(self):
        """In full_verifier mode high-severity findings are NOT dropped."""
        quote_text = "from infrastructure import db_connection"
        mock_llm = MockLLMClient(
            _llm_response(
                verdict="flag_human",
                verdict_explanation="High severity layer violation",
                findings=[_finding(severity="high", certainty="detected", quote=quote_text)],
            )
        )
        agent = ArchitectureAgent(
            _config(), llm_client=mock_llm, adapter=FullVerifierAdapter()
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)

        # full_verifier mode does not drop high-severity findings
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_local_pattern_agent_name_is_architecture(self):
        """AgentResult.agent_name is 'architecture' in all modes."""
        agent = ArchitectureAgent(
            _config(),
            llm_client=MockLLMClient(_llm_response(verdict="pass", findings=[])),
            adapter=AgentsMdAdapter(),
        )
        context = _make_context(patch=_PATCH_WITH_QUOTE)
        result = await agent.review(context)
        assert result.agent_name == "architecture"
