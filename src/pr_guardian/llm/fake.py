from __future__ import annotations

import json

from pr_guardian.llm.protocol import LLMResponse

# Marker that E2E test diffs embed to trigger a deterministic finding.
# Any diff line containing this string causes the fake provider to return
# one stable finding instead of a clean pass.
E2E_FINDING_MARKER = "GUARDIAN_E2E_FINDING"

_PASS_RESPONSE = json.dumps(
    {
        "verdict": "pass",
        "verdict_explanation": None,
        "languages_reviewed": ["python"],
        "findings": [],
        "cross_language_findings": [],
    }
)

_FINDING_RESPONSE = json.dumps(
    {
        "verdict": "warn",
        "verdict_explanation": (
            "E2E fixture marker detected. This is a deterministic test finding."
        ),
        "languages_reviewed": ["python"],
        "findings": [
            {
                "severity": "low",
                "certainty": "detected",
                "category": "e2e-fixture",
                "language": "python",
                "file": "e2e_fixture.py",
                "line": 1,
                "description": (
                    f"Deterministic E2E finding triggered by {E2E_FINDING_MARKER} marker."
                ),
                "suggestion": "Remove the marker before merging to production.",
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
        ],
        "cross_language_findings": [],
    }
)

_RE_EVALUATE_KEPT_RESPONSE = json.dumps(
    {
        "evaluations": [
            {
                "finding_index": 1,
                "status": "kept",
                "reason": "Deterministic E2E re-evaluation: marker still present.",
                "updated_severity": None,
                "updated_description": None,
            }
        ]
    }
)

_RE_EVALUATE_PASS_RESPONSE = json.dumps({"evaluations": []})


class FakeLLMClient:
    """Deterministic fake LLM client for E2E and local dev harness.

    Returns stable JSON based on whether the user message contains
    E2E_FINDING_MARKER. Never makes any network calls.

    Only activated when a provider with type="fake" is configured —
    never used in production paths.
    """

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
        has_marker = E2E_FINDING_MARKER in user

        # Re-evaluate mode detection: the system prompt contains "RE-EVALUATION MODE"
        if "RE-EVALUATION MODE" in system:
            content = _RE_EVALUATE_KEPT_RESPONSE if has_marker else _RE_EVALUATE_PASS_RESPONSE
        else:
            content = _FINDING_RESPONSE if has_marker else _PASS_RESPONSE

        return LLMResponse(
            content=content,
            model=model or "fake-deterministic-v1",
            input_tokens=0,
            output_tokens=0,
        )
