"""Verify the closed StickyTriggerKind set contains 'gate_agent' (ADR-011)."""

from typing import get_args

from pr_guardian.api.dashboard import _VALID_TRIGGER_KINDS
from pr_guardian.decision.types import StickyTriggerKind


def test_gate_agent_in_sticky_trigger_kind_literal():
    assert "gate_agent" in get_args(StickyTriggerKind)


def test_gate_agent_accepted_by_dashboard_verify_endpoint():
    assert "gate_agent" in _VALID_TRIGGER_KINDS
