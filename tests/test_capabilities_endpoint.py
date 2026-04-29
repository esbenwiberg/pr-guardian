"""End-to-end tests for the wizard's GET /capabilities endpoint (Phase 3b).

Mocks the clusterer (and the platform diff fetch) so the suite runs in-process
without LLM cost or network access.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pr_guardian.wizard.capability_clusterer import Capability, ClusterResult


@pytest.fixture
def client():
    from pr_guardian.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_capability_cache():
    from pr_guardian.api import dashboard as dash
    dash._capability_cache.clear()
    yield
    dash._capability_cache.clear()


@pytest.fixture
def fake_review():
    return {
        "id": str(uuid.uuid4()),
        "pr_id": "42",
        "repo": "org/repo",
        "platform": "github",
        "title": "Add Graph integration",
        "body": "Wires up the Graph client.",
        "head_commit_sha": "abc123",
        "pr_url": "https://github.com/org/repo/pull/42",
        "agent_results": [
            {"agent_name": "security", "findings": [
                {"id": str(uuid.uuid4()), "severity": "high", "category": "auth",
                 "file": "svc.py", "line": 1},
                {"id": str(uuid.uuid4()), "severity": "medium", "category": "retry",
                 "file": "svc.py", "line": 10},
            ]},
        ],
    }


def _fake_diff_files():
    return [
        SimpleNamespace(path="svc.py",       status="modified", old_path=None, additions=20, deletions=2, patch=""),
        SimpleNamespace(path="model.py",     status="added",    old_path=None, additions=15, deletions=0, patch=""),
        SimpleNamespace(path="tests/x.py",   status="added",    old_path=None, additions=30, deletions=0, patch=""),
    ]


def _patch_endpoint(monkeypatch, review, *, cluster_result=None, diff_raises=None):
    from pr_guardian.api import dashboard as dash

    async def _get(_id): return review
    monkeypatch.setattr(dash.storage, "get_review", _get)

    async def _hydrate(_a, _s, _p): return SimpleNamespace(pr_id="42", repo="org/repo")
    monkeypatch.setattr("pr_guardian.api.review._hydrate_pr", _hydrate)
    monkeypatch.setattr("pr_guardian.api.review._parse_pr_url",
                        lambda url: (SimpleNamespace(), "github"))

    fake_adapter = SimpleNamespace()
    if diff_raises:
        async def _fail(_pr): raise diff_raises
        fake_adapter.fetch_diff = _fail
    else:
        async def _diff(_pr): return SimpleNamespace(files=_fake_diff_files())
        fake_adapter.fetch_diff = _diff
    monkeypatch.setattr(dash, "create_adapter", lambda _p: fake_adapter)

    monkeypatch.setattr(dash, "create_llm_client", lambda _config: object())

    async def _stub_apply(cfg): return cfg
    monkeypatch.setattr(dash, "apply_global_settings", _stub_apply)
    monkeypatch.setattr(dash, "load_service_defaults", lambda: {})
    monkeypatch.setattr(dash, "GuardianConfig", lambda **_kw: SimpleNamespace())

    if cluster_result is not None:
        cluster_mock = AsyncMock(return_value=cluster_result)
        monkeypatch.setattr(dash, "cluster_capabilities", cluster_mock)
        return cluster_mock
    return None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_llm_clusters_with_token_metadata(client, fake_review, monkeypatch):
    cluster = ClusterResult(
        capabilities=[
            Capability(name="Graph integration",
                       intent="Wires up the typed Graph client.",
                       files=("svc.py", "model.py"),
                       layers=("Services", "Models")),
            Capability(name="Tests",
                       intent="Coverage for the new client.",
                       files=("tests/x.py",),
                       layers=("Tests",)),
        ],
        source="llm", model="claude-sonnet",
        input_tokens=420, output_tokens=88,
    )
    cluster_mock = _patch_endpoint(monkeypatch, fake_review, cluster_result=cluster)

    resp = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "llm"
    assert body["model"] == "claude-sonnet"
    assert body["input_tokens"] == 420
    assert body["output_tokens"] == 88
    assert body["cache"] == "miss"
    assert [c["name"] for c in body["capabilities"]] == ["Graph integration", "Tests"]
    assert body["capabilities"][0]["files"] == ["svc.py", "model.py"]
    cluster_mock.assert_awaited_once()


def test_passes_files_findings_pr_metadata_to_clusterer(client, fake_review, monkeypatch):
    cluster = ClusterResult(capabilities=[Capability("X", "y", ("svc.py", "model.py", "tests/x.py"), ("Services",))],
                            source="llm", model="claude-sonnet", input_tokens=1, output_tokens=1)
    cluster_mock = _patch_endpoint(monkeypatch, fake_review, cluster_result=cluster)

    client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")

    call_kwargs = cluster_mock.await_args.kwargs
    assert {f.path for f in call_kwargs["files"]} == {"svc.py", "model.py", "tests/x.py"}
    # Tests file gets TEST role; the production files get PRODUCTION.
    roles_by_path = {f.path: f.role for f in call_kwargs["files"]}
    assert roles_by_path["tests/x.py"] == "TEST"
    assert roles_by_path["svc.py"] == "PRODUCTION"
    assert call_kwargs["pr_title"] == "Add Graph integration"
    assert call_kwargs["pr_body"] == "Wires up the Graph client."
    # Both findings on svc.py make it through to the clusterer.
    finding_files = [f.file for f in call_kwargs["findings"]]
    assert finding_files.count("svc.py") == 2


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_second_call_with_same_head_sha_hits_cache_no_llm(client, fake_review, monkeypatch):
    cluster = ClusterResult(
        capabilities=[Capability("X", "y", ("svc.py", "model.py", "tests/x.py"), ("Services",))],
        source="llm", model="claude-sonnet", input_tokens=1, output_tokens=1,
    )
    cluster_mock = _patch_endpoint(monkeypatch, fake_review, cluster_result=cluster)

    r1 = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    r2 = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["cache"] == "miss"
    assert r2.json()["cache"] == "hit"
    assert cluster_mock.await_count == 1


def test_new_head_commit_sha_invalidates_cache(client, fake_review, monkeypatch):
    cluster = ClusterResult(
        capabilities=[Capability("X", "y", ("svc.py", "model.py", "tests/x.py"), ("Services",))],
        source="llm", model="claude-sonnet", input_tokens=1, output_tokens=1,
    )
    cluster_mock = _patch_endpoint(monkeypatch, fake_review, cluster_result=cluster)

    client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    fake_review["head_commit_sha"] = "def456"  # author pushed a new commit
    client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")

    assert cluster_mock.await_count == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_review_returns_404(client, monkeypatch):
    from pr_guardian.api import dashboard as dash

    async def _none(_id): return None
    monkeypatch.setattr(dash.storage, "get_review", _none)

    resp = client.get(f"/api/dashboard/reviews/{uuid.uuid4()}/capabilities")
    assert resp.status_code == 404


def test_review_without_pr_url_returns_422(client, fake_review, monkeypatch):
    from pr_guardian.api import dashboard as dash

    fake_review["pr_url"] = ""
    async def _get(_id): return fake_review
    monkeypatch.setattr(dash.storage, "get_review", _get)

    resp = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    assert resp.status_code == 422


def test_diff_fetch_failure_returns_502(client, fake_review, monkeypatch):
    _patch_endpoint(monkeypatch, fake_review, diff_raises=RuntimeError("github 500"))
    resp = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    assert resp.status_code == 502


def test_clusterer_fallback_response_propagates_to_client(client, fake_review, monkeypatch):
    fallback = ClusterResult(
        capabilities=[Capability("All changes", "Single fallback bucket.",
                                 ("svc.py", "model.py", "tests/x.py"), ("Services",))],
        source="fallback_error", error="parse: not valid JSON",
    )
    _patch_endpoint(monkeypatch, fake_review, cluster_result=fallback)

    resp = client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback_error"
    assert body["error"] == "parse: not valid JSON"
    assert body["capabilities"][0]["name"] == "All changes"


# ---------------------------------------------------------------------------
# Wizard contract — finding filtering before clustering
# ---------------------------------------------------------------------------


def test_dismissed_findings_are_not_passed_to_clusterer(client, fake_review, monkeypatch):
    fake_review["agent_results"][0]["findings"][0]["dismissal"] = {"id": "x", "status": "false_positive"}
    cluster = ClusterResult(
        capabilities=[Capability("X", "y", ("svc.py", "model.py", "tests/x.py"), ("Services",))],
        source="llm", model="claude-sonnet", input_tokens=1, output_tokens=1,
    )
    cluster_mock = _patch_endpoint(monkeypatch, fake_review, cluster_result=cluster)

    client.get(f"/api/dashboard/reviews/{fake_review['id']}/capabilities")

    findings = cluster_mock.await_args.kwargs["findings"]
    assert len(findings) == 1
    assert findings[0].severity == "medium"  # dismissed high-severity finding excluded
