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
