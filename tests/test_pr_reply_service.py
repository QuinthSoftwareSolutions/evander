from __future__ import annotations

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

        def post_reply(self, **_: object):
            self.calls += 1
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
