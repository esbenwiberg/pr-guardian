"""LLM-driven capability clustering for the wizard view (Phase 3a).

Replaces the path-prefix heuristic with agent-decided capability shape
inside a locked scaffold:

- Closed layer vocabulary (Models / Services / Endpoints / Validation /
  Infra / Tests / Config / Docs).
- Soft cap on capability count (default 6).
- Fallback to a single-capability "All changes" when the surfaced-findings
  count is too low for clustering to add value, when the LLM call fails,
  or when the LLM returns a malformed response.

This module provides an `async cluster_capabilities(...)` entry point.
3b wires it into the wizard's data path; 3a only ships the module + tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from pr_guardian.llm.protocol import LLMClient

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Scaffold constants — locked design decisions from the prototype phase.
# ---------------------------------------------------------------------------

LAYER_VOCAB: tuple[str, ...] = (
    "Models",
    "Services",
    "Endpoints",
    "Validation",
    "Infra",
    "Tests",
    "Config",
    "Docs",
)
LAYER_VOCAB_SET = frozenset(LAYER_VOCAB)

SOFT_CAP_CAPABILITIES = 6
SMALL_PR_THRESHOLD = 2  # surfaced findings below this → single-capability fallback


# ---------------------------------------------------------------------------
# Public types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSummary:
    """Per-file context fed to the LLM."""
    path: str
    role: str
    locs: int
    finding_count: int


@dataclass(frozen=True)
class FindingSummary:
    """Per-finding context fed to the LLM (just enough to spot risk hot-spots)."""
    file: str
    severity: str
    category: str


@dataclass(frozen=True)
class Capability:
    name: str
    intent: str
    files: tuple[str, ...]
    layers: tuple[str, ...]


@dataclass
class ClusterResult:
    """Outcome of clustering. `source` makes the fallback path observable."""
    capabilities: list[Capability]
    source: str  # "llm" | "fallback_small_pr" | "fallback_no_files" | "fallback_error"
    briefing: dict[str, str] | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    raw_response: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


async def cluster_capabilities(
    files: list[FileSummary],
    findings: list[FindingSummary],
    pr_title: str,
    pr_body: str,
    *,
    llm_client: LLMClient,
    model: str | None = None,
    soft_cap: int = SOFT_CAP_CAPABILITIES,
) -> ClusterResult:
    """Cluster the given files into capabilities. Falls back to a single
    capability when the PR is too small to benefit from clustering, when no
    files were touched, or when the LLM call fails / returns garbage."""
    if not files:
        return ClusterResult(capabilities=[], source="fallback_no_files")

    surfaced = sum(1 for f in findings if f.severity.lower() in ("high", "critical", "medium"))
    if surfaced < SMALL_PR_THRESHOLD:
        return ClusterResult(
            capabilities=[_single_capability(files, name="All changes",
                                             intent="Whole-PR view; not enough surfaced findings to warrant clustering.")],
            source="fallback_small_pr",
        )

    system = _build_system_prompt(soft_cap)
    user = _build_user_prompt(files, findings, pr_title, pr_body)

    try:
        response = await llm_client.complete(
            system=system, user=user, model=model,
            max_tokens=6144, temperature=0.1, response_format="json",
        )
    except Exception as exc:  # noqa: BLE001 — the goal is graceful fallback
        log.warning("capability_clusterer_llm_call_failed", error=str(exc))
        return ClusterResult(
            capabilities=[_single_capability(files)],
            source="fallback_error",
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        capabilities, briefing = _parse_and_validate(response.content, files=files, soft_cap=soft_cap)
    except _ParseError as exc:
        log.warning("capability_clusterer_parse_failed", error=str(exc), raw=response.content[:500])
        return ClusterResult(
            capabilities=[_single_capability(files)],
            source="fallback_error",
            error=f"parse: {exc}",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            raw_response=response.content,
        )

    return ClusterResult(
        capabilities=capabilities,
        briefing=briefing,
        source="llm",
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        raw_response=response.content,
    )


# ---------------------------------------------------------------------------
# Prompt assembly.
# ---------------------------------------------------------------------------


def _build_system_prompt(soft_cap: int) -> str:
    layers = ", ".join(LAYER_VOCAB)
    return (
        "You are preparing a pull-request review briefing for a human reviewer. "
        "You will (a) cluster the changed files into capabilities and "
        "(b) write a short opening briefing that orients the reviewer.\n\n"
        f"CLUSTERING RULES:\n"
        f"- Output between 1 and {soft_cap} capabilities. Fewer is better.\n"
        "- Every input file must appear in exactly one capability.\n"
        "- Capability names should reveal *what the change does*, not where the "
        "files live (e.g. \"Microsoft Graph integration\" not \"Infrastructure/Graph\").\n"
        "- A capability's `intent` is one or two sentences explaining what the "
        "capability delivers and the role it plays in the PR.\n"
        f"- A capability's `layers` is a subset of this fixed vocabulary: {layers}. "
        "Use only these names; do not invent new ones.\n"
        "- If two files belong together logically (e.g. a service and the test "
        "that exercises it), put them in the same capability even if they live "
        "in different top-level folders.\n\n"
        "BRIEFING RULES:\n"
        "- `what` — one or two sentences plainly describing what this PR delivers. "
        "Read it as if explaining to someone who hasn't seen the diff yet.\n"
        "- `why` — one or two sentences inferring the motivation for the change "
        "from the title, description, commit messages, and the shape of the diff. "
        "If you genuinely cannot tell, say so briefly.\n"
        "- `how` — one or two sentences describing the architectural shape of the "
        "change: which layers are touched, where the risk concentrates, what the "
        "structural pattern is. Specific, not generic.\n"
        "- Use plain prose. No bullet lists, no markdown headings, no preamble. "
        "Light inline `<code>` is fine for identifiers.\n\n"
        "OUTPUT FORMAT — return JSON only, no commentary:\n"
        "{\n"
        '  "capabilities": [\n'
        '    {"name": "...", "intent": "...", '
        '"files": ["path/a", "path/b"], '
        '"layers": ["Services", "Tests"]}\n'
        "  ],\n"
        '  "briefing": {"what": "...", "why": "...", "how": "..."}\n'
        "}\n"
    )


def _build_user_prompt(
    files: list[FileSummary],
    findings: list[FindingSummary],
    pr_title: str,
    pr_body: str,
) -> str:
    lines: list[str] = []
    lines.append("PR TITLE: " + (pr_title.strip() or "(no title)"))
    if pr_body.strip():
        lines.append("\nPR DESCRIPTION:")
        lines.append(pr_body.strip()[:2000])

    lines.append(f"\nFILES ({len(files)}):")
    for f in files:
        lines.append(
            f"- {f.path}  [role={f.role}, +{f.locs} LOC"
            f"{', ' + str(f.finding_count) + ' finding(s)' if f.finding_count else ''}]"
        )

    if findings:
        lines.append(f"\nSURFACED FINDINGS ({len(findings)}) — risk hot-spots to keep together:")
        by_file: dict[str, list[FindingSummary]] = {}
        for fi in findings:
            by_file.setdefault(fi.file, []).append(fi)
        for path, items in by_file.items():
            cats = ", ".join(f"{i.severity}:{i.category}" for i in items)
            lines.append(f"- {path} → {cats}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


class _ParseError(Exception):
    pass


def _parse_and_validate(
    raw: str,
    *,
    files: list[FileSummary],
    soft_cap: int,
) -> tuple[list[Capability], dict[str, str] | None]:
    """Parse the LLM's JSON, coerce shape, enforce scaffold invariants.

    Returns (capabilities, briefing). Briefing is None if missing / malformed —
    capabilities validation is the gating concern, briefing is best-effort.

    Capability rules:
    - Soft: invalid layers dropped; unknown file paths dropped; over-cap
      capabilities truncated.
    - Hard: response must be JSON with a `capabilities` array; every input
      file must end up assigned to exactly one capability."""
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise _ParseError(f"not valid JSON: {exc}") from exc

    raw_caps = data.get("capabilities") if isinstance(data, dict) else None
    if not isinstance(raw_caps, list) or not raw_caps:
        raise _ParseError("response missing non-empty `capabilities` array")

    valid_paths = {f.path for f in files}
    out: list[Capability] = []
    seen_paths: set[str] = set()

    for entry in raw_caps[:soft_cap]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        intent = str(entry.get("intent", "")).strip()
        if not name or not intent:
            continue
        cap_files = tuple(
            p for p in (entry.get("files") or [])
            if isinstance(p, str) and p in valid_paths and p not in seen_paths
        )
        if not cap_files:
            continue
        cap_layers = tuple(
            l for l in (entry.get("layers") or [])
            if isinstance(l, str) and l in LAYER_VOCAB_SET
        )
        seen_paths.update(cap_files)
        out.append(Capability(name=name, intent=intent, files=cap_files, layers=cap_layers))

    if not out:
        raise _ParseError("no usable capabilities in response")

    unassigned = valid_paths - seen_paths
    if unassigned:
        raise _ParseError(f"{len(unassigned)} input files not assigned: {sorted(unassigned)[:5]}")

    briefing = _coerce_briefing(data.get("briefing"))
    return out, briefing


def _coerce_briefing(raw: Any) -> dict[str, str] | None:
    """Best-effort briefing extraction. Returns None if any of what/why/how is
    missing or empty after trimming — partial briefings are worse than none,
    because the wizard's heuristic stub is already a complete fallback."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for key in ("what", "why", "how"):
        v = raw.get(key)
        if not isinstance(v, str):
            return None
        v = v.strip()
        if not v:
            return None
        out[key] = v
    return out


def _strip_fences(raw: str) -> str:
    """Strip a leading ```json … ``` fence if present."""
    s = raw.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline > 0:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


# ---------------------------------------------------------------------------
# Fallback shape.
# ---------------------------------------------------------------------------


def _single_capability(
    files: list[FileSummary],
    *,
    name: str = "All changes",
    intent: str = "Whole-PR view (capability clustering unavailable for this PR).",
) -> Capability:
    layers: list[str] = []
    seen: set[str] = set()
    for f in files:
        layer = _ROLE_TO_LAYER.get(f.role.upper(), "Code")
        if layer in LAYER_VOCAB_SET and layer not in seen:
            layers.append(layer)
            seen.add(layer)
    return Capability(
        name=name,
        intent=intent,
        files=tuple(f.path for f in files),
        layers=tuple(layers),
    )


_ROLE_TO_LAYER: dict[str, str] = {
    "PRODUCTION": "Services",  # nearest fit in the closed vocab
    "TEST": "Tests",
    "INFRA": "Infra",
    "DOCS": "Docs",
    "CONFIG": "Config",
    "BUILD": "Infra",
    "DEPENDENCY": "Config",
    "GENERATED": "Models",
}
