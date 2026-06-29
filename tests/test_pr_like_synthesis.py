"""Cross-PR synthesis over deep-scan per-PR outcomes."""

from __future__ import annotations

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core import pr_like_synthesis as syn
from pr_guardian.core.pr_like_synthesis import (
    SYNTHESIS_DISPLAY_NAME,
    synthesize_cross_pr,
)
from pr_guardian.llm.protocol import LLMResponse
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.scan import ScanAgentResult, ScanFinding


class _FakeLLM:
    def __init__(self, content="**Recurring issues**\n- Missing tests in #1, #2.", raises=False):
        self.content = content
        self.raises = raises
        self.calls: list[dict] = []

    async def complete(self, system, user, model=None, **kw):
        self.calls.append({"system": system, "user": user, "model": model})
        if self.raises:
            raise RuntimeError("llm down")
        return LLMResponse(
            content=self.content, model=model or "m", input_tokens=120, output_tokens=80
        )

    @property
    def provider_name(self):
        return "fake"


def _finding(category, file, line, desc, lens="security_privacy"):
    return ScanFinding(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category=category,
        file=file,
        line=line,
        description=desc,
        agent_name=lens,
    )


def _pr_card(n, verdict=Verdict.WARN, findings=None, error=None):
    return ScanAgentResult(
        agent_name=f"PR #{n}: title {n}",
        verdict=verdict,
        findings=findings or [],
        summary=f"summary {n}",
        error=error,
    )


async def test_synthesis_skipped_below_two_reviews():
    llm = _FakeLLM()
    out = await synthesize_cross_pr([_pr_card(1)], "o/r", GuardianConfig(), llm_client=llm)
    assert out is None
    assert llm.calls == []  # never called the LLM


async def test_synthesis_skips_when_only_one_pr_actually_reviewed():
    # Two cards, but one is a failed review — only one real outcome to synthesize.
    llm = _FakeLLM()
    cards = [_pr_card(1), _pr_card(2, error="boom")]
    out = await synthesize_cross_pr(cards, "o/r", GuardianConfig(), llm_client=llm)
    assert out is None
    assert llm.calls == []


async def test_synthesis_happy_path_builds_card():
    llm = _FakeLLM(content="**Hotspots**\n- auth.py flagged in #1 and #3.")
    cards = [
        _pr_card(1, Verdict.WARN, [_finding("auth", "auth.py", 10, "missing check")]),
        _pr_card(2, Verdict.PASS),
        _pr_card(3, Verdict.FLAG_HUMAN, [_finding("auth", "auth.py", 22, "bypass")]),
    ]
    out = await synthesize_cross_pr(cards, "o/r", GuardianConfig(), llm_client=llm)

    assert out is not None
    assert out.agent_name == SYNTHESIS_DISPLAY_NAME
    assert out.verdict == Verdict.PASS  # narrative card never blocks
    assert out.findings == []  # never emits findings (would double-count)
    assert "auth.py flagged" in out.summary
    assert out.extras["input_tokens"] == 120
    assert out.extras["output_tokens"] == 80
    assert out.extras["cost_usd"] > 0

    # Digest carries verdict distribution + per-PR findings, but no raw diffs.
    user = llm.calls[0]["user"]
    assert "Verdict distribution" in user
    assert "auth.py:10" in user
    assert "PR #1: title 1" in user


async def test_synthesis_llm_failure_is_non_fatal():
    llm = _FakeLLM(raises=True)
    cards = [_pr_card(1), _pr_card(2), _pr_card(3)]
    out = await synthesize_cross_pr(cards, "o/r", GuardianConfig(), llm_client=llm)
    assert out is None  # degrades gracefully — caller still saves per-PR results


async def test_synthesis_empty_response_returns_none():
    llm = _FakeLLM(content="   ")
    cards = [_pr_card(1), _pr_card(2)]
    out = await synthesize_cross_pr(cards, "o/r", GuardianConfig(), llm_client=llm)
    assert out is None


def test_build_digest_is_diff_free_and_bounded():
    cards = [
        _pr_card(1, Verdict.WARN, [_finding("x", "a.py", 1, "z" * 500)]),
        _pr_card(2, Verdict.PASS),
    ]
    digest = syn._build_digest(cards, "o/r")
    assert "```" not in digest  # no code fences / diffs
    assert "…" in digest  # long description truncated
    assert "No findings." in digest  # the clean PR is represented
