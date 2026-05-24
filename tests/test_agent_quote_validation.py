"""Tests for quote-grounded finding validation (Brief 02).

Covers:
- quote_added_line: valid quotes kept; missing/mismatched/context/deleted rejected
- pr_level_intent_exception: scope-opacity with line=null is the only null-line exception
"""
from __future__ import annotations

import json

import pytest

from pr_guardian.agents.base import BaseAgent, SCOPE_OPACITY_CATEGORY
from pr_guardian.models.findings import Certainty, Finding, Severity


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        severity=Severity.MEDIUM,
        certainty=Certainty.DETECTED,
        category="Test",
        language="python",
        file="src/app.py",
        line=10,
        description="A test finding.",
        quote="return user.is_admin or allow_all",
    )
    defaults.update(overrides)
    return Finding(**defaults)


PATCH_WITH_ADDED_LINE = """\
@@ -1,3 +1,4 @@
 def check(user):
+    return user.is_admin or allow_all
-    return False
 pass
"""

DIFF_MAP: dict[str, str] = {"src/app.py": PATCH_WITH_ADDED_LINE}


# ---------------------------------------------------------------------------
# quote_added_line — _is_valid_finding unit tests
# ---------------------------------------------------------------------------

class TestQuoteAddedLine:
    def test_quote_added_line_kept(self):
        """A finding whose quote exactly matches an added line passes."""
        finding = _make_finding(quote="return user.is_admin or allow_all")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is True

    def test_quote_added_line_missing_dropped(self):
        """A finding with an empty quote fails the contract."""
        finding = _make_finding(quote="")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_mismatch_dropped(self):
        """A finding whose quote does not match any added line is dropped."""
        finding = _make_finding(quote="something completely different")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_context_line_rejected(self):
        """A quote from a context line (space-prefixed) is rejected."""
        finding = _make_finding(quote="def check(user):")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_deleted_line_rejected(self):
        """A quote from a deleted line (- prefix) is rejected."""
        finding = _make_finding(quote="return False")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_file_not_in_diff_dropped(self):
        """A finding whose file has no entry in diff_map is dropped."""
        finding = _make_finding(
            file="src/other.py",
            quote="return user.is_admin or allow_all",
        )
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_null_line_normal_category_dropped(self):
        """A non-scope-opacity finding with line=None fails even with a valid-looking quote."""
        finding = _make_finding(line=None, quote="return user.is_admin or allow_all")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_quote_added_line_whitespace_stripped(self):
        """Quote matching strips surrounding whitespace on both sides."""
        finding = _make_finding(quote="  return user.is_admin or allow_all  ")
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is True

    def test_quote_added_line_empty_patch_drops_finding(self):
        """A file in diff_map with an empty patch has no added lines; finding is dropped."""
        diff_map_empty_patch = {"src/app.py": ""}
        finding = _make_finding(quote="return user.is_admin or allow_all")
        assert BaseAgent._is_valid_finding(finding, diff_map_empty_patch) is False

    def test_extract_added_lines_excludes_file_header(self):
        """_extract_added_lines ignores the +++ file-header line."""
        patch = "+++ b/src/app.py\n+    return True\n"
        added = BaseAgent._extract_added_lines(patch)
        assert "return True" in added
        assert "+++ b/src/app.py" not in added
        assert "b/src/app.py" not in added


# ---------------------------------------------------------------------------
# quote_added_line — _parse_response integration
# ---------------------------------------------------------------------------

class TestParseResponseQuoteFiltering:
    """_parse_response with diff_map drops ungrounded findings."""

    def _build_raw(self, findings: list[dict]) -> str:
        return json.dumps({
            "verdict": "warn",
            "verdict_explanation": "found issues",
            "languages_reviewed": ["python"],
            "findings": findings,
            "cross_language_findings": [],
        })

    def _agent(self) -> BaseAgent:
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "security_privacy"
        return agent

    def test_quote_added_line_valid_finding_kept_by_parser(self):
        """Parser keeps a finding whose quote matches an added line."""
        raw = self._build_raw([{
            "severity": "medium",
            "certainty": "detected",
            "category": "Auth",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "return user.is_admin or allow_all",
            "description": "Unsafe allow-all path.",
            "suggestion": "Remove allow_all.",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.findings) == 1
        assert result.findings[0].quote == "return user.is_admin or allow_all"

    def test_quote_added_line_ungrounded_finding_dropped_by_parser(self):
        """Parser drops a finding with empty quote when diff_map is provided."""
        raw = self._build_raw([{
            "severity": "high",
            "certainty": "detected",
            "category": "Bug",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "",
            "description": "Missing quote.",
            "suggestion": "",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.findings) == 0

    def test_quote_added_line_mismatched_quote_dropped_by_parser(self):
        """Parser drops a finding whose quote doesn't match any added diff line."""
        raw = self._build_raw([{
            "severity": "high",
            "certainty": "detected",
            "category": "Bug",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "this line is not in the diff",
            "description": "Mismatched quote.",
            "suggestion": "",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.findings) == 0

    def test_quote_added_line_no_diff_map_skips_validation(self):
        """Without diff_map, parser keeps all findings (backward compat)."""
        raw = self._build_raw([{
            "severity": "high",
            "certainty": "detected",
            "category": "Bug",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "",
            "description": "No quote but no diff_map.",
            "suggestion": "",
        }])
        # No diff_map — validation skipped
        result = self._agent()._parse_response(raw, ["python"])
        assert len(result.findings) == 1

    def test_quote_added_line_mixed_valid_invalid_only_valid_kept(self):
        """When some findings pass and some fail, only valid ones are returned."""
        raw = self._build_raw([
            {
                "severity": "high",
                "certainty": "detected",
                "category": "Auth",
                "language": "python",
                "file": "src/app.py",
                "line": 10,
                "quote": "return user.is_admin or allow_all",
                "description": "Valid.",
                "suggestion": "",
            },
            {
                "severity": "medium",
                "certainty": "suspected",
                "category": "Style",
                "language": "python",
                "file": "src/app.py",
                "line": 5,
                "quote": "not a real line",
                "description": "Invalid — quote mismatch.",
                "suggestion": "",
            },
        ])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.findings) == 1
        assert result.findings[0].category == "Auth"


# ---------------------------------------------------------------------------
# pr_level_intent_exception — scope-opacity with line=null
# ---------------------------------------------------------------------------

class TestPrLevelIntentException:
    def test_pr_level_intent_exception_kept(self):
        """scope-opacity finding with line=null and non-empty quote is valid."""
        finding = _make_finding(
            category=SCOPE_OPACITY_CATEGORY,
            line=None,
            quote="PR title/body lacks a useful intent anchor",
            file="",
        )
        assert BaseAgent._is_valid_finding(finding, {}) is True

    def test_pr_level_intent_exception_empty_quote_rejected(self):
        """scope-opacity finding with empty quote is rejected."""
        finding = _make_finding(
            category=SCOPE_OPACITY_CATEGORY,
            line=None,
            quote="",
            file="",
        )
        assert BaseAgent._is_valid_finding(finding, {}) is False

    def test_pr_level_intent_exception_other_null_line_rejected(self):
        """A non-scope-opacity finding with line=null is rejected even with a quote."""
        finding = _make_finding(
            category="Auth",
            line=None,
            quote="return user.is_admin or allow_all",
        )
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False

    def test_pr_level_intent_exception_does_not_require_diff_match(self):
        """scope-opacity exception needs only a non-empty quote; no diff matching."""
        finding = _make_finding(
            category=SCOPE_OPACITY_CATEGORY,
            line=None,
            quote="PR title/body lacks a useful intent anchor",
            file="",
        )
        assert BaseAgent._is_valid_finding(finding, {}) is True

    def test_pr_level_intent_exception_exact_category_string(self):
        """Only the exact string 'scope-opacity' triggers the exception."""
        assert SCOPE_OPACITY_CATEGORY == "scope-opacity"
        non_matching = _make_finding(
            category="scope_opacity",  # underscore — not the same
            line=None,
            quote="PR title/body lacks a useful intent anchor",
            file="",
        )
        assert BaseAgent._is_valid_finding(non_matching, {}) is False

    def test_pr_level_intent_exception_kept_by_parser(self):
        """Parser keeps a scope-opacity finding with line=null even with a diff_map."""
        raw = json.dumps({
            "verdict": "warn",
            "verdict_explanation": "scope opacity",
            "languages_reviewed": [],
            "findings": [{
                "severity": "medium",
                "certainty": "suspected",
                "category": SCOPE_OPACITY_CATEGORY,
                "language": "",
                "file": "",
                "line": None,
                "quote": "PR title/body lacks a useful intent anchor",
                "description": "PR has no useful intent anchor.",
                "suggestion": "Add a clear description.",
            }],
            "cross_language_findings": [],
        })
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "intent"
        result = agent._parse_response(raw, [], diff_map=DIFF_MAP)
        assert len(result.findings) == 1
        assert result.findings[0].category == SCOPE_OPACITY_CATEGORY
        assert result.findings[0].line is None
        assert result.findings[0].quote == "PR title/body lacks a useful intent anchor"

    def test_pr_level_intent_exception_scope_opacity_with_line_set_uses_normal_path(self):
        """If category=scope-opacity but line is set, normal diff-match rules apply."""
        finding = _make_finding(
            category=SCOPE_OPACITY_CATEGORY,
            line=10,
            quote="return user.is_admin or allow_all",
            file="src/app.py",
        )
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is True

    def test_pr_level_intent_exception_scope_opacity_with_line_set_bad_quote_dropped(self):
        """scope-opacity + line set + mismatched quote → dropped by normal rules."""
        finding = _make_finding(
            category=SCOPE_OPACITY_CATEGORY,
            line=10,
            quote="not in the diff",
            file="src/app.py",
        )
        assert BaseAgent._is_valid_finding(finding, DIFF_MAP) is False


# ---------------------------------------------------------------------------
# quote_added_line — cross_language_findings filtering parallels findings
# ---------------------------------------------------------------------------

class TestParseResponseCrossLanguageQuoteFiltering:
    """cross_language_findings get the same quote validation as findings."""

    def _agent(self) -> BaseAgent:
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "security_privacy"
        return agent

    def _raw_with_cross(self, cross: list[dict]) -> str:
        return json.dumps({
            "verdict": "warn",
            "verdict_explanation": "cross-lang issues",
            "languages_reviewed": ["python"],
            "findings": [],
            "cross_language_findings": cross,
        })

    def test_quote_added_line_valid_cross_language_finding_kept(self):
        """A valid-quote cross_language finding is kept by the parser."""
        raw = self._raw_with_cross([{
            "severity": "high",
            "certainty": "detected",
            "category": "API contract drift",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "return user.is_admin or allow_all",
            "description": "Backend change breaks frontend contract.",
            "suggestion": "Coordinate the API field rename.",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.cross_language_findings) == 1
        assert result.cross_language_findings[0].category == "API contract drift"

    def test_quote_added_line_mismatched_cross_language_finding_dropped(self):
        """A mismatched-quote cross_language finding is dropped."""
        raw = self._raw_with_cross([{
            "severity": "high",
            "certainty": "detected",
            "category": "API drift",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "this line is not in any diff",
            "description": "Drift.",
            "suggestion": "",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.cross_language_findings) == 0

    def test_quote_added_line_empty_quote_cross_language_finding_dropped(self):
        """A cross_language finding with an empty quote is dropped."""
        raw = self._raw_with_cross([{
            "severity": "high",
            "certainty": "detected",
            "category": "API drift",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "",
            "description": "No quote provided.",
            "suggestion": "",
        }])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.cross_language_findings) == 0

    def test_quote_added_line_no_diff_map_keeps_cross_language_finding(self):
        """Without diff_map, cross_language findings are not validated either."""
        raw = self._raw_with_cross([{
            "severity": "high",
            "certainty": "detected",
            "category": "API drift",
            "language": "python",
            "file": "src/app.py",
            "line": 10,
            "quote": "",
            "description": "No quote but no diff_map.",
            "suggestion": "",
        }])
        result = self._agent()._parse_response(raw, ["python"])  # no diff_map
        assert len(result.cross_language_findings) == 1

    def test_quote_added_line_mixed_valid_invalid_cross_language(self):
        """Valid cross_language findings are kept; invalid ones dropped, in mixed input."""
        raw = self._raw_with_cross([
            {
                "severity": "high",
                "certainty": "detected",
                "category": "API drift kept",
                "language": "python",
                "file": "src/app.py",
                "line": 10,
                "quote": "return user.is_admin or allow_all",
                "description": "Valid.",
                "suggestion": "",
            },
            {
                "severity": "medium",
                "certainty": "suspected",
                "category": "API drift dropped",
                "language": "python",
                "file": "src/app.py",
                "line": 10,
                "quote": "not in the diff",
                "description": "Invalid.",
                "suggestion": "",
            },
        ])
        result = self._agent()._parse_response(raw, ["python"], diff_map=DIFF_MAP)
        assert len(result.cross_language_findings) == 1
        assert result.cross_language_findings[0].category == "API drift kept"


class TestPrLevelIntentExceptionCrossLanguage:
    """The scope-opacity exception is honored on the cross_language path too."""

    def test_pr_level_intent_exception_via_cross_language_findings_kept(self):
        """A scope-opacity cross_language finding with line=None is kept."""
        raw = json.dumps({
            "verdict": "warn",
            "verdict_explanation": "scope opacity via cross-lang",
            "languages_reviewed": [],
            "findings": [],
            "cross_language_findings": [{
                "severity": "medium",
                "certainty": "suspected",
                "category": SCOPE_OPACITY_CATEGORY,
                "language": "",
                "file": "",
                "line": None,
                "quote": "PR title/body lacks a useful intent anchor",
                "description": "No anchor.",
                "suggestion": "Add a description.",
            }],
        })
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "intent"
        result = agent._parse_response(raw, [], diff_map=DIFF_MAP)
        assert len(result.cross_language_findings) == 1
        assert result.cross_language_findings[0].category == SCOPE_OPACITY_CATEGORY
        assert result.cross_language_findings[0].line is None
