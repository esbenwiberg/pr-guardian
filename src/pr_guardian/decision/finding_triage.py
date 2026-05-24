"""Per-finding triage: classify each finding as noise / fyi / decision.

Pure-Python classifier for the wizard's three-class triage. Operates on
the JSON-shaped finding dicts the dashboard already produces, so it can
be applied at request time without reaching into the SQLAlchemy models.

Classes:
- decision  — surface as a wizard decision the human must make
- fyi       — informational, hidden from the main flow but available
              in the wrap-up audit drawer
- noise     — too low-signal to show; included in audit count only

The default rules below are intentionally conservative — Phase 2 does
not yet expose tuning knobs in `GuardianConfig`. Phase 2.5 can lift
these into config when usage tells us where the boundaries should sit.
"""

from __future__ import annotations

from typing import Any, Iterable

# The three classes, exported as constants so callers (tests, the
# dashboard JSON enricher, the wizard's JS via the JSON envelope) all
# agree on the wire format.
NOISE = "noise"
FYI = "fyi"
DECISION = "decision"

VALID_CLASSES = (NOISE, FYI, DECISION)


def classify_finding(finding: dict[str, Any]) -> str:
    """Return one of NOISE / FYI / DECISION for a single finding dict.

    Decision rules (first match wins):
    - dismissed findings → NOISE (the human already decided)
    - severity ∈ {high, critical} → DECISION
    - severity = medium and certainty = detected → DECISION
    - severity = medium → FYI
    - severity = low and certainty = detected → FYI
    - severity = low → NOISE
    - missing/unknown severity → DECISION (fail safe — surface it)
    """
    if finding.get("dismissal"):
        return NOISE

    sev = (finding.get("severity") or "").lower()
    cert = (finding.get("certainty") or "").lower()

    if sev in ("high", "critical"):
        return DECISION
    if sev == "medium":
        return DECISION if cert == "detected" else FYI
    if sev == "low":
        return FYI if cert == "detected" else NOISE

    # Unknown / missing severity: fail safe — surface for human review.
    return DECISION


def tag_findings_with_triage(agent_results: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Annotate every finding dict in-place with a `triage` field.

    Returns a counts dict {noise, fyi, decision} for use in summary UIs.
    """
    counts = {NOISE: 0, FYI: 0, DECISION: 0}
    for agent in agent_results or []:
        for finding in agent.get("findings") or []:
            cls = classify_finding(finding)
            finding["triage"] = cls
            counts[cls] += 1
    return counts
