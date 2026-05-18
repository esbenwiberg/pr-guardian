"""Tests for GET /api/dashboard/reviews/{id}/diff with path/line/context params.

Verifies the hunk-extraction path of dashboard_review_diff without hitting
GitHub or a real database.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

# A minimal unified diff patch used across tests.
SAMPLE_PATCH = """\
@@ -1,8 +1,9 @@
 def foo():
     x = 1
-    return x
+    y = x + 1
+    return y

 def bar():
     pass

+# end
"""


@pytest.fixture
def client():
    from pr_guardian.main import app
    return TestClient(app)


@pytest.fixture
def review_id():
    return uuid.uuid4()


@pytest.fixture
def fake_review(review_id):
    return {
        "id": str(review_id),
        "pr_id": "7",
        "repo": "org/repo",
        "platform": "github",
        "pr_url": "https://github.com/org/repo/pull/7",
        "head_commit_sha": "deadbeef",
    }


def _fake_diff(patch: str = SAMPLE_PATCH):
    return SimpleNamespace(
        files=[
            SimpleNamespace(
                path="src/foo.py",
                status="modified",
                old_path=None,
                additions=2,
                deletions=1,
                patch=patch,
            )
        ]
    )


def _patch_deps(monkeypatch, fake_review, diff=None):
    from pr_guardian.api import dashboard as dash

    monkeypatch.setattr(dash.storage, "get_review", AsyncMock(return_value=fake_review))

    adapter = AsyncMock()
    adapter.fetch_diff = AsyncMock(return_value=diff or _fake_diff())

    monkeypatch.setattr(dash, "create_github_adapter", AsyncMock(return_value=adapter))

    from pr_guardian.api import review as rv
    monkeypatch.setattr(rv, "_parse_pr_url", lambda url: (url, "github"))

    pr_stub = SimpleNamespace(
        pr_id="7", repo="org/repo", platform="github",
        source_branch="feat", target_branch="main",
        author="dev", title="T", head_commit_sha="deadbeef",
        pr_url=fake_review["pr_url"], org="", project="",
    )
    monkeypatch.setattr(rv, "_hydrate_pr", AsyncMock(return_value=pr_stub))


# ---------------------------------------------------------------------------
# Hunk-extraction path
# ---------------------------------------------------------------------------

def test_path_and_line_returns_hunk(client, review_id, fake_review, monkeypatch):
    _patch_deps(monkeypatch, fake_review)
    resp = client.get(
        f"/api/dashboard/reviews/{review_id}/diff",
        params={"path": "src/foo.py", "line": 3, "context": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file"] == "src/foo.py"
    assert body["line"] == 3
    assert "lines" in body
    # Should include the add/del lines around line 3 (the return→y change)
    types = [ln["type"] for ln in body["lines"]]
    assert "del" in types or "add" in types or "ctx" in types


def test_context_zero_returns_only_target_line(client, review_id, fake_review, monkeypatch):
    _patch_deps(monkeypatch, fake_review)
    resp = client.get(
        f"/api/dashboard/reviews/{review_id}/diff",
        params={"path": "src/foo.py", "line": 4, "context": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # context=0 means only lines at exactly line 4
    for ln in body["lines"]:
        assert ln["type"] in ("add", "del", "ctx")
    line_numbers = [ln["ln"] for ln in body["lines"] if ln["type"] != "del"]
    assert all(n == 4 for n in line_numbers), f"Expected only line 4, got {line_numbers}"


def test_unknown_file_returns_404(client, review_id, fake_review, monkeypatch):
    _patch_deps(monkeypatch, fake_review)
    resp = client.get(
        f"/api/dashboard/reviews/{review_id}/diff",
        params={"path": "does/not/exist.py", "line": 1, "context": 3},
    )
    assert resp.status_code == 404


def test_unknown_review_returns_404(client, monkeypatch):
    from pr_guardian.api import dashboard as dash
    monkeypatch.setattr(dash.storage, "get_review", AsyncMock(return_value=None))
    rid = uuid.uuid4()
    resp = client.get(
        f"/api/dashboard/reviews/{rid}/diff",
        params={"path": "src/foo.py", "line": 1, "context": 3},
    )
    assert resp.status_code == 404


def test_no_params_returns_full_diff(client, review_id, fake_review, monkeypatch):
    """Without path+line, the endpoint still returns the full diff."""
    _patch_deps(monkeypatch, fake_review)
    resp = client.get(f"/api/dashboard/reviews/{review_id}/diff")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "files" in body
    assert body["files"][0]["path"] == "src/foo.py"
