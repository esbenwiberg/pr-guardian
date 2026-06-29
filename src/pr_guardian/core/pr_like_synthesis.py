"""Cross-PR synthesis for the deep ("fat nightly") scan.

The deep scan re-reviews every merged PR independently and emits one verdict card
per PR. Those cards answer "is *this* PR ok?" but nobody reads *across* them. This
step does: it takes the assembled per-PR cards (verdicts + findings, NOT raw
diffs) and asks one LLM pass for the patterns only visible across the whole batch:

- **Recurrence** — the same finding class flagged in several independent PRs.
- **Hotspot convergence** — one file/module flagged across multiple PRs.
- **Gate effectiveness** — how many PRs would need human attention at full depth
  that the thin daytime gate let through.

This is deliberately NOT a 5th macro scan agent: the macro ``recent_changes`` scan
(TrendAgent etc.) reasons over the combined *code*; this reasons over the *review
outcomes*. The output is a narrative card, not findings — it never produces
``ScanFinding``s (that would double-count the per-PR findings it summarizes) and
its failure is non-fatal: a deep scan with no synthesis still saves every per-PR
result.
"""

from __future__ import annotations

import structlog

from pr_guardian.agents.prompt_composer import build_agent_prompt
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.recent_changes import _estimate_cost
from pr_guardian.llm.factory import create_llm_client, resolve_model
from pr_guardian.llm.protocol import LLMClient
from pr_guardian.models.findings import Verdict
from pr_guardian.models.scan import ScanAgentResult

log = structlog.get_logger()

# Internal model-resolution key (matches an optional agent_override); distinct
# from the card's human-facing display name below.
SYNTHESIS_AGENT_KEY = "cross_pr_synthesis"
# Display name on the card. The dashboard detects this exact string to hoist the
# card above the per-PR grid — keep the two in sync.
SYNTHESIS_DISPLAY_NAME = "Cross-PR Synthesis"

# Don't synthesize a "pattern across PRs" from a single PR — there's no across.
_MIN_REVIEWS = 2
# Keep the digest cheap and bounded regardless of how large the scan was.
_MAX_DESC_CHARS = 200
_DIGEST_CHAR_BUDGET = 60_000

_VERDICT_LABEL = {
    Verdict.PASS: "pass (auto-approve-equivalent)",
    Verdict.WARN: "warn (human-review-equivalent)",
    Verdict.FLAG_HUMAN: "flag_human (reject/block at full depth)",
}


def _build_digest(pr_cards: list[ScanAgentResult], repo: str) -> str:
    """Render the per-PR cards into a compact, diff-free digest for synthesis."""
    counts = {Verdict.PASS: 0, Verdict.WARN: 0, Verdict.FLAG_HUMAN: 0}
    for ar in pr_cards:
        counts[ar.verdict] = counts.get(ar.verdict, 0) + 1

    parts: list[str] = [
        f"# Deep re-review of {repo}",
        f"{len(pr_cards)} merged PR(s) re-reviewed at full PR-review depth.",
        "",
        "## Verdict distribution",
        f"- pass: {counts.get(Verdict.PASS, 0)}",
        f"- warn (human review): {counts.get(Verdict.WARN, 0)}",
        f"- flag_human (reject/block): {counts.get(Verdict.FLAG_HUMAN, 0)}",
        "",
        "## Per-PR outcomes",
    ]
    used = sum(len(p) for p in parts)
    omitted = 0
    for ar in pr_cards:
        block = [f"\n### {ar.agent_name} — verdict: {ar.verdict.value}"]
        if ar.error:
            block.append(f"(review failed: {ar.error})")
        elif ar.findings:
            block.append(f"Findings ({len(ar.findings)}):")
            for f in ar.findings:
                desc = (f.description or "").strip().replace("\n", " ")
                if len(desc) > _MAX_DESC_CHARS:
                    desc = desc[:_MAX_DESC_CHARS] + "…"
                loc = f"{f.file}:{f.line}" if f.line is not None else (f.file or "?")
                block.append(
                    f"- [{f.severity.value}/{f.category}] (lens: {f.agent_name}) {loc} — {desc}"
                )
        else:
            block.append("No findings.")
        text = "\n".join(block)
        if used + len(text) > _DIGEST_CHAR_BUDGET:
            omitted += 1
            continue
        parts.append(text)
        used += len(text)

    if omitted:
        parts.append(f"\n_({omitted} PR(s) omitted from this digest due to size budget.)_")
    return "\n".join(parts)


async def synthesize_cross_pr(
    pr_cards: list[ScanAgentResult],
    repo: str,
    config: GuardianConfig,
    llm_client: LLMClient | None = None,
) -> ScanAgentResult | None:
    """Produce one narrative synthesis card over the per-PR review outcomes.

    Returns ``None`` (and never raises) when synthesis is not applicable or the
    LLM call fails — synthesis is additive, so a failure must not fail the scan.
    """
    reviewed = [ar for ar in pr_cards if ar.error is None]
    if len(reviewed) < _MIN_REVIEWS:
        log.info("cross_pr_synthesis_skipped", repo=repo, reviewed=len(reviewed))
        return None

    system_prompt = build_agent_prompt("recent_changes_deep/synthesis", [])
    user_message = _build_digest(pr_cards, repo)
    model = resolve_model(config, SYNTHESIS_AGENT_KEY)

    # Everything from client creation onward is best-effort: synthesis is additive,
    # so an unconfigured provider or a failed call must skip the card, never fail
    # the scan (the per-PR results are already in hand and must still save).
    try:
        llm = llm_client or create_llm_client(config)
        response = await llm.complete(
            system=system_prompt,
            user=user_message,
            model=model,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
        )
    except Exception as e:
        log.warning("cross_pr_synthesis_failed", repo=repo, error=str(e))
        return None

    summary = (response.content or "").strip()
    if not summary:
        log.warning("cross_pr_synthesis_empty", repo=repo)
        return None

    cost = _estimate_cost(model, response.input_tokens, response.output_tokens)
    return ScanAgentResult(
        agent_name=SYNTHESIS_DISPLAY_NAME,
        verdict=Verdict.PASS,  # the card is a narrative, not a gate — never blocks
        findings=[],
        summary=summary,
        extras={
            "model": model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": cost,
        },
    )
