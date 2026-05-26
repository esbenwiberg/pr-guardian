"""Regression coverage for persisted review fields exposed to API handlers."""

from __future__ import annotations

from pr_guardian.persistence.models import ReviewRow
from pr_guardian.persistence.storage import _review_to_dict


def test_review_to_dict_exposes_comment_mode_and_pat_name():
    row = ReviewRow(
        pr_id="42",
        repo="org/repo",
        platform="github",
        comment_mode="inline",
        pat_name="work-pat",
    )

    data = _review_to_dict(row)

    assert data["comment_mode"] == "inline"
    assert data["pat_name"] == "work-pat"
