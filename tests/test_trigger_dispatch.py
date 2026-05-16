"""Tests for /api/reviews/trigger: scan/PR routing across GitHub and ADO."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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
