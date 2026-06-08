from __future__ import annotations

from pr_guardian.api import reviews_queue


def test_shape_review_preserves_failed_review_state():
    row = reviews_queue._shape_review(
        {
            "id": "review-failed",
            "platform": "github",
            "repo": "repo/api",
            "pr_id": "118",
            "title": "failed review row",
            "decision": "error",
            "stage": "error",
            "stage_detail": "Candidate review failed: provider unavailable",
            "agent_results": [],
            "started_at": "2026-06-02T10:00:00Z",
            "finished_at": "2026-06-02T10:01:00Z",
        }
    )

    assert row["decision"] == "error"
    assert row["stage"] == "error"
    assert row["stage_detail"] == "Candidate review failed: provider unavailable"
    assert row["findings"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


async def test_reviews_queue_merges_actionable_candidates_and_hides_drafts(monkeypatch):
    async def fake_list_reviews(limit: int = 100):
        return [
            {
                "id": "review-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "118",
                "title": "completed review row",
                "decision": "human_review",
                "started_at": "2026-06-02T10:00:00Z",
            }
        ]

    async def fake_get_synced_pr_lookup(keys):
        return {}

    async def fake_list_candidates(**kwargs):
        assert kwargs["states"] == ["waiting", "blocked"]
        return [
            {
                "id": "candidate-waiting",
                "repo_link_id": "repo-link-1",
                "profile_id": "profile-1",
                "connection_id": "connection-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "124",
                "head_sha": "abc",
                "state": "waiting",
                "reason": "checks_pending",
                "readiness_snapshot": {"checks": {"total": 2, "passed": 1, "pending": 1}},
                "updated_at": "2026-06-02T12:00:00Z",
            },
            {
                "id": "candidate-blocked",
                "repo_link_id": "repo-link-1",
                "profile_id": "profile-1",
                "connection_id": "connection-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "125",
                "head_sha": "def",
                "state": "blocked",
                "reason": "checks_timeout",
                "readiness_snapshot": {},
                "updated_at": "2026-06-02T11:00:00Z",
            },
            {
                "id": "candidate-draft",
                "repo_link_id": "repo-link-1",
                "profile_id": "profile-1",
                "connection_id": "connection-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "126",
                "head_sha": "ghi",
                "state": "waiting",
                "reason": "draft",
                "readiness_snapshot": {"draft": True},
                "updated_at": "2026-06-02T13:00:00Z",
            },
            {
                "id": "candidate-errorish",
                "repo_link_id": "repo-link-1",
                "profile_id": "profile-1",
                "connection_id": "connection-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "127",
                "head_sha": "jkl",
                "state": "blocked",
                "reason": "profile_unavailable",
                "readiness_snapshot": {},
                "updated_at": "2026-06-02T13:00:00Z",
            },
            {
                "id": "candidate-waiting-errorish",
                "repo_link_id": "repo-link-1",
                "profile_id": "profile-1",
                "connection_id": "connection-1",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "128",
                "head_sha": "mno",
                "state": "waiting",
                "reason": "connection_unavailable",
                "readiness_snapshot": {},
                "updated_at": "2026-06-02T13:00:00Z",
            },
            {
                "id": "candidate-waiting-unknown",
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "129",
                "head_sha": "pqr",
                "state": "waiting",
                "reason": "technical_error",
                "readiness_snapshot": {},
                "updated_at": "2026-06-02T13:00:00Z",
            },
            {
                "id": "candidate-unlinked",
                "platform": "github",
                "repo": "browse/only",
                "pr_id": "129",
                "head_sha": "pqr",
                "state": "waiting",
                "reason": "checks_pending",
                "readiness_snapshot": {},
                "updated_at": "2026-06-02T13:00:00Z",
            },
        ]

    monkeypatch.setattr(reviews_queue.storage, "list_reviews", fake_list_reviews)
    monkeypatch.setattr(reviews_queue.storage, "get_synced_pr_lookup", fake_get_synced_pr_lookup)
    monkeypatch.setattr(
        reviews_queue.storage,
        "list_active_readiness_candidates",
        fake_list_candidates,
    )

    response = await reviews_queue.reviews_queue(None)  # type: ignore[arg-type]

    ids = {item["id"] for item in response["items"]}
    assert {"review-1", "candidate-waiting", "candidate-blocked"} <= ids
    assert "candidate-draft" not in ids
    assert "candidate-errorish" not in ids
    assert "candidate-waiting-errorish" not in ids
    assert "candidate-unlinked" not in ids
    assert "candidate-waiting-unknown" not in ids
    candidate = next(item for item in response["items"] if item["id"] == "candidate-waiting")
    assert candidate["subject_type"] == "candidate"
    assert candidate["row_key"] == "candidate:candidate-waiting"
    assert candidate["readiness"]["snapshot"]["checks"]["pending"] == 1
