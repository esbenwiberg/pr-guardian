"""Contract test for the additive guardian/review merge gate.

Proves brief 03 scenario ``required-check-added-additively``: enforcing the
gate adds ``guardian/review`` to branch protection without dropping the repo's
existing required checks (``ci/test``, ``lint``) or its other protection
settings (we PATCH only the required_status_checks sub-resource, so the rest of
the protection config is never touched).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pr_guardian.platform.github import GitHubAdapter


def _resp(json_data: dict | list, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _adapter(*responses: MagicMock) -> GitHubAdapter:
    adapter = GitHubAdapter(token="test-token")
    client = AsyncMock()
    client.get = AsyncMock(side_effect=list(responses))
    client.patch = AsyncMock()
    adapter._client = client
    return adapter


@pytest.mark.asyncio
async def test_guardian_review_required_check_is_added_without_dropping_existing_requirements():
    adapter = _adapter(
        _resp({"default_branch": "main"}),
        _resp({"required_status_checks": {"strict": True, "contexts": ["ci/test", "lint"]}}),
    )
    adapter._client.patch.return_value = _resp(
        {"contexts": ["ci/test", "guardian/review", "lint"]}
    )

    result = await adapter.ensure_required_review_check("owner/repo")

    assert result.state == "enforced"
    adapter._client.patch.assert_awaited_once()
    assert (
        adapter._client.patch.await_args.args[0]
        == "/repos/owner/repo/branches/main/protection/required_status_checks"
    )
    payload = adapter._client.patch.await_args.kwargs["json"]
    # guardian/review added; existing ci/test + lint still required; strict preserved.
    assert payload["contexts"] == ["ci/test", "guardian/review", "lint"]
    assert payload["strict"] is True


@pytest.mark.asyncio
async def test_gate_reports_unsupported_when_branch_protection_is_absent():
    """If the branch has no protection at all, enforcement can't add a required
    check — surface an actionable 'enable branch protection first' state rather
    than silently patching."""
    adapter = _adapter(
        _resp({"default_branch": "main"}),
        _resp({}, status_code=404),
    )

    result = await adapter.ensure_required_review_check("owner/repo")

    assert result.state == "unsupported"
    adapter._client.patch.assert_not_awaited()
