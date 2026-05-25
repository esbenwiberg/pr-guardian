from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

log = structlog.get_logger()

# Specs path reference: e.g. specs/auth/login.md or specs/feature-x.md
_SPEC_REF = re.compile(r"\bspecs/[\w/.-]+\.md\b")

# Cap on spec-file fetches per PR to bound I/O latency and rate-limit exposure
# from bot-generated PRs that list many specs/... paths.
_MAX_SPEC_FETCHES = 5

# Generic keywords that, when present as the entirety of the text, indicate no
# concrete scope claim was made.
_GENERIC_ONLY = re.compile(
    r"^\s*(misc|update|updates|refactor|refactoring|fixes|fix|cleanup|clean"
    r"|wip|todo|tbd|n/?a|chore|patch|bump|minor)\s*$",
    re.IGNORECASE,
)

# HTML comments and common template placeholder markers stripped before char
# counting so they don't inflate the 80-char threshold.
_TEMPLATE_NOISE = re.compile(
    r"<!--.*?-->|"
    r"\[description\]|"
    r"\[your .+?\]|"
    r"_describe .+?_|"
    r"< ?describe .+? ?>",
    re.IGNORECASE | re.DOTALL,
)

# Issue/work-item reference patterns (GitHub #NNN, ADO AB#NNN, GH URL) —
# detected only for logging; never fetched in v1.
_ISSUE_PATTERN = re.compile(
    r"(?:#\d+|AB#\d+|github\.com/[^/]+/[^/]+/issues/\d+)",
    re.IGNORECASE,
)


@dataclass
class IntentAnchorContext:
    """Classified intent anchor from a PR's title, body, and referenced specs."""

    has_useful_anchor: bool
    anchor_kind: Literal["spec", "title_body", "missing"]
    title: str
    body: str
    referenced_specs: dict[str, str] = field(default_factory=dict)
    missing_reason: str | None = None


def _strip_template_noise(text: str) -> str:
    return _TEMPLATE_NOISE.sub("", text)


def _count_concrete_chars(text: str) -> int:
    """Return count of non-whitespace characters after stripping template noise."""
    cleaned = _strip_template_noise(text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)  # strip bare URLs
    return len(re.sub(r"\s+", "", cleaned))


def _is_generic(text: str) -> bool:
    """Return True when the entire text is a generic placeholder or empty."""
    return not text.strip() or bool(_GENERIC_ONLY.match(text.strip()))


async def load_intent_anchors(
    title: str,
    body: str | None,
    adapter=None,
    repo: str = "",
    head_sha: str = "",
) -> IntentAnchorContext:
    """Classify the intent anchor from PR title/body and fetchable spec files.

    V1 rules:
    - A reachable specs/... markdown file is a useful anchor (kind=spec).
    - At least 80 non-template characters with a non-generic concrete claim is
      useful (kind=title_body).
    - Everything else is missing.
    - Work items, GitHub issues, and ADO work item APIs are NOT fetched in v1.
    """
    body = body or ""

    # Log if body mentions issues/work items so it's clear they were skipped.
    if _ISSUE_PATTERN.search(body):
        log.debug(
            "intent_anchor_issue_refs_skipped",
            reason="work item and issue fetching is out of scope for v1 intent anchors",
        )

    # --- Try fetchable specs/... references ---
    # Cap to _MAX_SPEC_FETCHES distinct paths so a PR body listing many
    # specs/... references cannot stall the agent or burn platform rate limit.
    spec_paths: list[str] = list(dict.fromkeys(
        _SPEC_REF.findall(f"{title} {body}")
    ))[:_MAX_SPEC_FETCHES]

    referenced_specs: dict[str, str] = {}
    if spec_paths and adapter and repo:
        ref = head_sha or "HEAD"
        for spec_path in spec_paths:
            try:
                content = await adapter.fetch_file_content(repo, spec_path, ref=ref)
                if content:
                    referenced_specs[spec_path] = content
            except Exception as exc:
                log.debug(
                    "intent_spec_fetch_failed",
                    spec_path=spec_path,
                    repo=repo,
                    error=str(exc),
                )

    if referenced_specs:
        return IntentAnchorContext(
            has_useful_anchor=True,
            anchor_kind="spec",
            title=title,
            body=body,
            referenced_specs=referenced_specs,
        )

    # --- Check 80-char concrete-claim heuristic on title + body ---
    combined = f"{title} {body}"
    if not _is_generic(combined) and _count_concrete_chars(combined) >= 80:
        return IntentAnchorContext(
            has_useful_anchor=True,
            anchor_kind="title_body",
            title=title,
            body=body,
        )

    # --- Missing anchor ---
    if not body.strip():
        reason = "PR body is empty"
    elif _is_generic(combined):
        reason = "PR title/body contains only generic text (e.g. misc, update, refactor)"
    else:
        reason = (
            "PR title/body has fewer than 80 non-template characters "
            "with a concrete behavior or scope claim"
        )

    return IntentAnchorContext(
        has_useful_anchor=False,
        anchor_kind="missing",
        title=title,
        body=body,
        missing_reason=reason,
    )
