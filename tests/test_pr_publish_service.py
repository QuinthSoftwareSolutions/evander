from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_sandbox.services import (
    AzureDevOpsPrPublishProvider,
    GitHubPrPublishProvider,
    PrPublishService,
)


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
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
            return httpx.Response(201, json={"id": 301, "html_url": "https://github.test/reviews/301"})
        if request.url.path == "/repos/org/repo/pulls/17/comments":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "body": "Fix this line",
                "path": "src/app.py",
                "line": 17,
            }
            return httpx.Response(201, json={"id": 401, "html_url": "https://github.test/comments/401"})
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


def test_azure_publish_provider_submits_review_and_inline_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        if request.url.path.endswith("/pullRequests/17/threads") and payload["comments"][0]["content"] == "Summary":
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
                "comments": [{"parentCommentId": 0, "content": "Fix this line", "commentType": "text"}],
                "status": "active",
                "threadContext": {
                    "filePath": "src/app.py",
                    "rightFileStart": {"line": 17},
                    "rightFileEnd": {"line": 17},
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
    assert len(requests) == 2


def test_azure_publish_provider_maps_left_side_inline_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "comments": [{"parentCommentId": 0, "content": "Removed path is wrong", "commentType": "text"}],
            "status": "active",
            "threadContext": {
                "filePath": "src/app.py",
                "leftFileStart": {"line": 14},
                "leftFileEnd": {"line": 17},
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
