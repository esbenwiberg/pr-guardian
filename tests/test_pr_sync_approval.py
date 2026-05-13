"""Unit tests for self-approval exclusion in pr_sync approval status helpers."""
from __future__ import annotations

import pytest

from pr_guardian.core.pr_sync import (
    _ado_approval_status,
    _gh_approval_status,
    _normalize_ado_pr,
)


# ---------------------------------------------------------------------------
# _ado_approval_status
# ---------------------------------------------------------------------------

class TestAdoApprovalStatus:
    def test_no_reviewers_is_pending(self):
        assert _ado_approval_status([]) == "pending"

    def test_single_non_author_approval(self):
        reviewers = [{"uniqueName": "reviewer@example.com", "vote": 10}]
        assert _ado_approval_status(reviewers, "author@example.com") == "approved"

    def test_self_approval_only_is_pending(self):
        # Author approves their own PR — must not count as approved
        reviewers = [{"uniqueName": "author@example.com", "vote": 10}]
        assert _ado_approval_status(reviewers, "author@example.com") == "pending"

    def test_self_approval_plus_real_approval(self):
        reviewers = [
            {"uniqueName": "author@example.com", "vote": 10},
            {"uniqueName": "reviewer@example.com", "vote": 10},
        ]
        assert _ado_approval_status(reviewers, "author@example.com") == "approved"

    def test_self_approval_with_pending_reviewer(self):
        # Author approved but real reviewer hasn't voted yet
        reviewers = [
            {"uniqueName": "author@example.com", "vote": 10},
            {"uniqueName": "reviewer@example.com", "vote": 0},
        ]
        assert _ado_approval_status(reviewers, "author@example.com") == "pending"

    def test_changes_requested_by_non_author(self):
        reviewers = [{"uniqueName": "reviewer@example.com", "vote": -10}]
        assert _ado_approval_status(reviewers, "author@example.com") == "changes_requested"

    def test_author_changes_requested_excluded(self):
        # Self-requested-changes should also be excluded
        reviewers = [{"uniqueName": "author@example.com", "vote": -10}]
        assert _ado_approval_status(reviewers, "author@example.com") == "pending"

    def test_no_author_id_counts_all_votes(self):
        # Without author_id, all votes are counted (backward compatibility)
        reviewers = [{"uniqueName": "author@example.com", "vote": 10}]
        assert _ado_approval_status(reviewers) == "approved"

    def test_self_approval_matched_by_id_field(self):
        # Some ADO reviewer objects use 'id' instead of 'uniqueName'
        reviewers = [{"id": "user-guid-123", "uniqueName": "", "vote": 10}]
        assert _ado_approval_status(reviewers, "user-guid-123") == "pending"


# ---------------------------------------------------------------------------
# _gh_approval_status
# ---------------------------------------------------------------------------

class TestGhApprovalStatus:
    def _review(self, login: str, state: str) -> dict:
        return {"user": {"login": login}, "state": state}

    def test_no_reviews_is_pending(self):
        assert _gh_approval_status([]) == "pending"

    def test_single_non_author_approval(self):
        reviews = [self._review("reviewer", "APPROVED")]
        assert _gh_approval_status(reviews, "author") == "approved"

    def test_self_approval_only_is_pending(self):
        reviews = [self._review("author", "APPROVED")]
        assert _gh_approval_status(reviews, "author") == "pending"

    def test_self_approval_plus_real_approval(self):
        reviews = [
            self._review("author", "APPROVED"),
            self._review("reviewer", "APPROVED"),
        ]
        assert _gh_approval_status(reviews, "author") == "approved"

    def test_self_approval_with_changes_requested_by_other(self):
        reviews = [
            self._review("author", "APPROVED"),
            self._review("reviewer", "CHANGES_REQUESTED"),
        ]
        assert _gh_approval_status(reviews, "author") == "changes_requested"

    def test_no_author_id_counts_all(self):
        reviews = [self._review("author", "APPROVED")]
        assert _gh_approval_status(reviews) == "approved"

    def test_latest_review_per_reviewer_wins(self):
        # reviewer first dismissed, then approved — should count as approved
        reviews = [
            self._review("reviewer", "DISMISSED"),
            self._review("reviewer", "APPROVED"),
        ]
        assert _gh_approval_status(reviews, "author") == "approved"


# ---------------------------------------------------------------------------
# _normalize_ado_pr — author_id extraction edge cases
# ---------------------------------------------------------------------------

class TestNormalizeAdoPrAuthorId:
    def _pr(self, created_by: dict, reviewers: list[dict]) -> dict:
        return {
            "pullRequestId": 1,
            "title": "t",
            "createdBy": created_by,
            "reviewers": reviewers,
            "sourceRefName": "refs/heads/feat",
            "targetRefName": "refs/heads/main",
        }

    def test_service_account_with_empty_uniquename_falls_back_to_id(self):
        # Service accounts / federated identities can have an empty uniqueName.
        # We must still recognise their self-approval via the GUID id.
        svc_id = "00000000-0000-0000-0000-000000000001"
        pr = self._pr(
            created_by={"id": svc_id, "uniqueName": ""},
            reviewers=[{"id": svc_id, "uniqueName": "", "vote": 10}],
        )
        result = _normalize_ado_pr(pr, "https://dev.azure.com/org", "proj", "repo")
        assert result["approval_status"] == "pending"

    def test_service_account_self_plus_real_reviewer(self):
        svc_id = "00000000-0000-0000-0000-000000000001"
        pr = self._pr(
            created_by={"id": svc_id, "uniqueName": ""},
            reviewers=[
                {"id": svc_id, "uniqueName": "", "vote": 10},
                {"uniqueName": "human@example.com", "vote": 10},
            ],
        )
        result = _normalize_ado_pr(pr, "https://dev.azure.com/org", "proj", "repo")
        assert result["approval_status"] == "approved"

    def test_normal_account_self_approval_still_pending(self):
        pr = self._pr(
            created_by={"id": "guid-1", "uniqueName": "author@example.com"},
            reviewers=[{"uniqueName": "author@example.com", "vote": 10}],
        )
        result = _normalize_ado_pr(pr, "https://dev.azure.com/org", "proj", "repo")
        assert result["approval_status"] == "pending"
