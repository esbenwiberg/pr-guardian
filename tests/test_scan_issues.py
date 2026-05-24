"""Tests for scan issue creation: storage, API endpoints, and platform adapters."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pr_guardian.main import app
from pr_guardian.persistence.storage import create_scan_issue, get_scan_issues
from pr_guardian.platform.ado import ADOAdapter
from pr_guardian.platform.github import GitHubAdapter


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------

# Minimal in-memory schema that mirrors the scan_issues table without
# requiring PostgreSQL JSONB or UUID types.
_meta = sa.MetaData()
sa.Table("scans", _meta, sa.Column("id", sa.Text, primary_key=True))
sa.Table(
    "scan_issues",
    _meta,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("scan_id", sa.Text, nullable=False),
    sa.Column("finding_ids", sa.Text, nullable=False, server_default="[]"),
    sa.Column("issue_url", sa.Text, nullable=False, server_default=""),
    sa.Column("issue_number", sa.Text, nullable=False, server_default=""),
    sa.Column("title", sa.Text, nullable=False, server_default=""),
    sa.Column("platform", sa.Text, nullable=False, server_default=""),
    sa.Column("repo", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime),
)


async def _make_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_get_scan_issues_unknown_scan_returns_empty():
    engine, factory = await _make_session_factory()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            result = await get_scan_issues(uuid.uuid4())
        assert result == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_scan_issue_and_get():
    """create_scan_issue persists and get_scan_issues retrieves it."""
    engine, factory = await _make_session_factory()
    scan_id = uuid.uuid4()
    finding_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    # The SQLAlchemy ORM model stores JSONB as Python list; SQLite stores it
    # as JSON text. We patch at the session level to use the in-memory DB.
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            # Seed the parent scan row (FK-less in SQLite)
            async with factory() as session:
                await session.execute(
                    sa.text("INSERT INTO scans (id) VALUES (:id)"),
                    {"id": str(scan_id)},
                )
                await session.commit()

            new_id = await create_scan_issue(
                scan_id=scan_id,
                finding_ids=finding_ids,
                issue_url="https://github.com/org/repo/issues/42",
                issue_number="42",
                title="[PR Guardian] test issue",
                platform="github",
                repo="org/repo",
            )
            assert isinstance(new_id, uuid.UUID)

            # Verify round-trip retrieval — especially the JSONB finding_ids column
            issues = await get_scan_issues(scan_id)
        assert len(issues) == 1
        issue = issues[0]
        assert str(issue["id"]) == str(new_id)
        assert issue["issue_url"] == "https://github.com/org/repo/issues/42"
        assert issue["issue_number"] == "42"
        assert issue["title"] == "[PR Guardian] test issue"
        assert isinstance(issue["finding_ids"], list)
        assert set(issue["finding_ids"]) == set(finding_ids)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_scan_issues_isolates_by_scan():
    """Issues for scan A must not appear for scan B."""
    engine, factory = await _make_session_factory()
    scan_a, scan_b = uuid.uuid4(), uuid.uuid4()
    try:
        with patch("pr_guardian.persistence.storage.async_session", lambda: factory()):
            async with factory() as session:
                for sid in (scan_a, scan_b):
                    await session.execute(
                        sa.text("INSERT INTO scans (id) VALUES (:id)"), {"id": str(sid)}
                    )
                await session.commit()

            await create_scan_issue(
                scan_id=scan_a,
                finding_ids=["f1"],
                issue_url="https://github.com/org/repo/issues/1",
                issue_number="1",
                title="Issue A",
                platform="github",
                repo="org/repo",
            )
            result_b = await get_scan_issues(scan_b)
        assert result_b == []
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

SCAN_ID = str(uuid.uuid4())
FINDING_ID = str(uuid.uuid4())

_MOCK_SCAN = {
    "id": SCAN_ID,
    "repo": "org/repo",
    "platform": "github",
    "scan_type": "recent_changes",
    "agent_results": [
        {
            "agent_name": "security",
            "verdict": "warning",
            "findings": [
                {
                    "id": FINDING_ID,
                    "severity": "high",
                    "certainty": "detected",
                    "category": "SQL Injection",
                    "description": "Unsafe query",
                    "file": "app.py",
                    "line": 42,
                }
            ],
        }
    ],
}


class TestCreateScanIssuesValidation:
    """Input validation returns errors before hitting the platform."""

    def test_bad_mode_returns_400(self, client):
        with patch(
            "pr_guardian.api.scans.storage.get_scan",
            new_callable=AsyncMock,
            return_value=_MOCK_SCAN,
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "all_at_once", "finding_ids": [FINDING_ID]},
            )
        assert resp.status_code == 400

    def test_empty_finding_ids_returns_400(self, client):
        with patch(
            "pr_guardian.api.scans.storage.get_scan",
            new_callable=AsyncMock,
            return_value=_MOCK_SCAN,
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "single", "finding_ids": []},
            )
        assert resp.status_code == 400

    def test_scan_not_found_returns_404(self, client):
        with patch(
            "pr_guardian.api.scans.storage.get_scan", new_callable=AsyncMock, return_value=None
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "single", "finding_ids": [FINDING_ID]},
            )
        assert resp.status_code == 404

    def test_unmatched_finding_ids_returns_400(self, client):
        with patch(
            "pr_guardian.api.scans.storage.get_scan",
            new_callable=AsyncMock,
            return_value=_MOCK_SCAN,
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "single", "finding_ids": [str(uuid.uuid4())]},
            )
        assert resp.status_code == 400


class TestCreateScanIssuesSuccess:
    """Valid requests create issues and persist them."""

    def _mock_github_adapter(self):
        adapter = MagicMock()
        adapter.create_issue = AsyncMock(
            return_value={"number": 99, "url": "https://github.com/org/repo/issues/99"}
        )
        adapter.close = AsyncMock()
        return adapter

    def test_single_mode_creates_one_issue(self, client):
        mock_adapter = self._mock_github_adapter()
        with (
            patch(
                "pr_guardian.api.scans.storage.get_scan",
                new_callable=AsyncMock,
                return_value=_MOCK_SCAN,
            ),
            patch("pr_guardian.api.scans.create_adapter", return_value=mock_adapter),
            patch(
                "pr_guardian.api.scans.storage.create_scan_issue",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "single", "finding_ids": [FINDING_ID]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["created"]) == 1
        assert data["errors"] == []
        mock_adapter.create_issue.assert_awaited_once()

    def test_per_finding_mode_creates_one_issue_per_finding(self, client):
        mock_adapter = self._mock_github_adapter()
        with (
            patch(
                "pr_guardian.api.scans.storage.get_scan",
                new_callable=AsyncMock,
                return_value=_MOCK_SCAN,
            ),
            patch("pr_guardian.api.scans.create_adapter", return_value=mock_adapter),
            patch(
                "pr_guardian.api.scans.storage.create_scan_issue",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "per_finding", "finding_ids": [FINDING_ID]},
            )

        assert resp.status_code == 200
        assert len(resp.json()["created"]) == 1

    def test_all_failures_returns_500(self, client):
        mock_adapter = MagicMock()
        mock_adapter.create_issue = AsyncMock(side_effect=RuntimeError("API down"))
        mock_adapter.close = AsyncMock()
        with (
            patch(
                "pr_guardian.api.scans.storage.get_scan",
                new_callable=AsyncMock,
                return_value=_MOCK_SCAN,
            ),
            patch("pr_guardian.api.scans.create_adapter", return_value=mock_adapter),
        ):
            resp = client.post(
                f"/api/scans/{SCAN_ID}/create-issues",
                json={"mode": "single", "finding_ids": [FINDING_ID]},
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "errors" in detail


class TestListScanIssues:
    """GET /api/scans/{id}/issues returns persisted issues."""

    def test_returns_list(self, client):
        mock_issues = [
            {
                "id": str(uuid.uuid4()),
                "scan_id": SCAN_ID,
                "finding_ids": [FINDING_ID],
                "issue_url": "https://github.com/org/repo/issues/99",
                "issue_number": "99",
                "title": "Test",
                "platform": "github",
                "repo": "org/repo",
                "created_at": "2026-05-06T00:00:00+00:00",
            }
        ]
        with patch(
            "pr_guardian.api.scans.storage.get_scan_issues",
            new_callable=AsyncMock,
            return_value=mock_issues,
        ):
            resp = client.get(f"/api/scans/{SCAN_ID}/issues")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["issue_number"] == "99"

    def test_empty_for_unknown_scan(self, client):
        with patch(
            "pr_guardian.api.scans.storage.get_scan_issues",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(f"/api/scans/{uuid.uuid4()}/issues")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GitHubAdapter.create_issue
# ---------------------------------------------------------------------------


class TestGitHubAdapterCreateIssue:
    def _adapter_with_post(self, *responses):
        adapter = GitHubAdapter(token="test-token")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=list(responses))
        adapter._client = mock_client
        return adapter

    @pytest.mark.asyncio
    async def test_create_issue_returns_number_and_url(self):
        mock_resp = _resp({"number": 7, "html_url": "https://github.com/org/repo/issues/7"})
        adapter = self._adapter_with_post(mock_resp)
        result = await adapter.create_issue(repo="org/repo", title="Bug", body="Details")
        assert result == {"number": 7, "url": "https://github.com/org/repo/issues/7"}

    @pytest.mark.asyncio
    async def test_create_issue_sends_labels(self):
        mock_resp = _resp({"number": 8, "html_url": "https://github.com/org/repo/issues/8"})
        adapter = self._adapter_with_post(mock_resp)
        await adapter.create_issue(
            repo="org/repo", title="Sec", body="Body", labels=["pr-guardian"]
        )
        call_kwargs = adapter._client.post.call_args
        payload = (
            call_kwargs.kwargs.get("json") or call_kwargs.args[1]
            if len(call_kwargs.args) > 1
            else call_kwargs.kwargs["json"]
        )
        assert payload["labels"] == ["pr-guardian"]

    @pytest.mark.asyncio
    async def test_create_issue_raises_on_http_error(self):
        mock_resp = _resp({}, status_code=422)
        adapter = self._adapter_with_post(mock_resp)
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.create_issue(repo="org/repo", title="T", body="B")


# ---------------------------------------------------------------------------
# ADOAdapter.create_work_item
# ---------------------------------------------------------------------------


class TestADOAdapterCreateWorkItem:
    def _adapter_with_post(self, *responses):
        adapter = ADOAdapter(pat="test-pat", org_url="https://dev.azure.com/org")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=list(responses))
        adapter._client = mock_client
        return adapter

    @pytest.mark.asyncio
    async def test_create_work_item_returns_id_and_url(self):
        mock_resp = _resp(
            {
                "id": 123,
                "_links": {"html": {"href": "https://dev.azure.com/org/proj/_workitems/edit/123"}},
            }
        )
        adapter = self._adapter_with_post(mock_resp)
        result = await adapter.create_work_item(
            project="proj", title="Security Bug", body="Details"
        )
        assert result["id"] == 123
        assert "123" in result["url"]

    @pytest.mark.asyncio
    async def test_create_work_item_raises_on_http_error(self):
        mock_resp = _resp({}, status_code=400)
        adapter = self._adapter_with_post(mock_resp)
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.create_work_item(project="proj", title="T", body="B")

    @pytest.mark.asyncio
    async def test_create_work_item_sends_patch_document(self):
        mock_resp = _resp({"id": 5, "_links": {}})
        adapter = self._adapter_with_post(mock_resp)
        await adapter.create_work_item(project="proj", title="My Title", body="My Body")
        call_kwargs = adapter._client.post.call_args
        content = call_kwargs.kwargs.get("content") or call_kwargs.args[1]
        patch_doc = json.loads(content)
        titles = [op["value"] for op in patch_doc if op["path"] == "/fields/System.Title"]
        assert titles == ["My Title"]
