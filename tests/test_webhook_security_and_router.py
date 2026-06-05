from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from pr_guardian.main import app


def _github_payload() -> dict:
    return {
        "action": "opened",
        "repository": {
            "full_name": "octo/service",
            "clone_url": "https://github.com/octo/service.git",
            "owner": {"login": "octo"},
        },
        "pull_request": {
            "number": 42,
            "head": {"ref": "feature", "sha": "sha1"},
            "base": {"ref": "main"},
            "user": {"login": "alice"},
            "title": "Feature",
        },
    }


def _github_issue_comment_payload() -> dict:
    return {
        "action": "created",
        "repository": {
            "full_name": "octo/service",
            "clone_url": "https://github.com/octo/service.git",
            "owner": {"login": "octo"},
        },
        "issue": {
            "number": 42,
            "pull_request": {"url": "https://api.github.com/repos/octo/service/pulls/42"},
        },
        "comment": {
            "id": 9001,
            "body": "@pr-guardian re-review",
            "user": {"login": "alice"},
            "author_association": "MEMBER",
        },
    }


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_and_ado_webhooks_require_valid_secrets(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WEBHOOK_DEV_BYPASS", raising=False)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
    monkeypatch.setenv("ADO_WEBHOOK_SECRET", "ado-secret")
    github_body = json.dumps(_github_payload()).encode()
    ado_payload = {
        "eventType": "git.pullrequest.created",
        "resourceContainers": {"collection": {"baseUrl": "https://dev.azure.com/acme"}},
        "resource": {
            "pullRequestId": 42,
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
            "title": "Feature",
            "createdBy": {"uniqueName": "alice"},
            "lastMergeSourceCommit": {"commitId": "sha1"},
            "repository": {
                "name": "service",
                "remoteUrl": "https://dev.azure.com/acme/Proj/_git/service",
                "project": {"name": "Proj"},
            },
        },
    }

    with (
        patch(
            "pr_guardian.core.readiness.create_or_update_candidate_from_pr",
            new_callable=AsyncMock,
            return_value={"id": "candidate"},
        ) as create_candidate,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        missing_github = client.post(
            "/api/webhooks/github",
            content=github_body,
            headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
        )
        assert missing_github.status_code == 401

        invalid_github = client.post(
            "/api/webhooks/github",
            content=github_body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=bad",
                "Content-Type": "application/json",
            },
        )
        assert invalid_github.status_code == 401

        valid_github = client.post(
            "/api/webhooks/github",
            content=github_body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _signature(github_body, "github-secret"),
                "Content-Type": "application/json",
            },
        )
        assert valid_github.status_code == 200
        assert valid_github.json()["status"] == "candidate"

        missing_ado = client.post("/api/webhooks/ado", json=ado_payload)
        assert missing_ado.status_code == 401

        invalid_ado = client.post(
            "/api/webhooks/ado",
            json=ado_payload,
            headers={"X-ADO-Webhook-Token": "bad"},
        )
        assert invalid_ado.status_code == 401

        valid_ado = client.post(
            "/api/webhooks/ado",
            json=ado_payload,
            headers={"X-ADO-Webhook-Token": "ado-secret"},
        )
        assert valid_ado.status_code == 200
        assert valid_ado.json()["status"] == "candidate"

        assert create_candidate.await_count == 2
        assert create_candidate.await_args_list[0].kwargs["base_url"] == "http://testserver"
        assert create_candidate.await_args_list[1].kwargs["base_url"] == "http://testserver"


def test_github_issue_comment_webhook_routes_chatops_command(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WEBHOOK_DEV_BYPASS", raising=False)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
    body = json.dumps(_github_issue_comment_payload()).encode()

    with (
        patch(
            "pr_guardian.core.github_chatops.handle_github_comment",
            new_callable=AsyncMock,
            return_value={"status": "queued", "review_id": "review-1"},
        ) as handle_comment,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": _signature(body, "github-secret"),
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    handle_comment.assert_awaited_once()
    assert handle_comment.call_args.kwargs["repo"] == "octo/service"
    assert handle_comment.call_args.kwargs["pr_id"] == "42"
    assert handle_comment.call_args.kwargs["body"] == "@pr-guardian re-review"
