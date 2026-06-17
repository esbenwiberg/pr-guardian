"""Pool footprint is small + env-tunable so rolling deploys don't exhaust
Postgres max_connections (old + new revisions overlap during deploy)."""

from __future__ import annotations

import pytest

from pr_guardian.persistence.database import _env_int


def test_env_int_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("GUARDIAN_DB_POOL_SIZE", raising=False)
    assert _env_int("GUARDIAN_DB_POOL_SIZE", 3) == 3


def test_env_int_reads_override(monkeypatch):
    monkeypatch.setenv("GUARDIAN_DB_POOL_SIZE", "8")
    assert _env_int("GUARDIAN_DB_POOL_SIZE", 3) == 8


def test_env_int_zero_allowed(monkeypatch):
    monkeypatch.setenv("GUARDIAN_DB_MAX_OVERFLOW", "0")
    assert _env_int("GUARDIAN_DB_MAX_OVERFLOW", 2) == 0


@pytest.mark.parametrize("bad", ["", "abc", "-1", "3.5"])
def test_env_int_falls_back_on_invalid(monkeypatch, bad):
    monkeypatch.setenv("GUARDIAN_DB_POOL_SIZE", bad)
    assert _env_int("GUARDIAN_DB_POOL_SIZE", 3) == 3
