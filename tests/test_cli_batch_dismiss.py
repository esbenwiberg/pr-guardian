from __future__ import annotations

from click.testing import CliRunner

from pr_guardian.cli import main
from pr_guardian.persistence import storage


def test_batch_dismiss_rejects_invalid_severity_before_dismissal(monkeypatch):
    upsert_calls = []

    async def fail_get_review(_review_id):
        raise AssertionError("storage.get_review should not be called")

    async def record_upsert_dismissal(**kwargs):
        upsert_calls.append(kwargs)

    monkeypatch.setattr(storage, "get_review", fail_get_review)
    monkeypatch.setattr(storage, "upsert_dismissal", record_upsert_dismissal)

    result = CliRunner().invoke(
        main,
        [
            "batch-dismiss",
            "00000000-0000-0000-0000-000000000000",
            "--status",
            "acknowledged",
            "--severity",
            "medum",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--severity'" in result.output
    assert upsert_calls == []
