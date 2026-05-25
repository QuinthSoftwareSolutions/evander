from __future__ import annotations

import json

import httpx
import pytest

from agent_sandbox.services import AzureDevOpsPrQueryProvider, GitHubPrQueryProvider, PrQueryService


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_azure_query_provider_constructs_request_with_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/_apis/git/repositories/repo-1/pullrequests")
        assert request.url.params["api-version"] == "7.1"
        assert request.url.params["searchCriteria.status"] == "active"
        assert request.url.params["searchCriteria.creatorId"] == "user-a"
        assert request.url.params["searchCriteria.reviewerId"] == "user-b"
        assert request.url.params["searchCriteria.sourceRefName"] == "refs/heads/feature"
        assert request.url.params["searchCriteria.targetRefName"] == "refs/heads/main"
        assert request.url.params["searchCriteria.sourceRepositoryId"] == "source-repo-id"
        assert request.url.params["searchCriteria.repositoryId"] == "target-repo-id"
        assert request.url.params["searchCriteria.minTime"] == "2026-04-01T00:00:00Z"
        assert request.url.params["searchCriteria.maxTime"] == "2026-04-30T00:00:00Z"
        assert request.url.params["searchCriteria.queryTimeRangeType"] == "created"
        assert request.url.params["searchCriteria.includeLinks"] == "true"
        assert request.url.params["maxCommentLength"] == "250"
        assert request.url.params["$top"] == "25"
        assert request.url.params["$skip"] == "50"
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
        source_ref_name="refs/heads/feature",
        target_ref_name="refs/heads/main",
        source_repository_id="source-repo-id",
        repository_id="target-repo-id",
        min_time="2026-04-01T00:00:00Z",
        max_time="2026-04-30T00:00:00Z",
        query_time_range_type="created",
        include_links=True,
        max_comment_length=250,
        top=25,
        skip=50,
        cursor="cursor-1",
    )

    assert response.next_cursor == "cursor-2"
    assert response.paging["skip"] == 50
    assert len(response.pull_requests) == 1
    assert response.pull_requests[0].pr_id == "17"
    assert response.pull_requests[0].status == "active"


def test_azure_query_provider_prefers_web_url_in_list_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "pullRequestId": 17,
                        "title": "Fix race",
                        "status": "active",
                        "url": "https://dev.azure.com/org/proj/_apis/git/repositories/repo-1/pullRequests/17",
                        "_links": {
                            "web": {
                                "href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"
                            }
                        },
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

    response = provider.list_pull_requests(repo="repo-1")

    assert (
        response.pull_requests[0].url == "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"
    )


def test_pr_query_service_normalizes_provider_and_returns_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_pr_query_service_accepts_ado_provider_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["searchCriteria.status"] == "active"
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

    result = service.list_pull_requests(provider="ado", repo="repo-1", status="active")

    assert result.provider == "azure_devops"


def test_azure_query_provider_filters_tag_expression_after_label_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pullrequests"):
            assert "labels" not in request.url.path
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "value": [
                        {"pullRequestId": 17, "title": "One", "status": "active"},
                        {"pullRequestId": 18, "title": "Two", "status": "active"},
                        {"pullRequestId": 19, "title": "Three", "status": "active"},
                    ],
                },
            )
        if request.url.path.endswith("/pullRequests/17/labels"):
            label_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={"value": [{"name": "TagA"}, {"name": "agent"}]},
            )
        if request.url.path.endswith("/pullRequests/18/labels"):
            label_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={"value": [{"name": "TagB"}, {"name": "TagC"}]},
            )
        if request.url.path.endswith("/pullRequests/19/labels"):
            label_paths.append(request.url.path)
            return httpx.Response(200, json={"value": [{"name": "TagD"}]})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    response = provider.list_pull_requests(
        repo="repo-1",
        status="active",
        tags="(TagA or tagb) and (not TAGC)",
        top=50,
    )

    assert len(label_paths) == 3
    assert response.count == 1
    assert response.pull_requests[0].pr_id == "17"
    assert response.pull_requests[0].tags == ["TagA", "agent"]
    assert response.paging["tag_filter_client_side"] is True


def test_azure_query_provider_rejects_invalid_tag_expression() -> None:
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    with pytest.raises(ValueError, match="missing"):
        provider.list_pull_requests(repo="repo-1", tags="(TagA or TagB")


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
                "reviewers": [
                    {"id": "u1", "displayName": "Reviewer One", "vote": 10, "isRequired": True}
                ],
                "_links": {
                    "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}
                },
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
    assert result.url == "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"
    assert result.raw["customField"]["still"] == "in_raw"


def test_azure_query_provider_get_pull_request_keeps_raw_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "commitId": "abc123",
                            "comment": "message",
                            "url": "https://example/commit/abc123",
                        }
                    ]
                },
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


def test_github_query_provider_get_pull_request_uses_base_payload_without_optional_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/repos/org/repo/pulls/17"
        assert "authorization" not in request.headers
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        return httpx.Response(
            200,
            json={
                "number": 17,
                "title": "Fix race",
                "state": "open",
                "html_url": "https://github.test/org/repo/pull/17",
                "head": {"ref": "feature/fix-race", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "user": {"login": "agent-user"},
                "requested_reviewers": [
                    {"login": "reviewer-one"},
                ],
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrQueryProvider(api_base_url="https://api.github.test")

    result = provider.get_pull_request(repo="org/repo", pr_id="17")

    assert len(requests) == 1
    assert result.provider == "github"
    assert result.title == "Fix race"
    assert result.status == "open"
    assert result.url == "https://github.test/org/repo/pull/17"
    assert result.source_branch == "feature/fix-race"
    assert result.target_branch == "main"
    assert result.source_commit == "abc123"
    assert result.target_commit == "def456"
    assert result.created_by == "agent-user"
    assert [reviewer.reviewer_id for reviewer in result.reviewers] == ["reviewer-one"]
    assert result.reviewers[0].status == "requested"
    assert result.commits == []


def test_github_query_provider_get_pull_request_honors_optional_flags_and_merges_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.url.path in {
            "/repos/org/repo/pulls/17",
            "/repos/org/repo/pulls/17/commits",
            "/repos/org/repo/pulls/17/reviews",
        }
        assert request.headers["authorization"] == "Bearer token-1"
        assert request.headers["accept"] == "application/vnd.github+json"
        if request.url.path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abc123",
                        "html_url": "https://github.test/org/repo/commit/abc123",
                        "commit": {"message": "First commit"},
                    }
                ],
            )
        if request.url.path.endswith("/reviews"):
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "reviewer-one"},
                        "state": "APPROVED",
                        "submitted_at": "2026-05-01T12:00:00Z",
                    },
                    {
                        "user": {"login": "reviewer-one"},
                        "state": "CHANGES_REQUESTED",
                        "submitted_at": "2026-05-01T13:00:00Z",
                    },
                    {
                        "user": {"login": "reviewer-two"},
                        "state": "COMMENTED",
                        "submitted_at": "2026-05-01T14:00:00Z",
                    },
                ],
            )
        return httpx.Response(
            200,
            json={
                "number": 17,
                "title": "Fix race",
                "state": "closed",
                "merged": True,
                "merged_at": "2026-05-01T15:00:00Z",
                "html_url": "https://github.test/org/repo/pull/17",
                "head": {"ref": "feature/fix-race", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "user": {"login": "agent-user"},
                "requested_reviewers": [
                    {"login": "reviewer-two"},
                    {"login": "reviewer-three"},
                ],
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrQueryProvider(
        token="token-1",
        api_base_url="https://api.github.test",
    )

    result = provider.get_pull_request(
        repo="org/repo",
        pr_id="17",
        include_reviewers=True,
        include_commits=True,
    )

    assert requests == [
        "/repos/org/repo/pulls/17",
        "/repos/org/repo/pulls/17/commits",
        "/repos/org/repo/pulls/17/reviews",
    ]
    assert result.status == "merged"
    assert result.commits[0].commit_id == "abc123"
    assert result.commits[0].comment == "First commit"
    assert [reviewer.reviewer_id for reviewer in result.reviewers] == [
        "reviewer-one",
        "reviewer-two",
        "reviewer-three",
    ]
    assert result.reviewers[0].status == "changes_requested"
    assert result.reviewers[1].status == "commented"
    assert result.reviewers[2].status == "requested"
    assert result.raw["commits"][0]["sha"] == "abc123"
    assert len(result.raw["reviews"]) == 3


def test_pr_query_service_rejects_github_listing() -> None:
    provider = GitHubPrQueryProvider(api_base_url="https://api.github.test")
    service = PrQueryService(providers={"github": provider})

    with pytest.raises(ValueError, match="GitHub PR listing is not supported"):
        service.list_pull_requests(provider="github", repo="org/repo")


def test_azure_query_provider_update_pull_request_updates_metadata_and_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17"):
            return httpx.Response(
                200,
                json={
                    "pullRequestId": 17,
                    "title": "Old title",
                    "status": "active",
                    "_links": {
                        "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}
                    },
                },
            )
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/labels"):
            label_get_count = sum(
                1
                for method, path in requests
                if method == "GET" and path.endswith("/pullRequests/17/labels")
            )
            if label_get_count == 1:
                return httpx.Response(
                    200,
                    json={"value": [{"name": "ready"}, {"name": "stale"}]},
                )
            return httpx.Response(
                200,
                json={"value": [{"name": "ready"}, {"name": "reviewed"}]},
            )
        if request.method == "PATCH" and request.url.path.endswith("/pullrequests/17"):
            assert request.url.params["api-version"] == "7.1"
            assert json.loads(request.content.decode("utf-8")) == {
                "title": "New title",
                "description": "New description",
                "status": "abandoned",
            }
            return httpx.Response(
                200,
                json={
                    "pullRequestId": 17,
                    "title": "New title",
                    "status": "abandoned",
                    "_links": {
                        "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}
                    },
                },
            )
        if request.method == "POST" and request.url.path.endswith("/pullRequests/17/labels"):
            assert json.loads(request.content.decode("utf-8")) == {"name": "reviewed"}
            return httpx.Response(200, json={"name": "reviewed"})
        if request.method == "DELETE" and request.url.path.endswith(
            "/pullRequests/17/labels/stale"
        ):
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    result = provider.update_pull_request(
        repo="repo-1",
        pr_id="17",
        title="New title",
        description="New description",
        status="abandoned",
        add_tags=["ready", "reviewed"],
        remove_tags=["stale"],
        expected_statuses=["active"],
    )

    assert result.updated is True
    assert result.skipped is False
    assert result.status == "abandoned"
    assert result.title == "New title"
    assert result.added_tags == ["reviewed"]
    assert result.removed_tags == ["stale"]
    assert result.tags == ["ready", "reviewed"]


def test_azure_query_provider_update_pull_request_skips_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17"):
            return httpx.Response(
                200,
                json={"pullRequestId": 17, "title": "Done", "status": "completed"},
            )
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/labels"):
            return httpx.Response(200, json={"value": [{"name": "ready"}]})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrQueryProvider(
        organization_url="https://dev.azure.com/org",
        project="proj",
        pat="secret",
    )

    result = provider.update_pull_request(
        repo="repo-1",
        pr_id="17",
        title="Should not apply",
        add_tags=["reviewed"],
        expected_statuses=["active"],
    )

    assert result.updated is False
    assert result.skipped is True
    assert result.skipped_reason == "unexpected_status"
    assert result.status == "completed"
    assert methods == ["GET", "GET"]
