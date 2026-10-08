from __future__ import annotations

import json

import httpx
import pytest

from http_client_support import patch_httpx_client

from evander.services import AzureDevOpsPrPublishProvider, GitHubPrPublishProvider


def ado_provider() -> AzureDevOpsPrPublishProvider:
    return AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="Project One",
        pat="synthetic-pat",
    )


@pytest.mark.parametrize("status_code", [200, 204])
def test_ado_deletes_only_the_selected_pr_comment(monkeypatch, status_code) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status_code)

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    result = ado_provider().delete_comment(
        repo="repo-1", pr_id="17", thread_id="45", comment_id="3"
    )
    assert result["deleted"] is True
    assert (result["thread_id"], result["comment_id"]) == ("45", "3")
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.raw_path == (
        b"/example/Project%20One/_apis/git/repositories/repo-1/"
        b"pullRequests/17/threads/45/comments/3?api-version=7.1"
    )
    assert requests[0].headers["authorization"] == "Basic OnN5bnRoZXRpYy1wYXQ="


@pytest.mark.parametrize("thread_status", ["fixed", 2])
def test_ado_resolves_the_thread_without_replacing_comments(monkeypatch, thread_status) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 45, "status": thread_status})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    result = ado_provider().resolve_comment(repo="repo-1", pr_id="17", thread_id="45")
    assert result["resolved"] is True
    assert result["thread_status"] == "fixed"
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path.endswith("/pullRequests/17/threads/45")
    assert json.loads(requests[0].content) == {"status": "fixed"}


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 45, "status": "active"},
        {"id": 46, "status": "fixed"},
        {"id": 45, "status": "fixed", "isDeleted": True},
    ],
)
def test_ado_does_not_report_an_unconfirmed_resolution(monkeypatch, payload) -> None:
    patch_httpx_client(
        monkeypatch, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(ValueError, match="did not confirm"):
        ado_provider().resolve_comment(repo="repo-1", pr_id="17", thread_id="45")


@pytest.mark.parametrize("status_code", [401, 403, 404, 500])
@pytest.mark.parametrize("operation", ["delete_comment", "resolve_comment"])
def test_ado_comment_mutations_propagate_provider_failures(
    monkeypatch, status_code, operation
) -> None:
    patch_httpx_client(
        monkeypatch,
        httpx.MockTransport(lambda _: httpx.Response(status_code, json={"message": "Failed"})),
    )
    arguments = {"repo": "repo-1", "pr_id": "17", "thread_id": "45"}
    if operation == "delete_comment":
        arguments["comment_id"] = "3"
    with pytest.raises(httpx.HTTPStatusError):
        getattr(ado_provider(), operation)(**arguments)


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("delete_comment", {"comment_id": "3"}),
        ("delete_comment", {"comment_id": "3", "thread_id": "../45"}),
        ("delete_comment", {"comment_id": "0", "thread_id": "45"}),
        (
            "delete_comment",
            {"comment_id": "3", "thread_id": "45", "feedback_kind": "issue_comment"},
        ),
        ("resolve_comment", {"comment_id": "3"}),
        ("resolve_comment", {"thread_id": "0"}),
    ],
)
def test_ado_rejects_invalid_comment_targets_before_network_io(
    monkeypatch, operation, arguments
) -> None:
    def handle(request):
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(ValueError):
        getattr(ado_provider(), operation)(repo="repo-1", pr_id="17", **arguments)


@pytest.mark.parametrize("kind", ["issue_comment", "review_comment"])
def test_github_deletion_verifies_the_pr_and_uses_the_correct_comment_endpoint(
    monkeypatch, kind
) -> None:
    requests = []
    category = "issues" if kind == "issue_comment" else "pulls"
    owner_key = "issue_url" if kind == "issue_comment" else "pull_request_url"

    def handle(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/comments/123"):
            return httpx.Response(
                200, json={owner_key: f"https://api.github.com/repos/org/repo/{category}/17"}
            )
        assert request.url.path == "/repos/org/repo/pulls/17"
        return httpx.Response(200, json={"number": 17})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    result = GitHubPrPublishProvider(token="synthetic-token").delete_comment(
        repo="org/repo", pr_id="17", comment_id="123", feedback_kind=kind
    )
    assert result["deleted"] is True
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", f"/repos/org/repo/{category}/comments/123"),
        *([("GET", "/repos/org/repo/pulls/17")] if category == "issues" else []),
        ("DELETE", f"/repos/org/repo/{category}/comments/123"),
    ]
    assert all(r.headers["authorization"] == "Bearer synthetic-token" for r in requests)


@pytest.mark.parametrize("kind", ["issue_comment", "review_comment"])
def test_github_does_not_delete_a_comment_on_a_different_pr(monkeypatch, kind) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        key = "issue_url" if kind == "issue_comment" else "pull_request_url"
        category = "issues" if kind == "issue_comment" else "pulls"
        return httpx.Response(
            200, json={key: f"https://api.github.com/repos/org/repo/{category}/18"}
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="does not belong"):
        GitHubPrPublishProvider(token="synthetic-token").delete_comment(
            repo="org/repo", pr_id="17", comment_id="123", feedback_kind=kind
        )
    assert [r.method for r in requests] == ["GET"]


def test_github_does_not_delete_a_comment_on_an_ordinary_issue(monkeypatch) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/comments/123"):
            return httpx.Response(
                200, json={"issue_url": "https://api.github.com/repos/org/repo/issues/17"}
            )
        return httpx.Response(404)

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        GitHubPrPublishProvider(token="synthetic-token").delete_comment(
            repo="org/repo", pr_id="17", comment_id="123", feedback_kind="issue_comment"
        )
    assert all(r.method == "GET" for r in requests)


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


@pytest.mark.parametrize("pagination", ["threads", "comments"])
def test_github_resolves_review_comments_beyond_the_first_page(monkeypatch, pagination) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pull_request_url": "https://api.github.com/repos/org/repo/pulls/17",
                    "node_id": "PRRC_target",
                },
            )
        body = json.loads(request.content)
        variables = body["variables"]
        if "mutation" in body["query"]:
            assert variables == {"id": "PRRT_target"}
            data = {"resolveReviewThread": {"thread": {"id": "PRRT_target", "isResolved": True}}}
        elif "owner" in variables:
            first_page = variables["after"] is None
            comments = connection(
                [{"id": "PRRC_other"}] if first_page else [{"id": "PRRC_target"}],
                "comments-next" if pagination == "comments" else None,
            )
            threads = connection(
                [{"id": "PRRT_target", "isResolved": False, "comments": comments}],
                "threads-next" if pagination == "threads" and first_page else None,
            )
            data = {"repository": {"pullRequest": {"reviewThreads": threads}}}
        else:
            assert variables == {"id": "PRRT_target", "after": "comments-next"}
            data = {"node": {"comments": connection([{"id": "PRRC_target"}])}}
        return httpx.Response(200, json={"data": data})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    result = GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
        repo="org/repo", pr_id="17", comment_id="123"
    )
    assert result["thread_id"] == "PRRT_target"
    assert result["resolved"] is True
    assert result["already_resolved"] is False
    assert len(requests) == 4
    assert all(r.url.path == "/graphql" for r in requests if r.method == "POST")


@pytest.mark.parametrize(
    "wrong_target", [{"number": 18}, {"repository": {"nameWithOwner": "org/other"}}]
)
def test_github_resolution_checks_thread_ownership_before_mutating(
    monkeypatch, wrong_target
) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "node": {
                        "id": "PRRT_target",
                        "isResolved": False,
                        "pullRequest": {
                            "number": 17,
                            "repository": {"nameWithOwner": "org/repo"},
                            **wrong_target,
                        },
                    }
                }
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="does not belong"):
        GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
            repo="org/repo", pr_id="17", thread_id="PRRT_target"
        )
    assert len(requests) == 1
    assert "mutation" not in json.loads(requests[0].content)["query"]


def test_github_already_resolved_thread_is_reported_without_another_mutation(monkeypatch) -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "node": {
                        "id": "PRRT_target",
                        "isResolved": True,
                        "pullRequest": {"number": 17, "repository": {"nameWithOwner": "org/repo"}},
                    }
                }
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    result = GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
        repo="org/repo", pr_id="17", thread_id="PRRT_target"
    )
    assert result["resolved"] is True
    assert result["already_resolved"] is True
    assert len(requests) == 1


def test_github_graphql_errors_do_not_report_success_even_with_partial_data(monkeypatch) -> None:
    patch_httpx_client(
        monkeypatch,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "errors": [{"message": "Forbidden"}],
                    "data": {"node": {"id": "PRRT_target"}},
                },
            )
        ),
    )
    with pytest.raises(ValueError, match="GraphQL request failed"):
        GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
            repo="org/repo", pr_id="17", thread_id="PRRT_target"
        )


@pytest.mark.parametrize("confirmed", [False, None])
def test_github_does_not_report_success_without_confirmed_thread_resolution(
    monkeypatch, confirmed
) -> None:
    def handle(request):
        query = json.loads(request.content)["query"]
        if "mutation" in query:
            data = {
                "resolveReviewThread": {"thread": {"id": "PRRT_target", "isResolved": confirmed}}
            }
        else:
            data = {
                "node": {
                    "id": "PRRT_target",
                    "isResolved": False,
                    "pullRequest": {"number": 17, "repository": {"nameWithOwner": "org/repo"}},
                }
            }
        return httpx.Response(200, json={"data": data})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="did not confirm"):
        GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
            repo="org/repo", pr_id="17", thread_id="PRRT_target"
        )


@pytest.mark.parametrize("cursor_mode", ["repeated", "unbounded"])
def test_github_thread_lookup_stops_when_pagination_cannot_complete(
    monkeypatch, cursor_mode
) -> None:
    graphql_calls = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pull_request_url": "https://api.github.com/repos/org/repo/pulls/17",
                    "node_id": "PRRC_target",
                },
            )
        body = json.loads(request.content)
        graphql_calls.append(body)
        assert "mutation" not in body["query"]
        cursor = "same" if cursor_mode == "repeated" else str(len(graphql_calls))
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": connection([], cursor),
                        }
                    }
                }
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="non-advancing|request limit"):
        GitHubPrPublishProvider(token="synthetic-token").resolve_comment(
            repo="org/repo", pr_id="17", comment_id="123"
        )
    assert len(graphql_calls) == (2 if cursor_mode == "repeated" else 100)
