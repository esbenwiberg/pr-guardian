"""Tests for the Conventional Commits commit-msg hook."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_hook(tmp_path: Path, subject: str) -> subprocess.CompletedProcess[str]:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(subject + "\n\nBody is ignored.\n")
    return subprocess.run(
        ["bash", "scripts/check-commit-msg.sh", str(msg_file)],
        check=False,
        text=True,
        capture_output=True,
    )


def test_commit_msg_hook_accepts_scoped_conventional_subject(tmp_path):
    result = _run_hook(tmp_path, "feat(api): add review queue endpoint")

    assert result.returncode == 0
    assert result.stderr == ""


def test_commit_msg_hook_rejects_untyped_subject_with_helpful_error(tmp_path):
    result = _run_hook(tmp_path, "add review queue endpoint")

    assert result.returncode == 1
    assert "Expected: <type>(<scope>)?!?: <subject>" in result.stderr
    assert "feat(api): add /reviews/queue endpoint" in result.stderr


def test_commit_msg_hook_allows_merge_and_fixup_subjects(tmp_path):
    assert _run_hook(tmp_path, "Merge branch 'main' into feature").returncode == 0
    assert _run_hook(tmp_path, "fixup! feat(api): add review queue endpoint").returncode == 0
