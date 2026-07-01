from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from evander.services import (
    AzureDevOpsPrPublishProvider,
    GitHubPrPublishProvider,
    PrPublishService,
)


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "evander.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_pr_publish_service_idempotency_reuses_first_comment_response(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls = 0

        def post_comment(self, **_: object) -> dict[str, object]:
            self.calls += 1
            return {
                "status": "posted",
                "provider": "github",
                "repo": "org/repo",
                "pr_id": "17",
                "comment_id": "123",
                "comment_url": "https://example.test/comments/123",
                "published_feedback_ids": ["github:issue_comment:123"],
            }

        def submit_review(self, **_: object) -> dict[str, object]:
            raise AssertionError("submit_review was not expected in this test")

    provider = StubProvider()
    service = PrPublishService(
        providers={"github": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    first = service.post_comment(
        provider="github",
        repo="org/repo",
        pr_id="17",
        body_text="review comment",
        idempotency_key="key-1",
    )
    second = service.post_comment(
        provider="github",
        repo="org/repo",
        pr_id="17",
        body_text="review comment",
        idempotency_key="key-1",
    )

    assert first["status"] == "posted"
    assert second["status"] == "deduplicated"
    assert provider.calls == 1


def test_pr_publish_service_records_ignored_feedback_ids_for_github_review(tmp_path: Path) -> None:
    class StubProvider:
        def post_comment(self, **_: object) -> dict[str, object]:
            raise AssertionError("post_comment was not expected in this test")

        def submit_review(self, **_: object) -> dict[str, object]:
            return {
                "status": "posted",
                "provider": "github",
                "repo": "org/repo",
                "pr_id": "17",
                "review_id": "501",
                "review_url": "https://example.test/reviews/501",
                "comment_ids": ["601", "602"],
                "comment_urls": [
                    "https://example.test/comments/601",
                    "https://example.test/comments/602",
                ],
                "published_feedback_ids": [
                    "github:review:501",
                    "github:review_comment:601",
                    "github:review_comment:602",
                ],
            }

    ignored_path = tmp_path / "ignored-feedback-ids.json"
    service = PrPublishService(
        providers={"github": StubProvider()},
        idempotency_store_path=tmp_path / "idempotency.json",
        ignored_feedback_store_path=ignored_path,
    )

    service.submit_review(
        provider="github",
        repo="org/repo",
        pr_id="17",
        body_text="Summary",
        comments=[{"path": "src/app.py", "line": 17, "body": "fix this"}],
    )

    payload = json.loads(ignored_path.read_text(encoding="utf-8"))
    assert [entry["feedback_id"] for entry in payload["feedback_ids"]] == [
        "github:review:501",
        "github:review_comment:601",
        "github:review_comment:602",
    ]


def test_pr_publish_service_posts_inline_comment_via_review_provider(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post_comment(self, **_: object) -> dict[str, object]:
            raise AssertionError("post_comment was not expected in this test")

        def submit_review(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            return {
                "status": "posted",
                "provider": "azure_devops",
                "repo": "repo-1",
                "pr_id": "17",
                "review_id": None,
                "review_url": "https://example.test/threads/45",
                "comment_ids": ["11"],
                "comment_urls": ["https://example.test/threads/45"],
                "published_feedback_ids": ["azure_devops:thread_comment:45:11"],
            }

    provider = StubProvider()
    service = PrPublishService(
        providers={"azure_devops": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    result = service.post_inline_comment(
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
        path="src/app.py",
        line=17,
        body_text="Fix this line",
        idempotency_key="inline-1",
    )

    assert result["comment_ids"] == ["11"]
    assert provider.calls == [
        {
            "repo": "repo-1",
            "pr_id": "17",
            "body_text": "",
            "comments": [{"path": "src/app.py", "line": 17, "body_text": "Fix this line"}],
            "event": None,
            "attachments": None,
        }
    ]


def test_pr_publish_service_records_ignored_feedback_ids_for_azure_comment(tmp_path: Path) -> None:
    class StubProvider:
        def post_comment(self, **_: object) -> dict[str, object]:
            return {
                "status": "posted",
                "provider": "azure_devops",
                "repo": "repo-1",
                "pr_id": "17",
                "thread_id": "44",
                "comment_id": "10",
                "comment_url": "https://example.test/reply/10",
                "published_feedback_ids": ["azure_devops:thread_comment:44:10"],
            }

        def submit_review(self, **_: object) -> dict[str, object]:
            raise AssertionError("submit_review was not expected in this test")

    ignored_path = tmp_path / "ignored-feedback-ids.json"
    service = PrPublishService(
        providers={"azure_devops": StubProvider()},
        idempotency_store_path=tmp_path / "idempotency.json",
        ignored_feedback_store_path=ignored_path,
    )

    service.post_comment(
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
        body_text="Done.",
    )

    payload = json.loads(ignored_path.read_text(encoding="utf-8"))
    assert [entry["feedback_id"] for entry in payload["feedback_ids"]] == [
        "azure_devops:thread_comment:44:10"
    ]


def test_pr_publish_service_normalizes_azure_devops_provider_alias(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post_comment(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            return {
                "status": "posted",
                "provider": "azure_devops",
                "repo": "repo-1",
                "pr_id": "17",
                "thread_id": "44",
                "comment_id": "10",
                "comment_url": "https://example.test/reply/10",
                "published_feedback_ids": ["azure_devops:thread_comment:44:10"],
            }

        def submit_review(self, **_: object) -> dict[str, object]:
            raise AssertionError("submit_review was not expected in this test")

    provider = StubProvider()
    service = PrPublishService(
        providers={"azure_devops": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    result = service.post_comment(
        provider="azure-devops",
        repo="repo-1",
        pr_id="17",
        body_text="Done.",
    )

    assert result["provider"] == "azure_devops"
    assert len(provider.calls) == 1
    assert provider.calls[0]["repo"] == "repo-1"


def test_github_publish_provider_posts_top_level_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/org/repo/issues/17/comments"
        assert request.content == b'{"body":"Published."}'
        return httpx.Response(201, json={"id": 201, "html_url": "https://github.test/comments/201"})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrPublishProvider(token="token-1", api_base_url="https://api.github.test")
    result = provider.post_comment(repo="org/repo", pr_id="17", body_text="Published.")

    assert result["comment_id"] == "201"
    assert result["published_feedback_ids"] == ["github:issue_comment:201"]


def test_github_publish_provider_submits_review_and_inline_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/org/repo/pulls/17/reviews":
            assert json.loads(request.content.decode("utf-8")) == {
                "body": "Summary",
                "event": "COMMENT",
            }
            return httpx.Response(
                201, json={"id": 301, "html_url": "https://github.test/reviews/301"}
            )
        if request.url.path == "/repos/org/repo/pulls/17/comments":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "body": "Fix this line",
                "path": "src/app.py",
                "line": 17,
            }
            return httpx.Response(
                201, json={"id": 401, "html_url": "https://github.test/comments/401"}
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrPublishProvider(token="token-1", api_base_url="https://api.github.test")
    result = provider.submit_review(
        repo="org/repo",
        pr_id="17",
        body_text="Summary",
        comments=[{"path": "src/app.py", "line": 17, "body": "Fix this line"}],
    )

    assert result["review_id"] == "301"
    assert result["comment_ids"] == ["401"]
    assert result["published_feedback_ids"] == ["github:review:301", "github:review_comment:401"]
    assert len(requests) == 2


def test_azure_publish_provider_posts_top_level_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/pullRequests/17/threads")
        assert request.url.params["api-version"] == "7.1"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "comments": [{"parentCommentId": 0, "content": "Published.", "commentType": "text"}],
            "status": "active",
        }
        return httpx.Response(
            201,
            json={
                "id": 44,
                "comments": [{"id": 10}],
                "_links": {"web": {"href": "https://ado.test/threads/44"}},
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )
    result = provider.post_comment(repo="repo1", pr_id="17", body_text="Published.")

    assert result["thread_id"] == "44"
    assert result["comment_id"] == "10"
    assert result["published_feedback_ids"] == ["azure_devops:thread_comment:44:10"]


def test_azure_publish_provider_posts_top_level_comment_with_mentions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/example/_apis/identities":
            assert request.url.params["filterValue"] == "ada@example.com"
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "11111111-1111-1111-1111-111111111111",
                            "providerDisplayName": "Ada Lovelace",
                            "uniqueName": "ada@example.com",
                        }
                    ]
                },
            )
        assert request.url.path.endswith("/pullRequests/17/threads")
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["comments"][0]["content"] == (
            "@<11111111-1111-1111-1111-111111111111>\n\nPublished."
        )
        return httpx.Response(
            201,
            json={
                "id": 44,
                "comments": [{"id": 10}],
                "_links": {"web": {"href": "https://ado.test/threads/44"}},
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )
    result = provider.post_comment(
        repo="repo1",
        pr_id="17",
        body_text="Published.",
        mentions=["ada@example.com"],
    )

    assert [request.method for request in requests] == ["GET", "POST"]
    assert result["resolved_mentions"][0]["id"] == "11111111-1111-1111-1111-111111111111"


def test_azure_publish_provider_uploads_pr_attachment_and_embeds_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"png-bytes")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pullRequests/17/attachments/shot.png"):
            assert request.headers["content-type"] == "application/octet-stream"
            assert request.content == b"png-bytes"
            return httpx.Response(
                201,
                json={
                    "id": 7,
                    "url": "https://dev.azure.com/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/shot.png",
                },
            )
        if request.url.path.endswith("/pullRequests/17/threads"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["comments"][0]["content"] == (
                "Published.\n\n"
                "![shot.png](https://dev.azure.com/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/shot.png)"
            )
            return httpx.Response(
                201,
                json={
                    "id": 44,
                    "comments": [{"id": 10}],
                    "_links": {"web": {"href": "https://ado.test/threads/44"}},
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    result = provider.post_comment(
        repo="repo1",
        pr_id="17",
        body_text="Published.",
        attachments=[
            {
                "artifact_id": "comm_123",
                "filename": "shot.png",
                "mime_type": "image/png",
                "byte_count": len(b"png-bytes"),
                "path": str(image_path),
            }
        ],
    )

    assert [request.url.path for request in requests] == [
        "/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/shot.png",
        "/example/proj/_apis/git/repositories/repo1/pullRequests/17/threads",
    ]
    assert result["uploaded_attachments"][0]["filename"] == "shot.png"


def test_azure_publish_provider_submits_review_and_inline_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 3}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/3/changes"
        ):
            assert request.url.params["$top"] == "2000"
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "edit",
                            "item": {"path": "/src/app.py"},
                        }
                    ]
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        if (
            request.url.path.endswith("/pullRequests/17/threads")
            and payload["comments"][0]["content"] == "Summary"
        ):
            assert payload == {
                "comments": [{"parentCommentId": 0, "content": "Summary", "commentType": "text"}],
                "status": "active",
            }
            return httpx.Response(
                201,
                json={
                    "id": 44,
                    "comments": [{"id": 10}],
                    "_links": {"web": {"href": "https://ado.test/threads/44"}},
                },
            )
        if request.url.path.endswith("/pullRequests/17/threads"):
            assert payload == {
                "comments": [
                    {"parentCommentId": 0, "content": "Fix this line", "commentType": "text"}
                ],
                "status": "active",
                "threadContext": {
                    "filePath": "/src/app.py",
                    "rightFileStart": {"line": 17, "offset": 1},
                    "rightFileEnd": {"line": 17, "offset": 1},
                },
                "pullRequestThreadContext": {
                    "changeTrackingId": 77,
                    "iterationContext": {
                        "firstComparingIteration": 1,
                        "secondComparingIteration": 3,
                    },
                },
            }
            return httpx.Response(
                201,
                json={
                    "id": 45,
                    "comments": [{"id": 11}],
                    "_links": {"web": {"href": "https://ado.test/threads/45"}},
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )
    result = provider.submit_review(
        repo="repo1",
        pr_id="17",
        body_text="Summary",
        comments=[{"path": "src/app.py", "line": 17, "body": "Fix this line"}],
    )

    assert result["summary_thread_id"] == "44"
    assert result["comment_ids"] == ["10", "11"]
    assert result["published_feedback_ids"] == [
        "azure_devops:thread_comment:44:10",
        "azure_devops:thread_comment:45:11",
    ]
    post_requests = [request for request in requests if request.method == "POST"]
    assert len(post_requests) == 2
    payloads = [json.loads(request.content.decode("utf-8")) for request in post_requests]
    assert payloads[0]["comments"][0]["content"] == "Fix this line"
    assert payloads[1]["comments"][0]["content"] == "Summary"


def test_azure_publish_provider_submits_review_with_summary_and_inline_mentions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def identity_payload(user_id: str, display_name: str, unique_name: str) -> dict[str, object]:
        return {
            "value": [
                {
                    "id": user_id,
                    "providerDisplayName": display_name,
                    "uniqueName": unique_name,
                }
            ]
        }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/example/_apis/identities":
            if request.url.params.get("filterValue") == "ada@example.com":
                return httpx.Response(
                    200,
                    json=identity_payload(
                        "11111111-1111-1111-1111-111111111111",
                        "Ada Lovelace",
                        "ada@example.com",
                    ),
                )
            if request.url.params.get("filterValue") == "grace@example.com":
                return httpx.Response(
                    200,
                    json=identity_payload(
                        "22222222-2222-2222-2222-222222222222",
                        "Grace Hopper",
                        "grace@example.com",
                    ),
                )
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 3}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/3/changes"
        ):
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "edit",
                            "item": {"path": "/src/app.py"},
                        }
                    ]
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        if payload["comments"][0]["content"].endswith("Inline"):
            assert payload["comments"][0]["content"] == (
                "@<22222222-2222-2222-2222-222222222222>\n\nInline"
            )
            return httpx.Response(
                201,
                json={
                    "id": 45,
                    "comments": [{"id": 11}],
                    "_links": {"web": {"href": "https://ado.test/threads/45"}},
                },
            )
        assert payload["comments"][0]["content"] == (
            "@<11111111-1111-1111-1111-111111111111>\n\nSummary"
        )
        return httpx.Response(
            201,
            json={
                "id": 44,
                "comments": [{"id": 10}],
                "_links": {"web": {"href": "https://ado.test/threads/44"}},
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    result = provider.submit_review(
        repo="repo1",
        pr_id="17",
        body_text="Summary",
        mentions=["ada@example.com"],
        comments=[
            {
                "path": "src/app.py",
                "line": 4,
                "body_text": "Inline",
                "mentions": ["grace@example.com"],
            }
        ],
    )

    assert result["resolved_mentions"][0]["id"] == "11111111-1111-1111-1111-111111111111"
    assert result["inline_resolved_mentions"][0][0]["id"] == (
        "22222222-2222-2222-2222-222222222222"
    )


def test_azure_publish_provider_failed_inline_comment_does_not_post_summary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 2}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/2/changes"
        ):
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "edit",
                            "item": {"path": "/src/app.py"},
                        },
                        {
                            "changeTrackingId": 78,
                            "changeType": "edit",
                            "item": {"path": "/src/other.py"},
                        },
                    ]
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["comments"][0]["content"] != "Summary"
        return httpx.Response(
            400,
            json={
                "message": "The pull request thread position is invalid.",
                "typeKey": "GitPullRequestCommentPositionDoesNotExistException",
                "errorCode": 0,
                "eventId": 3000,
            },
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    caplog.set_level("WARNING", logger="evander.services")
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        provider.submit_review(
            repo="repo1",
            pr_id="17",
            body_text="Summary",
            comments=[
                {"path": "src/app.py", "line": 17, "body": "Fix this line"},
                {"path": "src/other.py", "line": 23, "body": "Fix this too"},
            ],
        )

    post_requests = [request for request in requests if request.method == "POST"]
    assert len(post_requests) == 1
    error_text = str(exc_info.value)
    assert "inline comment 1/2 path=src/app.py line=17" in error_text
    assert "The pull request thread position is invalid." in error_text
    assert "GitPullRequestCommentPositionDoesNotExistException" in error_text
    assert "Posted 0 of 2 inline comment(s); summary comment was not posted." in error_text
    assert "The pull request thread position is invalid." in caplog.text


def test_azure_publish_provider_reports_failed_inline_after_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 2}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/2/changes"
        ):
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "edit",
                            "item": {"path": "/src/app.py"},
                        },
                        {
                            "changeTrackingId": 78,
                            "changeType": "edit",
                            "item": {"path": "/src/other.py"},
                        },
                    ]
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        content = payload["comments"][0]["content"]
        if content == "First finding":
            return httpx.Response(
                201,
                json={
                    "id": 45,
                    "comments": [{"id": 11}],
                    "_links": {"web": {"href": "https://ado.test/threads/45"}},
                },
                request=request,
            )
        assert content == "Second finding"
        return httpx.Response(
            400,
            json={"message": "Specified pull request thread position does not exist."},
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        provider.submit_review(
            repo="repo1",
            pr_id="17",
            body_text="Summary",
            comments=[
                {"path": "src/app.py", "line": 17, "body": "First finding"},
                {"path": "src/other.py", "line": 23, "body": "Second finding"},
            ],
        )

    post_requests = [request for request in requests if request.method == "POST"]
    assert len(post_requests) == 2
    posted_contents = [
        json.loads(request.content.decode("utf-8"))["comments"][0]["content"]
        for request in post_requests
    ]
    assert posted_contents == [
        "First finding",
        "Second finding",
    ]
    error_text = str(exc_info.value)
    assert "inline comment 2/2 path=src/other.py line=23" in error_text
    assert "Posted 1 of 2 inline comment(s); summary comment was not posted." in error_text


def test_azure_publish_provider_maps_left_side_inline_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 2}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/2/changes"
        ):
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "delete",
                            "item": {"path": "/src/app.py"},
                        }
                    ]
                },
            )
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "comments": [
                {"parentCommentId": 0, "content": "Removed path is wrong", "commentType": "text"}
            ],
            "status": "active",
            "threadContext": {
                "filePath": "/src/app.py",
                "leftFileStart": {"line": 14, "offset": 1},
                "leftFileEnd": {"line": 17, "offset": 1},
            },
            "pullRequestThreadContext": {
                "changeTrackingId": 77,
                "iterationContext": {
                    "firstComparingIteration": 1,
                    "secondComparingIteration": 2,
                },
            },
        }
        return httpx.Response(
            201,
            json={
                "id": 45,
                "comments": [{"id": 11}],
                "_links": {"web": {"href": "https://ado.test/threads/45"}},
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    result = provider.submit_review(
        repo="repo1",
        pr_id="17",
        body_text="",
        comments=[
            {
                "path": "src/app.py",
                "start_line": 14,
                "line": 17,
                "side": "LEFT",
                "body": "Removed path is wrong",
            }
        ],
    )

    assert result["comment_ids"] == ["11"]


def test_azure_publish_provider_prevalidates_inline_paths_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pullRequests/17/iterations"):
            return httpx.Response(200, json={"value": [{"id": 1}, {"id": 2}]})
        if request.method == "GET" and request.url.path.endswith(
            "/pullRequests/17/iterations/2/changes"
        ):
            return httpx.Response(
                200,
                json={
                    "changeEntries": [
                        {
                            "changeTrackingId": 77,
                            "changeType": "edit",
                            "item": {"path": "/src/app.py"},
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrPublishProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )

    with pytest.raises(ValueError, match="not present in the latest PR iteration"):
        provider.submit_review(
            repo="repo1",
            pr_id="17",
            body_text="Summary",
            comments=[
                {"path": "src/app.py", "line": 17, "body": "First finding"},
                {"path": "src/missing.py", "line": 23, "body": "Second finding"},
            ],
        )

    assert [request.method for request in requests] == ["GET", "GET"]


def test_github_publish_provider_rejects_ado_mentions(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_httpx_client(
        monkeypatch,
        httpx.MockTransport(lambda request: pytest.fail(f"unexpected request: {request.url}")),
    )
    provider = GitHubPrPublishProvider(token="token")

    with pytest.raises(ValueError, match="GitHub PR comments do not support ADO mentions"):
        provider.post_comment(
            repo="org/repo",
            pr_id="17",
            body_text="Published.",
            mentions=["ada@example.com"],
        )
