from __future__ import annotations

from http_client_support import patch_httpx_client

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier, Lock
import time

import httpx
import pytest

from evander_azure_devops_addon.pull_requests import AzureDevOpsPrReplyProvider
from evander.services import GitHubPrReplyProvider, PrReplyService, ignored_feedback_ids_for_scope, pr_feedback_scope_key, pr_reply_state_namespace


def test_pr_reply_service_idempotency_reuses_first_response(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.body_texts: list[str] = []

        def post_reply(self, **kwargs: object):
            self.calls += 1
            self.body_texts.append(str(kwargs["body_text"]))
            from evander.models import PostPrFeedbackReplyResponse

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
    assert provider.body_texts == ["done"]


def test_pr_reply_service_trims_feedback_replies_before_posting(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.body_text: str | None = None

        def post_reply(self, **kwargs: object):
            self.body_text = str(kwargs["body_text"])
            from evander.models import PostPrFeedbackReplyResponse

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
        body_text="done",
    )

    assert provider.body_text == "done"


def test_pr_reply_service_records_ignored_feedback_id_for_github_reply(tmp_path: Path) -> None:
    class StubProvider:
        def post_reply(self, **_: object):
            from evander.models import PostPrFeedbackReplyResponse

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
            from evander.models import PostPrFeedbackReplyResponse

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
            from evander.models import PostPrFeedbackReplyResponse

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


@pytest.mark.parametrize("feedback_kind", ["issue_comment", "review"])
def test_github_reply_provider_posts_conversation_comment_for_non_inline_feedback(
    monkeypatch: pytest.MonkeyPatch,
    feedback_kind: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/org/repo/issues/17/comments"
        assert json.loads(request.content.decode("utf-8")) == {"body": "Applied."}
        return httpx.Response(
            201,
            json={"id": 202, "html_url": "https://github.test/conversation/202"},
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubPrReplyProvider(token="token-1", api_base_url="https://api.github.test")

    result = provider.post_reply(
        repo="org/repo",
        pr_id="17",
        thread_id="91",
        comment_id="91",
        feedback_kind=feedback_kind,
        body_text="Applied.",
    )

    assert result.feedback_kind == "issue_comment"
    assert result.reply_id == "202"


def test_pr_reply_service_idempotency_is_process_safe_across_instances(tmp_path: Path) -> None:
    start = Barrier(3)

    class StubProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()

        def post_reply(self, **_: object):
            from evander.models import PostPrFeedbackReplyResponse

            with self.lock:
                self.calls += 1
            claim_payload = json.loads(store_path.read_text(encoding="utf-8"))
            assert [entry["_state"] for entry in claim_payload.values()] == ["claimed"]
            time.sleep(0.05)
            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="github",
                repo="org/repo",
                pr_id="17",
                comment_id="7",
                reply_id="reply-1",
            )

    provider = StubProvider()
    store_path = tmp_path / "idempotency.json"
    services = [
        PrReplyService(providers={"github": provider}, idempotency_store_path=store_path)
        for _ in range(2)
    ]

    def post(service: PrReplyService):
        start.wait()
        return service.post_reply(
            provider="github",
            repo="org/repo",
            pr_id="17",
            thread_id=None,
            comment_id="7",
            body_text="done",
            idempotency_key="shared-key",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(post, service) for service in services]
        start.wait()
        responses = [future.result(timeout=2) for future in futures]

    assert provider.calls == 1
    assert sorted(response.status for response in responses) == ["deduplicated", "posted"]
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(payload) == 1


def test_pr_reply_service_scopes_state_by_resolved_ado_target(tmp_path: Path) -> None:
    class StubProvider:
        def __init__(self) -> None:
            self.calls = 0

        def post_reply(self, **_: object):
            from evander.models import PostPrFeedbackReplyResponse

            self.calls += 1
            return PostPrFeedbackReplyResponse(
                status="posted",
                provider="azure_devops",
                repo="repo-1",
                pr_id="17",
                thread_id="44",
                comment_id="10",
                reply_id="301",
            )

    provider = StubProvider()
    store_path = tmp_path / "idempotency.json"
    ignored_path = tmp_path / "ignored-feedback-ids.json"
    namespaces = [
        pr_reply_state_namespace(
            provider="azure_devops",
            directory_path=f"root/team-{suffix}",
            ado_organization_url=f"https://dev.azure.com/org-{suffix}",
            project="Project",
        )
        for suffix in ("a", "b")
    ]
    services = [
        PrReplyService(
            providers={"azure_devops": provider},
            idempotency_store_path=store_path,
            ignored_feedback_store_path=ignored_path,
            provider_state_namespaces={"azure_devops": namespace},
        )
        for namespace in namespaces
    ]

    first = services[0].post_reply(
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="done",
        idempotency_key="same-key",
    )
    second_scope_key = pr_feedback_scope_key(
        state_namespace=namespaces[1],
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
    )
    assert ignored_feedback_ids_for_scope(
        json.loads(ignored_path.read_text(encoding="utf-8")),
        scope_key=second_scope_key,
    ) == set()
    second = services[1].post_reply(
        provider="azure_devops",
        repo="repo-1",
        pr_id="17",
        thread_id="44",
        comment_id="10",
        body_text="done",
        idempotency_key="same-key",
    )

    assert [first.status, second.status] == ["posted", "posted"]
    assert provider.calls == 2
    assert len(json.loads(store_path.read_text(encoding="utf-8"))) == 2
    ignored_entries = json.loads(ignored_path.read_text(encoding="utf-8"))["feedback_ids"]
    assert {entry["scope_key"] for entry in ignored_entries} == {
        json.dumps(
            [namespace, "azure_devops", "repo-1", "17"],
            separators=(",", ":"),
        )
        for namespace in namespaces
    }


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


def test_azure_reply_provider_posts_reply_with_mentions(
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
        assert request.url.path.endswith("/pullRequests/17/threads/44/comments")
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["content"] == ("@<11111111-1111-1111-1111-111111111111>\n\nHandled.")
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
        mentions=["ada@example.com"],
    )

    assert [request.method for request in requests] == ["GET", "POST"]
    assert result.resolved_mentions[0]["id"] == "11111111-1111-1111-1111-111111111111"


def test_azure_reply_provider_uploads_attachment_and_embeds_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "reply.png"
    image_path.write_bytes(b"png-bytes")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pullRequests/17/attachments/reply.png"):
            assert request.method == "POST"
            assert request.headers["content-type"] == "application/octet-stream"
            assert request.content == b"png-bytes"
            return httpx.Response(
                201,
                json={
                    "url": "https://dev.azure.com/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/reply.png"
                },
            )
        if request.url.path.endswith("/pullRequests/17/threads/44/comments"):
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["content"] == (
                "Handled.\n\n"
                "![reply.png](https://dev.azure.com/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/reply.png)"
            )
            return httpx.Response(
                201,
                json={"id": 301, "_links": {"web": {"href": "https://ado.test/reply/301"}}},
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

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
        attachments=[
            {
                "artifact_id": "comm_123",
                "filename": "reply.png",
                "mime_type": "image/png",
                "byte_count": len(b"png-bytes"),
                "path": str(image_path),
            }
        ],
    )

    assert [request.url.path for request in requests] == [
        "/example/proj/_apis/git/repositories/repo1/pullRequests/17/attachments/reply.png",
        "/example/proj/_apis/git/repositories/repo1/pullRequests/17/threads/44/comments",
    ]
    assert result.uploaded_attachments[0]["filename"] == "reply.png"


def test_azure_reply_provider_normalizes_composite_repo_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/repositories/example-repo/pullRequests/17/threads/44/comments"
        )
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
