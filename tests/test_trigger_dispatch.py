"""Tests for /api/reviews/trigger: scan/PR routing across GitHub and ADO."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pr_guardian.api.reviews_queue import _resolve_repo_scan_target


class TestResolveRepoScanTarget:
    def test_github_full_url(self):
        repo, plat = _resolve_repo_scan_target("https://github.com/octocat/spoon", None)
        assert (repo, plat) == ("octocat/spoon", "github")

    def test_github_full_url_with_git_suffix(self):
        repo, plat = _resolve_repo_scan_target("https://github.com/octocat/spoon.git", None)
        assert (repo, plat) == ("octocat/spoon", "github")

    def test_ado_full_url(self):
        repo, plat = _resolve_repo_scan_target(
            "https://dev.azure.com/myorg/myproj/_git/myrepo", None,
        )
        assert (repo, plat) == ("myproj/myrepo", "ado")

    def test_ado_triple_shorthand(self):
        repo, plat = _resolve_repo_scan_target("myorg/myproj/myrepo", None)
        assert (repo, plat) == ("myproj/myrepo", "ado")

    def test_short_form_defaults_to_github(self):
        repo, plat = _resolve_repo_scan_target("owner/repo", None)
        assert (repo, plat) == ("owner/repo", "github")

    def test_short_form_can_force_ado(self):
        repo, plat = _resolve_repo_scan_target("project/repo", "ado")
        assert (repo, plat) == ("project/repo", "ado")

    def test_unparseable_raises(self):
        with pytest.raises(HTTPException) as exc:
            _resolve_repo_scan_target("not a repo", None)
        assert exc.value.status_code == 400


class TestTriggerRouteScanDispatch:
    """POST /api/reviews/trigger with scan mode calls manual_repo_review with canonical repo/platform.

    The trigger route uses a lazy `from pr_guardian.api.review import manual_repo_review`
    inside the function body, so patching the definition site
    (pr_guardian.api.review.manual_repo_review) is correct — the lazy import
    fetches the name from the module's namespace at call time, picking up the mock.
    """

    @pytest.fixture()
    def client(self):
        from pr_guardian.main import app
        with TestClient(app) as c:
            yield c

    def _mock_repo_resp(self, repo="octocat/spoon", platform="github"):
        from pr_guardian.api.review import RepoReviewResponse
        return RepoReviewResponse(
            status="queued",
            repo=repo,
            platform=platform,
            ref="HEAD",
            selection="all",
            max_files=300,
        )

    def test_github_url_dispatches_with_canonical_repo(self, client):
        """Full GitHub URL resolves to owner/repo + github and the route returns those."""
        mock_resp = self._mock_repo_resp("octocat/spoon", "github")
        with patch(
            "pr_guardian.api.review.manual_repo_review",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_trigger:
            resp = client.post(
                "/api/reviews/trigger",
                json={"url": "https://github.com/octocat/spoon", "mode": "scan"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["repo"] == "octocat/spoon"
        assert data["platform"] == "github"
        # Prove the mock was actually called (not vacuous): assert_awaited_once
        # would raise if the real function ran instead, because the real function
        # requires platform credentials and would 500.
        mock_trigger.assert_awaited_once()
        call_req = mock_trigger.call_args[0][0]
        assert call_req.repo == "octocat/spoon"
        assert call_req.platform == "github"

    def test_github_url_with_git_suffix_dispatches_correctly(self, client):
        """.git suffix is stripped before dispatch."""
        mock_resp = self._mock_repo_resp("octocat/spoon", "github")
        with patch(
            "pr_guardian.api.review.manual_repo_review",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_trigger:
            resp = client.post(
                "/api/reviews/trigger",
                json={"url": "https://github.com/octocat/spoon.git", "mode": "scan"},
            )

        assert resp.status_code == 200
        mock_trigger.assert_awaited_once()
        call_req = mock_trigger.call_args[0][0]
        assert call_req.repo == "octocat/spoon"
        assert call_req.platform == "github"

    def test_ado_url_dispatches_with_project_repo(self, client):
        """ADO URL resolves to project/repo + ado."""
        mock_resp = self._mock_repo_resp("myproj/myrepo", "ado")
        with patch(
            "pr_guardian.api.review.manual_repo_review",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_trigger:
            resp = client.post(
                "/api/reviews/trigger",
                json={
                    "url": "https://dev.azure.com/myorg/myproj/_git/myrepo",
                    "mode": "scan",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["repo"] == "myproj/myrepo"
        assert data["platform"] == "ado"
        mock_trigger.assert_awaited_once()
        call_req = mock_trigger.call_args[0][0]
        assert call_req.repo == "myproj/myrepo"
        assert call_req.platform == "ado"
