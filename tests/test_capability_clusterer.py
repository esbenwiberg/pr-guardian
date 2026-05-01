"""Tests for the LLM-driven capability clusterer (Phase 3a).

Mocks the LLMClient so the suite stays in-process and offline.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pr_guardian.llm.protocol import LLMResponse
from pr_guardian.wizard.capability_clusterer import (
    LAYER_VOCAB,
    SOFT_CAP_CAPABILITIES,
    _MAX_PATCH_CHARS,
    _MAX_PATCH_PER_FILE,
    _build_user_prompt,
    Capability,
    FileSummary,
    FindingSummary,
    cluster_capabilities,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _f(path: str, role: str = "PRODUCTION", locs: int = 30, finding_count: int = 0) -> FileSummary:
    return FileSummary(path=path, role=role, locs=locs, finding_count=finding_count)


def _finding(file: str, severity: str = "high", category: str = "x") -> FindingSummary:
    return FindingSummary(file=file, severity=severity, category=category)


def _mock_llm(content: str, *, model: str = "claude-sonnet", in_tok: int = 100, out_tok: int = 50) -> AsyncMock:
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResponse(
        content=content, model=model, input_tokens=in_tok, output_tokens=out_tok,
    ))
    return llm


# ---------------------------------------------------------------------------
# Fallback paths — these never call the LLM.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_files_returns_empty_no_files_fallback():
    llm = _mock_llm("")
    result = await cluster_capabilities(
        files=[], findings=[], pr_title="x", pr_body="", llm_client=llm,
    )
    assert result.source == "fallback_no_files"
    assert result.capabilities == []
    llm.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_small_pr_still_calls_llm():
    """LLM is called for all PRs with files, regardless of finding count.
    Even a PR with only 1 high-severity finding gets a proper AI briefing."""
    files = [_f("a.py"), _f("b.py")]
    findings = [_finding("a.py", "high")]  # only 1 surfaced — LLM still called

    raw = json.dumps({"capabilities": [
        {"name": "Core changes", "intent": "Single capability.", "files": ["a.py", "b.py"], "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)
    result = await cluster_capabilities(
        files=files, findings=findings, pr_title="x", pr_body="", llm_client=llm,
    )

    assert result.source == "llm"
    assert len(result.capabilities) == 1
    llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_only_findings_still_calls_llm():
    """All-low-severity findings no longer suppress the LLM call — every PR
    with touched files gets an AI-generated briefing and capability grouping."""
    files = [_f("a.py"), _f("b.py")]
    findings = [_finding("a.py", "low"), _finding("b.py", "low")]

    raw = json.dumps({"capabilities": [
        {"name": "Minor tweaks", "intent": "Low-severity fixes.", "files": ["a.py", "b.py"], "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)
    result = await cluster_capabilities(
        files=files, findings=findings, pr_title="x", pr_body="", llm_client=llm,
    )
    assert result.source == "llm"
    llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_error_falls_back_to_single_capability():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("network down"))

    result = await cluster_capabilities(
        files=files, findings=findings, pr_title="x", pr_body="", llm_client=llm,
    )
    assert result.source == "fallback_error"
    assert "network down" in result.error
    assert len(result.capabilities) == 1
    assert set(result.capabilities[0].files) == {"a.py", "b.py", "c.py"}


# ---------------------------------------------------------------------------
# Happy path — LLM returns valid JSON.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_llm_response_returns_capabilities_and_propagates_tokens():
    files = [_f("svc.py"), _f("model.py"), _f("test_svc.py", role="TEST"), _f("api.py")]
    findings = [_finding("svc.py"), _finding("api.py")]

    response = json.dumps({
        "capabilities": [
            {"name": "Graph integration", "intent": "Adds the typed Graph client.",
             "files": ["svc.py", "model.py"], "layers": ["Services", "Models"]},
            {"name": "Endpoints", "intent": "Wires the new client into the API surface.",
             "files": ["api.py"], "layers": ["Endpoints"]},
            {"name": "Tests", "intent": "Happy-path coverage for the new client.",
             "files": ["test_svc.py"], "layers": ["Tests"]},
        ]
    })
    llm = _mock_llm(response, in_tok=234, out_tok=89)

    result = await cluster_capabilities(
        files=files, findings=findings, pr_title="Add Graph integration", pr_body="",
        llm_client=llm,
    )
    assert result.source == "llm"
    assert len(result.capabilities) == 3
    assert result.capabilities[0].name == "Graph integration"
    assert result.capabilities[0].layers == ("Services", "Models")
    assert result.input_tokens == 234
    assert result.output_tokens == 89
    assert result.model == "claude-sonnet"


@pytest.mark.asyncio
async def test_response_wrapped_in_markdown_fence_is_still_parsed():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = "```json\n" + json.dumps({"capabilities": [
        {"name": "All", "intent": "everything", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]}
    ]}) + "\n```"
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert len(result.capabilities) == 1


# ---------------------------------------------------------------------------
# Validation — soft enforcement.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_layers_are_dropped_not_rejected():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = json.dumps({"capabilities": [
        {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py"],
         "layers": ["Services", "MyMadeUpLayer", "Tests"]},
    ]})
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert result.capabilities[0].layers == ("Services", "Tests")  # invalid layer dropped


@pytest.mark.asyncio
async def test_unknown_file_paths_in_response_are_dropped():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = json.dumps({"capabilities": [
        {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py", "phantom.py"],
         "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert "phantom.py" not in result.capabilities[0].files


@pytest.mark.asyncio
async def test_capabilities_exceeding_soft_cap_are_truncated():
    files = [_f(f"f{i}.py") for i in range(10)]
    findings = [_finding("f0.py"), _finding("f1.py")]

    # 8 capabilities returned; soft cap defaults to 6, so 2 get truncated and
    # those files end up unassigned, triggering parse-error fallback.
    raw = json.dumps({"capabilities": [
        {"name": f"Cap{i}", "intent": "i", "files": [f"f{i}.py"], "layers": ["Services"]}
        for i in range(8)
    ]})
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    # Truncation produced unassigned files → parse error → fallback to single cap.
    assert result.source == "fallback_error"
    assert "not assigned" in result.error


@pytest.mark.asyncio
async def test_unassigned_files_trigger_fallback():
    """If the LLM omits some files, fall back rather than show a partial view."""
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = json.dumps({"capabilities": [
        {"name": "Cap", "intent": "i", "files": ["a.py", "b.py"], "layers": ["Services"]},
        # c.py omitted
    ]})
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    assert result.source == "fallback_error"


@pytest.mark.asyncio
async def test_each_file_appears_in_exactly_one_capability():
    """If the LLM dupes a file across capabilities, the second occurrence is dropped."""
    files = [_f("a.py"), _f("b.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = json.dumps({"capabilities": [
        {"name": "First", "intent": "i", "files": ["a.py", "b.py"], "layers": ["Services"]},
        {"name": "Second", "intent": "i", "files": ["a.py"], "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)

    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    # Second cap empties out (its only file already claimed) → cap dropped.
    assert result.source == "llm"
    assert len(result.capabilities) == 1
    assert set(result.capabilities[0].files) == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_malformed_json_falls_back_with_error_recorded():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    llm = _mock_llm("this is not json at all")
    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="x", pr_body="", llm_client=llm)
    assert result.source == "fallback_error"
    assert result.error.startswith("parse:")
    # Token usage is still propagated even on parse failure (the call did happen).
    assert result.input_tokens == 100


# ---------------------------------------------------------------------------
# Prompt construction.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_includes_layer_vocab_pr_title_and_findings():
    files = [_f("svc.py", finding_count=2), _f("model.py")]
    findings = [_finding("svc.py", "high", "auth"), _finding("svc.py", "medium", "retry")]

    raw = json.dumps({"capabilities": [
        {"name": "X", "intent": "y", "files": ["svc.py", "model.py"], "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)

    await cluster_capabilities(
        files=files, findings=findings,
        pr_title="Graph integration",
        pr_body="Some body text",
        llm_client=llm,
    )

    system_arg = llm.complete.await_args.kwargs["system"]
    user_arg = llm.complete.await_args.kwargs["user"]
    for layer in LAYER_VOCAB:
        assert layer in system_arg
    assert "Graph integration" in user_arg
    assert "Some body text" in user_arg
    assert "svc.py" in user_arg
    assert "high:auth" in user_arg


@pytest.mark.asyncio
async def test_response_format_json_is_requested():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]

    raw = json.dumps({"capabilities": [
        {"name": "X", "intent": "y", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
    ]})
    llm = _mock_llm(raw)

    await cluster_capabilities(files=files, findings=findings,
                               pr_title="x", pr_body="", llm_client=llm)
    assert llm.complete.await_args.kwargs["response_format"] == "json"


# ---------------------------------------------------------------------------
# Scaffold contract pins — these tests document the locked design decisions.
# ---------------------------------------------------------------------------


def test_layer_vocab_is_the_locked_closed_set():
    assert LAYER_VOCAB == (
        "Models", "Services", "Endpoints", "Validation",
        "Infra", "Tests", "Config", "Docs",
    )


def test_soft_cap_defaults_to_six():
    assert SOFT_CAP_CAPABILITIES == 6


# ---------------------------------------------------------------------------
# Briefing extraction (Phase 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_is_parsed_when_well_formed():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]
    raw = json.dumps({
        "capabilities": [
            {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
        ],
        "briefing": {
            "what": "Adds the typed Graph client.",
            "why":  "Consolidates duplicated retry + auth from legacy plugins.",
            "how":  "Layered shape: Infra registers, Services wraps, Tests cover.",
        },
    })
    llm = _mock_llm(raw)
    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="Graph", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert result.briefing == {
        "what": "Adds the typed Graph client.",
        "why":  "Consolidates duplicated retry + auth from legacy plugins.",
        "how":  "Layered shape: Infra registers, Services wraps, Tests cover.",
    }


@pytest.mark.asyncio
async def test_briefing_missing_returns_none_not_error():
    """Briefing is best-effort. A response with valid capabilities but no
    briefing block must still succeed — wizard falls back to its heuristic
    stub for the W/W/H block."""
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]
    raw = json.dumps({
        "capabilities": [
            {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
        ],
    })
    llm = _mock_llm(raw)
    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="Graph", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert result.briefing is None


@pytest.mark.asyncio
@pytest.mark.parametrize("briefing", [
    {"what": "x", "why": "y"},                      # missing how
    {"what": "x", "why": "y", "how": ""},           # empty how
    {"what": "  ", "why": "y", "how": "z"},         # whitespace what
    {"what": "x", "why": "y", "how": 12345},        # non-string how
    "not even a dict",                              # wrong shape
])
async def test_partial_or_malformed_briefing_returns_none(briefing):
    """A partial briefing is worse than none — we don't show 'What' without
    'Why' and 'How', because the heuristic stub at least produces all three."""
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]
    raw = json.dumps({
        "capabilities": [
            {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
        ],
        "briefing": briefing,
    })
    llm = _mock_llm(raw)
    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="Graph", pr_body="", llm_client=llm)
    assert result.source == "llm"
    assert result.briefing is None


@pytest.mark.asyncio
async def test_briefing_strings_are_trimmed():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]
    raw = json.dumps({
        "capabilities": [
            {"name": "Cap", "intent": "i", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
        ],
        "briefing": {"what": "  what.  \n", "why": "why.", "how": "\nhow."},
    })
    llm = _mock_llm(raw)
    result = await cluster_capabilities(files=files, findings=findings,
                                        pr_title="Graph", pr_body="", llm_client=llm)
    assert result.briefing == {"what": "what.", "why": "why.", "how": "how."}


@pytest.mark.asyncio
async def test_fallback_no_files_has_no_briefing():
    """The only remaining fallback path (no files) returns briefing=None."""
    llm = _mock_llm("")
    result = await cluster_capabilities(
        files=[], findings=[], pr_title="x", pr_body="", llm_client=llm,
    )
    assert result.source == "fallback_no_files"
    assert result.briefing is None


@pytest.mark.asyncio
async def test_prompt_asks_for_briefing_with_what_why_how():
    files = [_f("a.py"), _f("b.py"), _f("c.py")]
    findings = [_finding("a.py"), _finding("b.py")]
    raw = json.dumps({
        "capabilities": [
            {"name": "X", "intent": "y", "files": ["a.py", "b.py", "c.py"], "layers": ["Services"]},
        ],
        "briefing": {"what": "w", "why": "y", "how": "h"},
    })
    llm = _mock_llm(raw)
    await cluster_capabilities(files=files, findings=findings,
                               pr_title="Graph", pr_body="", llm_client=llm)
    system = llm.complete.await_args.kwargs["system"]
    assert "briefing" in system.lower()
    assert "what" in system.lower() and "why" in system.lower() and "how" in system.lower()


# ---------------------------------------------------------------------------
# _build_user_prompt — patch-budget and per-file truncation logic
# ---------------------------------------------------------------------------


def test_file_patches_appear_in_prompt():
    files = [_f("api.py"), _f("models.py")]
    prompt = _build_user_prompt(
        files, [], "My PR", "",
        file_patches={"api.py": "+def foo():\n+    pass", "models.py": "+class Bar: ..."},
    )
    assert "FILE DIFFS (excerpts):" in prompt
    assert "--- api.py ---" in prompt
    assert "+def foo():" in prompt


def test_patch_truncated_per_file_and_marker_shown():
    # Unique sentinel after the cut-off — must not appear if truncation works.
    overrun_sentinel = "OVERRUN_SENTINEL_UNIQUE_XYZ"
    long_patch = "+" + "a" * _MAX_PATCH_PER_FILE + overrun_sentinel
    files = [_f("big.py")]
    prompt = _build_user_prompt(files, [], "title", "", file_patches={"big.py": long_patch})
    assert "... (truncated)" in prompt
    assert overrun_sentinel not in prompt


def test_no_truncation_marker_when_patch_fits():
    short_patch = "+" + "x" * (_MAX_PATCH_PER_FILE - 10)
    files = [_f("small.py")]
    prompt = _build_user_prompt(files, [], "title", "", file_patches={"small.py": short_patch})
    assert "... (truncated)" not in prompt


def test_patch_budget_stops_including_files_after_limit():
    """Once _MAX_PATCH_CHARS is exhausted the remaining files produce no diff section."""
    # Each file fills exactly one per-file budget slot; create enough to exceed total budget.
    files = [_f(f"file{i}.py") for i in range(20)]
    full_patch = "+" + "a" * _MAX_PATCH_PER_FILE
    file_patches = {f"file{i}.py": full_patch for i in range(20)}
    prompt = _build_user_prompt(files, [], "title", "", file_patches=file_patches)
    file_headers = [line for line in prompt.split("\n") if line.startswith("--- file")]
    max_files = _MAX_PATCH_CHARS // _MAX_PATCH_PER_FILE
    assert len(file_headers) <= max_files + 1  # +1 for rounding edge


def test_file_order_in_diff_follows_files_list_not_dict_order():
    """Patches appear in the order of the files list, regardless of dict insertion order."""
    files = [_f("z.py"), _f("a.py"), _f("m.py")]
    file_patches = {"a.py": "+a", "m.py": "+m", "z.py": "+z"}
    prompt = _build_user_prompt(files, [], "title", "", file_patches=file_patches)
    z_pos = prompt.index("--- z.py ---")
    a_pos = prompt.index("--- a.py ---")
    m_pos = prompt.index("--- m.py ---")
    assert z_pos < a_pos < m_pos


def test_files_not_in_patches_produce_no_diff_header():
    files = [_f("a.py"), _f("b.py")]
    prompt = _build_user_prompt(files, [], "title", "", file_patches={"a.py": "+x"})
    assert "--- a.py ---" in prompt
    assert "--- b.py ---" not in prompt


def test_empty_patches_dict_produces_no_diff_section():
    files = [_f("a.py")]
    prompt = _build_user_prompt(files, [], "title", "", file_patches={})
    assert "FILE DIFFS" not in prompt


def test_none_patches_produces_no_diff_section():
    files = [_f("a.py")]
    prompt = _build_user_prompt(files, [], "title", "", file_patches=None)
    assert "FILE DIFFS" not in prompt


def test_commit_messages_appear_in_prompt():
    files = [_f("a.py")]
    prompt = _build_user_prompt(
        files, [], "My PR", "",
        commit_messages=["feat: first commit", "fix: second commit"],
    )
    assert "COMMIT MESSAGES (2):" in prompt
    assert "- feat: first commit" in prompt
    assert "- fix: second commit" in prompt


def test_no_commit_messages_no_section():
    files = [_f("a.py")]
    prompt = _build_user_prompt(files, [], "title", "", commit_messages=[])
    assert "COMMIT MESSAGES" not in prompt
