"""Tests for the intent verifier agent (Brief 03).

Required fact coverage:
  fact-intent-anchor-heuristic     → anchor_heuristic tests
  fact-intent-medium-high-scope-opacity → intent_medium_high tests
  fact-no-workitem-v1              → no_workitem tests
"""
from __future__ import annotations

import pytest

from pr_guardian.agents.base import SCOPE_OPACITY_CATEGORY
from pr_guardian.agents.intent import IntentAgent, SCOPE_OPACITY_QUOTE
from pr_guardian.agents.intent_anchors import IntentAnchorContext, load_intent_anchors
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.models.context import (
    BlastRadius,
    ChangeProfile,
    FileRole,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
)
from pr_guardian.models.findings import Certainty, Severity, Verdict
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.pr import Diff, DiffFile, Platform, PlatformPR
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pr(title: str = "My PR", body: str | None = None) -> PlatformPR:
    return PlatformPR(
        platform=Platform.GITHUB,
        pr_id="42",
        repo="org/repo",
        repo_url="",
        source_branch="feature/x",
        target_branch="main",
        author="dev",
        title=title,
        head_commit_sha="abc123",
        body=body,
    )


def _make_context(
    pr: PlatformPR | None = None,
    changed_files: list[str] | None = None,
    lines_changed: int = 10,
) -> ReviewContext:
    if changed_files is None:
        changed_files = ["src/main.py"]
    if pr is None:
        pr = _make_pr()
    diff_files = [
        DiffFile(path=f, status="modified", additions=2, deletions=1)
        for f in changed_files
    ]
    return ReviewContext(
        pr=pr,
        repo_path=Path("/tmp/test"),
        diff=Diff(files=diff_files),
        changed_files=changed_files,
        lines_changed=lines_changed,
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


# ---------------------------------------------------------------------------
# anchor_heuristic — fact-intent-anchor-heuristic
# ---------------------------------------------------------------------------

class TestAnchorHeuristic:
    """Tests for load_intent_anchors() heuristic logic."""

    @pytest.mark.asyncio
    async def test_anchor_heuristic_spec_ref_fetched_is_useful(self):
        """A fetchable specs/... markdown path is a useful anchor (kind=spec)."""
        fetched: list[tuple] = []

        class FakeAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                fetched.append((repo, path, ref))
                return "# Feature spec\n\nThis spec describes auth changes."

        ctx = await load_intent_anchors(
            title="Add auth token refresh",
            body="See specs/auth/token-refresh.md for details.",
            adapter=FakeAdapter(),
            repo="org/repo",
            head_sha="abc",
        )
        assert ctx.has_useful_anchor is True
        assert ctx.anchor_kind == "spec"
        assert "specs/auth/token-refresh.md" in ctx.referenced_specs
        assert len(fetched) == 1

    @pytest.mark.asyncio
    async def test_anchor_heuristic_spec_unfetchable_falls_through(self):
        """An unfetchable spec/... path does not count as a useful anchor."""

        class FakeAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                raise FileNotFoundError("not found")

        ctx = await load_intent_anchors(
            title="Fix stuff",
            body="See specs/missing.md",
            adapter=FakeAdapter(),
            repo="org/repo",
        )
        # Title+body combined = "Fix stuff See specs/missing.md" → 27 non-ws chars,
        # well below the 80-char threshold → must classify as missing.
        assert ctx.anchor_kind == "missing"
        assert ctx.has_useful_anchor is False
        assert ctx.referenced_specs == {}

    @pytest.mark.asyncio
    async def test_anchor_heuristic_80_concrete_chars_is_useful(self):
        """At least 80 non-template characters with a concrete claim is useful."""
        body = (
            "This PR adds a new rate-limiting middleware that rejects requests "
            "exceeding 100 req/s per client IP address using Redis counters."
        )
        ctx = await load_intent_anchors(title="Add rate limiting", body=body)
        assert ctx.has_useful_anchor is True
        assert ctx.anchor_kind == "title_body"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_79_chars_not_useful(self):
        """Text exactly one char below the 80-char threshold is not useful."""
        # Combined "T " + body → after whitespace strip = 1 + 78 = 79 non-ws chars
        short_body = "x" * 78
        ctx = await load_intent_anchors(title="T", body=short_body)
        assert ctx.has_useful_anchor is False
        assert ctx.anchor_kind == "missing"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_80_chars_is_useful(self):
        """Text exactly at the 80-char threshold is useful."""
        # Combined "T " + body → after whitespace strip = 1 + 79 = 80 non-ws chars
        body = "x" * 79
        ctx = await load_intent_anchors(title="T", body=body)
        assert ctx.has_useful_anchor is True
        assert ctx.anchor_kind == "title_body"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_empty_body_is_missing(self):
        """Empty body → missing anchor."""
        ctx = await load_intent_anchors(title="fix things", body="")
        assert ctx.has_useful_anchor is False
        assert ctx.anchor_kind == "missing"
        assert "empty" in (ctx.missing_reason or "").lower()

    @pytest.mark.asyncio
    async def test_anchor_heuristic_none_body_is_missing(self):
        """None body (not yet fetched) → missing anchor."""
        ctx = await load_intent_anchors(title="fix things", body=None)
        assert ctx.has_useful_anchor is False
        assert ctx.anchor_kind == "missing"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_generic_misc_is_missing(self):
        """PR titled 'misc' with empty body → missing anchor."""
        ctx = await load_intent_anchors(title="misc", body="")
        assert ctx.has_useful_anchor is False
        assert ctx.anchor_kind == "missing"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_generic_update_is_missing(self):
        """Generic keyword 'update' as only content → missing anchor."""
        ctx = await load_intent_anchors(title="update", body="update")
        assert ctx.has_useful_anchor is False
        assert ctx.anchor_kind == "missing"

    @pytest.mark.asyncio
    async def test_anchor_heuristic_refactor_keyword_is_missing(self):
        """'refactor' alone → missing anchor."""
        ctx = await load_intent_anchors(title="refactor", body="")
        assert ctx.has_useful_anchor is False

    @pytest.mark.asyncio
    async def test_anchor_heuristic_fixes_keyword_is_missing(self):
        """'fixes' alone → missing anchor."""
        ctx = await load_intent_anchors(title="fixes", body="")
        assert ctx.has_useful_anchor is False

    @pytest.mark.asyncio
    async def test_anchor_heuristic_template_noise_stripped(self):
        """HTML comments and template markers are stripped before char count."""
        # 200+ chars of HTML comment noise but only 10 real chars
        body = "<!-- describe what your PR does here -->" * 5 + "fix thing"
        ctx = await load_intent_anchors(title="x", body=body)
        # Combined: "x fix thing" = 10 non-ws chars → below 80 → missing
        assert ctx.has_useful_anchor is False

    @pytest.mark.asyncio
    async def test_anchor_heuristic_no_adapter_no_spec_fetch(self):
        """Without an adapter, spec references are noted but not fetched."""
        ctx = await load_intent_anchors(
            title="Add feature",
            body="See specs/feature.md",
            adapter=None,
        )
        # No adapter → can't fetch spec → fall through to char check
        assert not ctx.referenced_specs


# ---------------------------------------------------------------------------
# intent_medium_high — fact-intent-medium-high-scope-opacity
# ---------------------------------------------------------------------------

class TestIntentMediumHighScopeOpacity:
    """Tests that missing anchor on medium/high PR → medium/suspected finding."""

    @pytest.mark.asyncio
    async def test_intent_medium_high_scope_opacity_emitted_when_above_size_gate(self):
        """Missing anchor on a large PR → scope-opacity finding emitted."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(
            pr=pr,
            changed_files=[f"src/file{i}.py" for i in range(6)],  # >= 5 files
            lines_changed=200,
        )
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)

        assert result.verdict == Verdict.WARN
        assert len(result.findings) == 1

        f = result.findings[0]
        assert f.severity == Severity.MEDIUM
        assert f.certainty == Certainty.SUSPECTED
        assert f.category == SCOPE_OPACITY_CATEGORY
        assert f.line is None
        assert f.quote == SCOPE_OPACITY_QUOTE
        assert f.file == ""

    @pytest.mark.asyncio
    async def test_intent_medium_high_severity_is_medium(self):
        """Scope-opacity finding is always severity=medium."""
        pr = _make_pr(title="wip", body=None)
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(10)], lines_changed=300)
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert len(result.findings) == 1
        assert result.findings[0].severity == Severity.MEDIUM

    @pytest.mark.asyncio
    async def test_intent_medium_high_certainty_is_suspected(self):
        """Scope-opacity finding is always certainty=suspected."""
        pr = _make_pr(title="fixes", body="")
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(5)], lines_changed=0)
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert len(result.findings) == 1
        assert result.findings[0].certainty == Certainty.SUSPECTED

    @pytest.mark.asyncio
    async def test_intent_medium_high_line_is_none(self):
        """Scope-opacity finding has line=None (PR-level finding)."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(5)])
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert len(result.findings) == 1
        assert result.findings[0].line is None

    @pytest.mark.asyncio
    async def test_intent_medium_high_pass_when_anchor_present(self):
        """No scope-opacity finding when a useful anchor is present."""
        long_body = (
            "This PR introduces a new distributed rate limiting mechanism using "
            "Redis sliding window counters to enforce per-tenant quotas across services."
        )
        pr = _make_pr(title="Add rate limiting", body=long_body)
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(10)], lines_changed=500)
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert result.verdict == Verdict.PASS
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_intent_medium_high_no_finding_below_size_gate(self):
        """Missing anchor on a tiny PR → pass (below size gate)."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(
            pr=pr,
            changed_files=["src/one.py"],  # 1 file < 5
            lines_changed=10,              # 10 lines < 150
        )
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert result.verdict == Verdict.PASS
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_intent_medium_high_triggered_by_lines_threshold(self):
        """Size gate fires when lines_changed >= 150 even with few files."""
        pr = _make_pr(title="update", body="")
        ctx = _make_context(pr=pr, changed_files=["src/one.py"], lines_changed=150)
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert result.verdict == Verdict.WARN
        assert len(result.findings) == 1

    @pytest.mark.asyncio
    async def test_intent_medium_high_triggered_by_files_threshold(self):
        """Size gate fires when changed_files >= 5 even with few lines."""
        pr = _make_pr(title="update", body="")
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(5)], lines_changed=5)
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert result.verdict == Verdict.WARN
        assert len(result.findings) == 1

    @pytest.mark.asyncio
    async def test_intent_medium_high_category_is_scope_opacity(self):
        """Finding category matches SCOPE_OPACITY_CATEGORY constant."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(5)])
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert len(result.findings) == 1
        assert result.findings[0].category == "scope-opacity"

    @pytest.mark.asyncio
    async def test_intent_medium_high_configurable_size_gate(self):
        """Custom size gate thresholds are respected."""
        from pr_guardian.config.schema import IntentVerificationConfig
        cfg = GuardianConfig()
        cfg.intent_verification = IntentVerificationConfig(size_gate_files=2, size_gate_lines=20)
        pr = _make_pr(title="fix", body="")
        # 3 files, 15 lines → above files gate (2), below lines gate (20)
        ctx = _make_context(pr=pr, changed_files=["src/a.py", "src/b.py", "src/c.py"], lines_changed=15)
        agent = IntentAgent(cfg)
        result = await agent.review(ctx)
        assert result.verdict == Verdict.WARN

    @pytest.mark.asyncio
    async def test_intent_medium_high_quote_is_constant(self):
        """Scope-opacity finding quote matches the module-level constant."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(pr=pr, changed_files=[f"src/f{i}.py" for i in range(5)])
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert len(result.findings) == 1
        assert result.findings[0].quote == SCOPE_OPACITY_QUOTE

    @pytest.mark.asyncio
    async def test_intent_medium_high_agent_status_is_ran(self):
        """Intent agent always reports status='ran' (never skipped)."""
        pr = _make_pr(title="misc", body="")
        ctx = _make_context(pr=pr, changed_files=["src/x.py"])
        agent = IntentAgent(GuardianConfig())
        result = await agent.review(ctx)
        assert result.status == "ran"


# ---------------------------------------------------------------------------
# no_workitem_v1 — fact-no-workitem-v1
# ---------------------------------------------------------------------------

class TestNoWorkitemV1:
    """Tests that work item and issue APIs are never called in v1."""

    @pytest.mark.asyncio
    async def test_no_workitem_github_issue_ref_not_fetched(self):
        """PR body mentioning a GitHub issue → issue API is NOT called."""
        api_calls: list[str] = []

        class TrackingAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                api_calls.append(f"file:{path}")
                raise FileNotFoundError

            async def fetch_issue(self, *args, **kwargs):
                api_calls.append("issue_api_called")
                return {}

        ctx = await load_intent_anchors(
            title="Fix regression",
            body="Fixes #123 in the auth module.",
            adapter=TrackingAdapter(),
            repo="org/repo",
        )
        assert "issue_api_called" not in api_calls

    @pytest.mark.asyncio
    async def test_no_workitem_ado_workitem_ref_not_fetched(self):
        """PR body mentioning an ADO work item (AB#NNN) → work item API NOT called."""
        api_calls: list[str] = []

        class TrackingAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                api_calls.append(f"file:{path}")
                raise FileNotFoundError

            async def fetch_work_item(self, *args, **kwargs):
                api_calls.append("ado_work_item_called")
                return {}

        ctx = await load_intent_anchors(
            title="Update user service",
            body="Implements AB#456 — adds caching layer for user profiles.",
            adapter=TrackingAdapter(),
            repo="org/repo",
        )
        assert "ado_work_item_called" not in api_calls

    @pytest.mark.asyncio
    async def test_no_workitem_only_spec_files_are_fetched(self):
        """Only specs/... paths trigger fetch_file_content calls."""
        fetched_paths: list[str] = []

        class TrackingAdapter:
            async def fetch_file_content(self, repo, path, ref="HEAD"):
                fetched_paths.append(path)
                return "# spec content" if "specs/" in path else ""

        await load_intent_anchors(
            title="Big feature",
            body="Closes #99. See specs/big-feature.md for details. AB#200.",
            adapter=TrackingAdapter(),
            repo="org/repo",
        )
        # Only specs/... path was fetched, not any issue/work item endpoint
        assert all("specs/" in p for p in fetched_paths)
        assert len(fetched_paths) == 1
        assert fetched_paths[0] == "specs/big-feature.md"

    @pytest.mark.asyncio
    async def test_no_workitem_issue_body_alone_not_anchor(self):
        """A body that only references an issue number (no 80+ chars) is not an anchor."""
        ctx = await load_intent_anchors(
            title="Fix",
            body="Closes #42",
        )
        # "Fix Closes #42" is short and issue is not fetched → missing anchor
        assert ctx.has_useful_anchor is False

    @pytest.mark.asyncio
    async def test_no_workitem_no_adapter_no_network_calls(self):
        """Without adapter, no network calls are made for any reference type."""
        ctx = await load_intent_anchors(
            title="Add feature",
            body="Implements AB#100. See specs/feature.md. Closes #200.",
            adapter=None,
        )
        # No adapter → no network → specs not fetched → falls through to char check
        assert not ctx.referenced_specs
