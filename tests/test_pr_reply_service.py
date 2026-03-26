from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_sandbox.services import (
    AzureDevOpsPrReplyProvider,
    GitHubPrReplyProvider,
    PrReplyService,
)


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_pr_reply_service_idempotency_reuses_first_response(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.body_texts: list[str] = []

        def post_reply(self, **kwargs: object):
            self.calls += 1
            self.body_texts.append(str(kwargs["body_text"]))
            from agent_sandbox.models import PostPrFeedbackReplyResponse

            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="github",
                repo="org/repo",
                pr_id="17",
                comment_id="7",
                reply_id="reply-1",
                reply_url="https://example.test/reply/1",
            )

    provider = StubProvider()
    service = PrReplyService(
        providers={"github": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    first = service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="7",
        body_text="done",
        idempotency_key="key-1",
    )
    second = service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="7",
        body_text="done",
        idempotency_key="key-1",
    )

    assert first.status == "posted"
    assert second.status == "deduplicated"
    assert provider.calls == 1
    assert provider.body_texts == ["[Truman] done"]


def test_pr_reply_service_prefixes_feedback_replies_once(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.body_text: str | None = None

        def post_reply(self, **kwargs: object):
            self.body_text = str(kwargs["body_text"])
            from agent_sandbox.models import PostPrFeedbackReplyResponse

            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="github",
                repo="org/repo",
                pr_id="17",
                comment_id="7",
                reply_id="reply-1",
                reply_url="https://example.test/reply/1",
            )

    provider = StubProvider()
    service = PrReplyService(
        providers={"github": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="7",
        body_text="[Truman] done",
    )

    assert provider.body_text == "[Truman] done"


def test_pr_reply_service_records_ignored_feedback_id_for_github_reply(tmp_path: Path) -> None:
    class StubProvider:
        def post_reply(self, **_: object):
            from agent_sandbox.models import PostPrFeedbackReplyResponse

            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="github",
                repo="org/repo",
                pr_id="17",
                comment_id="7",
                reply_id="reply-1",
                reply_url="https://example.test/reply/1",
            )

    ignored_path = tmp_path / "ignored-feedback-ids.json"
    service = PrReplyService(
        providers={"github": StubProvider()},
        idempotency_store_path=tmp_path / "idempotency.json",
        ignored_feedback_store_path=ignored_path,
    )

    service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="7",
        body_text="done",
    )

    payload = json.loads(ignored_path.read_text(encoding="utf-8"))
    assert len(payload["feedback_ids"]) == 1
    assert payload["feedback_ids"][0]["feedback_id"] == "github:review_comment:reply-1"
    assert payload["feedback_ids"][0]["ignored_at"].endswith("Z")


def test_pr_reply_service_records_ignored_feedback_id_for_azure_reply(tmp_path: Path) -> None:
    class StubProvider:
        def post_reply(self, **_: object):
            from agent_sandbox.models import PostPrFeedbackReplyResponse

            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="azure_devops",
                repo="repo-1",
                pr_id="17",
                thread_id="44",
                comment_id="10",
                reply_id="301",
                reply_url="https://example.test/reply/301",
            )

    ignored_path = tmp_path / "ignored-feedback-ids.json"
    service = PrReplyService(
        providers={"azure_devops": StubProvider()},
        idempotency_store_path=tmp_path / "idempotency.json",
        ignored_feedback_store_path=ignored_path,
    )

    service.post_reply(
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="done",
    )

    payload = json.loads(ignored_path.read_text(encoding="utf-8"))
    assert len(payload["feedback_ids"]) == 1
    assert payload["feedback_ids"][0]["feedback_id"] == "azure_devops:thread_comment:44:301"
    assert payload["feedback_ids"][0]["ignored_at"].endswith("Z")


def test_pr_reply_service_normalizes_azure_devops_provider_alias(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post_reply(self, **kwargs: object):
            self.calls.append(kwargs)
            from agent_sandbox.models import PostPrFeedbackReplyResponse

            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="azure_devops",
                repo="repo-1",
                pr_id="17",
                thread_id="44",
                comment_id="10",
                reply_id="301",
                reply_url="https://example.test/reply/301",
            )

    provider = StubProvider()
    service = PrReplyService(
        providers={"azure_devops": provider},
        idempotency_store_path=tmp_path / "idempotency.json",
    )

    result = service.post_reply(
        provider="azure-devops",
        repo="repo-1",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="done",
    )

    assert result.provider == "azure_devops"
    assert len(provider.calls) == 1
    assert provider.calls[0]["repo"] == "repo-1"


def test_github_reply_provider_posts_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/org/repo/pulls/17/comments/91/replies"
        payload = {"id": 201, "html_url": "https://github.test/reply/201"}
        return httpx.Response(201, json=payload)

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrReplyProvider(token="token-1", api_base_url="https://api.github.test")
    result = provider.post_reply(
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="91",
        body_text="Applied.",
    )
    assert result.reply_id == "201"
    assert result.reply_url == "https://github.test/reply/201"


def test_azure_reply_provider_posts_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/pullRequests/17/threads/44/comments")
        assert request.url.params["api-version"] == "7.1"
        return httpx.Response(
            201,
            json={"id": 301, "_links": {"web": {"href": "https://ado.test/reply/301"}}},
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrReplyProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )
    result = provider.post_reply(
        repo="repo1",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="Handled.",
    )
    assert result.reply_id == "301"
    assert result.reply_url == "https://ado.test/reply/301"


def test_azure_reply_provider_normalizes_composite_repo_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/repositories/example-repo/pullRequests/17/threads/44/comments")
        return httpx.Response(
            201,
            json={"id": 302, "_links": {"web": {"href": "https://ado.test/reply/302"}}},
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = AzureDevOpsPrReplyProvider(
        organization_url="https://dev.azure.com/example",
        project="proj",
        pat="secret",
    )
    result = provider.post_reply(
        repo="example-org/example project/example-repo",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="Handled.",
    )
    assert result.reply_id == "302"


def test_pr_reply_service_posts_successfully_and_deduplicates_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.path == "/repos/org/repo/pulls/17/comments/91/replies"
        return httpx.Response(201, json={"id": 401, "html_url": "https://github.test/reply/401"})

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = PrReplyService(
        providers={
            "github": GitHubPrReplyProvider(token="token-1", api_base_url="https://api.github.test")
        },
        idempotency_store_path=tmp_path / "pr-reply-idempotency.json",
    )

    first = service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="91",
        body_text="Applied in follow-up commit.",
        idempotency_key="retry-safe-key",
    )
    second = service.post_reply(
        provider="github",
        repo="org/repo",
        pr_id="17",
        thread_id=None,
        comment_id="91",
        body_text="Applied in follow-up commit.",
        idempotency_key="retry-safe-key",
    )

    assert first.status == "posted"
    assert second.status == "deduplicated"
    assert second.reply_id == "401"
    assert request_count == 1
