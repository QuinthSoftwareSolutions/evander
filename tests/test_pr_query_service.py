from __future__ import annotations

import httpx
import pytest

from agent_sandbox.services import AzureDevOpsPrQueryProvider, PrQueryService


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_azure_query_provider_constructs_request_with_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/_apis/git/repositories/repo-1/pullrequests")
        assert request.url.params["api-version"] == "7.1"
        assert request.url.params["searchCriteria.status"] == "active"
        assert request.url.params["searchCriteria.creatorId"] == "user-a"
        assert request.url.params["searchCriteria.reviewerId"] == "user-b"
        assert request.url.params["$top"] == "25"
        assert request.url.params["continuationToken"] == "cursor-1"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            headers={"x-ms-continuationtoken": "cursor-2"},
            json={
                "value": [
                    {
                        "pullRequestId": 17,
                        "title": "Fix race",
                        "status": "active",
                        "creationDate": "2026-04-10T01:02:03Z",
                        "createdBy": {"displayName": "Agent"},
                        "url": "https://dev.azure.com/org/proj/_apis/git/repositories/repo-1/pullRequests/17",
                    }
                ]
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    response = provider.list_pull_requests(
        repo="repo-1",
        status="active",
        creator_id="user-a",
        reviewer_id="user-b",
        top=25,
        cursor="cursor-1",
    )

    assert response.next_cursor == "cursor-2"
    assert len(response.pull_requests) == 1
    assert response.pull_requests[0].pr_id == "17"
    assert response.pull_requests[0].status == "active"


def test_pr_query_service_normalizes_provider_and_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["searchCriteria.status"] == "completed"
        return httpx.Response(200, json={"value": []})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = PrQueryService(
        providers={
            "azure_devops": AzureDevOpsPrQueryProvider(
                organization_url="https://dev.azure.com/org",
                project="proj",
                pat="secret",
            )
        }
    )

    result = service.list_pull_requests(
        provider="azure-devops",
        repo="repo-1",
        status="completed",
    )

    assert result.pull_requests == []
    assert result.next_cursor is None


def test_azure_query_provider_get_pull_request_maps_fields_and_uses_pat_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        assert request.url.params["api-version"] == "7.1"
        assert request.url.path.endswith("/_apis/git/repositories/repo-1/pullRequests/17")
        return httpx.Response(
            200,
            json={
                "pullRequestId": 17,
                "title": "Fix race",
                "status": "active",
                "sourceRefName": "refs/heads/feature/fix-race",
                "targetRefName": "refs/heads/main",
                "lastMergeSourceCommit": {"commitId": "abc123"},
                "lastMergeTargetCommit": {"commitId": "def456"},
                "createdBy": {"displayName": "Agent"},
                "reviewers": [{"id": "u1", "displayName": "Reviewer One", "vote": 10, "isRequired": True}],
                "_links": {"web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}},
                "customField": {"still": "in_raw"},
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    result = provider.get_pull_request(repo="repo-1", pr_id="17")

    assert len(requests) == 1
    assert result.pr_id == "17"
    assert result.title == "Fix race"
    assert result.status == "active"
    assert result.source_branch == "refs/heads/feature/fix-race"
    assert result.target_branch == "refs/heads/main"
    assert result.source_commit == "abc123"
    assert result.target_commit == "def456"
    assert result.created_by == "Agent"
    assert result.reviewers[0].reviewer_id == "u1"
    assert result.raw["customField"]["still"] == "in_raw"


def test_azure_query_provider_get_pull_request_keeps_raw_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json={"value": [{"commitId": "abc123", "comment": "message", "url": "https://example/commit/abc123"}]},
            )
        return httpx.Response(
            200,
            json={"pullRequestId": 17, "title": "Fix race", "status": "active"},
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    result = provider.get_pull_request(repo="repo-1", pr_id="17", include_commits=True)

    assert result.commits[0].commit_id == "abc123"
    assert isinstance(result.raw.get("commits"), list)
