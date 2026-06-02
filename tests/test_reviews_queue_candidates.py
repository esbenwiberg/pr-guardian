from __future__ import annotations

from pr_guardian.api import reviews_queue


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
                "platform": "github",
                "repo": "repo/api",
                "pr_id": "127",
                "head_sha": "jkl",
                "state": "blocked",
                "reason": "profile_unavailable",
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
    candidate = next(item for item in response["items"] if item["id"] == "candidate-waiting")
    assert candidate["subject_type"] == "candidate"
    assert candidate["row_key"] == "candidate:candidate-waiting"
    assert candidate["readiness"]["snapshot"]["checks"]["pending"] == 1
