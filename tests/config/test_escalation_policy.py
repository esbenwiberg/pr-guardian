from __future__ import annotations

import pytest
from pydantic import ValidationError

from pr_guardian.config.profile_resolver import (
    profile_settings_to_config,
    sanitize_profile_settings,
    _STRUCTURED_PROFILE_KEYS,
)
from pr_guardian.config.schema import EscalationPolicyConfig


def test_defaults_when_absent():
    config = profile_settings_to_config({})
    assert config.escalation_policy.mode == "standard"
    assert config.escalation_policy.gate_threshold == "medium_plus"
    assert config.escalation_policy.reject_threshold == "confident_only"


def test_escalation_policy_is_structured_key():
    assert "escalation_policy" in _STRUCTURED_PROFILE_KEYS


def test_roundtrip_full_block():
    settings = {
        "escalation_policy": {
            "mode": "structural_only",
            "gate_threshold": "high",
            "reject_threshold": "any",
        }
    }
    config = profile_settings_to_config(settings)
    assert config.escalation_policy.mode == "structural_only"
    assert config.escalation_policy.gate_threshold == "high"
    assert config.escalation_policy.reject_threshold == "any"


def test_roundtrip_partial_block_uses_defaults_for_missing_fields():
    settings = {"escalation_policy": {"mode": "structural_only"}}
    config = profile_settings_to_config(settings)
    assert config.escalation_policy.mode == "structural_only"
    assert config.escalation_policy.gate_threshold == "medium_plus"
    assert config.escalation_policy.reject_threshold == "confident_only"


def test_sanitize_passes_escalation_policy_through():
    settings = {"escalation_policy": {"mode": "structural_only", "gate_threshold": "low"}}
    clean = sanitize_profile_settings(settings)
    assert clean["escalation_policy"] == settings["escalation_policy"]


def test_sanitize_drops_scalar_escalation_policy():
    # A legacy/corrupt row with a scalar instead of a dict must be healed.
    clean = sanitize_profile_settings({"escalation_policy": "structural_only"})
    assert "escalation_policy" not in clean


def test_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        EscalationPolicyConfig(mode="turbo")  # type: ignore[arg-type]


def test_unknown_gate_threshold_rejected():
    with pytest.raises(ValidationError):
        EscalationPolicyConfig(gate_threshold="extreme")  # type: ignore[arg-type]


def test_unknown_reject_threshold_rejected():
    with pytest.raises(ValidationError):
        EscalationPolicyConfig(reject_threshold="never")  # type: ignore[arg-type]


def test_unknown_enum_via_profile_settings_to_config_rejected():
    with pytest.raises(ValidationError):
        profile_settings_to_config({"escalation_policy": {"mode": "unknown_mode"}})
