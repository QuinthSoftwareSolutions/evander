from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import UTC, datetime
from email import message_from_bytes, policy
from threading import Barrier, Lock
from urllib.parse import parse_qs

import httpx
import pytest

from evander.config import GatewayConfig
from evander.email.gmail_provider import GmailEmailProvider
from evander.email.graph_provider import GraphAccessTokenCache, GraphEmailProvider
from evander.email.http_utils import ProviderRequestError
from evander.email.providers import EmailDraftAttachment, EmailMessageSearchQuery


def patch_provider_client(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        target,
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_gateway_config_supports_provider_mailbox_entries() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {
                    "mailbox_id": "ops-gmail",
                    "provider": "gmail",
                    "account_identifier": "me",
                    "auth_secret_refs": {"access_token": "GMAIL_ACCESS_TOKEN"},
                    "folder_mapping": {"inbox": "INBOX", "drafts": "DRAFT"},
                },
                {
                    "mailbox_id": "exec-graph",
                    "provider": "graph",
                    "account_identifier": "leader@example.com",
                    "auth_secret_refs": {
                        "tenant_id": "GRAPH_TENANT_ID",
                        "client_id": "GRAPH_CLIENT_ID",
                        "client_secret": "GRAPH_CLIENT_SECRET",
                    },
                    "folder_mapping": {"inbox": "Inbox", "drafts": "Drafts"},
                },
            ]
        }
    )

    assert config.email_mailboxes[0].mailbox_id == "ops-gmail"
    assert config.email_mailboxes[0].provider == "gmail"
    assert config.email_mailboxes[1].provider == "graph"
    assert config.email_mailboxes[1].auth_secret_refs == {
        "tenant_id": "GRAPH_TENANT_ID",
        "client_id": "GRAPH_CLIENT_ID",
        "client_secret": "GRAPH_CLIENT_SECRET",
    }


def test_gmail_summary_sets_gmail_thread_metadata() -> None:
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX", "drafts": "DRAFT"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )
    payload = {
        "id": "gmail-msg-1",
        "threadId": "thread-123",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "preview",
        "internalDate": "1700000000000",
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "a@example.com,b@example.com"},
            ]
        },
    }

    summary = provider._to_summary(mailbox_id="ops-gmail", folder="inbox", payload=payload)

    assert summary.thread_id == "thread-123"
    assert summary.metadata["gmail_thread_id"] == "thread-123"


def test_graph_summary_sets_conversation_metadata() -> None:
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )
    payload = {
        "id": "graph-msg-1",
        "conversationId": "conversation-999",
        "isRead": False,
        "bodyPreview": "preview",
        "receivedDateTime": "2026-03-20T10:00:00Z",
        "parentFolderId": "AAMkAG...",
        "from": {"emailAddress": {"address": "sender@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "person@example.com"}}],
    }

    summary = provider._to_summary(mailbox_id="exec-graph", folder="inbox", payload=payload)

    assert summary.thread_id == "conversation-999"
    assert summary.metadata["graph_conversation_id"] == "conversation-999"


def test_gmail_list_messages_translates_filters_and_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            assert request.url.params["pageToken"] == "next-page"
            assert request.url.params.get_list("labelIds") == ["Label_custom"]
            query = request.url.params["q"]
            assert "in:" not in query
            assert "is:read" in query
            assert "from:\"vendor@example.com\"" in query
            assert "subject:\"invoice\"" in query
            assert "has:attachment" in query
            return httpx.Response(200, json={"messages": [{"id": "msg-1"}]}, request=request)
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "threadId": "thread-1",
                "labelIds": ["INBOX"],
                "internalDate": "1774000800000",
                "snippet": "invoice details",
                "payload": {"headers": [{"name": "From", "value": "vendor@example.com"}]},
            },
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.gmail_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "Label_custom"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://gmail.test/gmail/v1",
    )

    page = provider.list_messages(
        mailbox_id="ops-gmail",
        folder="inbox",
        query=EmailMessageSearchQuery(
            received_after=datetime(2026, 3, 20, tzinfo=UTC),
            received_before=datetime(2026, 3, 21, tzinfo=UTC),
            unread=False,
            sender_contains="vendor@example.com",
            subject_contains="invoice",
            text_contains="details",
            has_attachments=True,
        ),
        cursor="next-page",
        limit=10,
    )

    assert [message.message_id for message in page.messages] == ["msg-1"]
    assert page.messages[0].unread is False
    assert page.next_cursor is None


def test_graph_list_messages_translates_filters_and_preserves_next_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/mailFolders/Inbox/messages")
        assert request.url.params["$top"] == "10"
        assert "$filter" not in request.url.params
        assert "$orderby" not in request.url.params
        assert request.url.params["$search"] == (
            '"details AND to:accounts@example.com AND subject:invoice"'
        )
        assert request.headers["ConsistencyLevel"] == "eventual"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "graph-msg-1",
                        "conversationId": "conversation-1",
                        "isRead": False,
                        "bodyPreview": "details",
                        "receivedDateTime": "2026-03-20T10:00:00Z",
                        "hasAttachments": True,
                        "parentFolderId": "Inbox",
                        "from": {"emailAddress": {"address": "sender@example.com"}},
                        "toRecipients": [],
                    },
                    {
                        "id": "graph-msg-read",
                        "conversationId": "conversation-2",
                        "isRead": True,
                        "bodyPreview": "details",
                        "receivedDateTime": "2026-03-20T09:00:00Z",
                        "hasAttachments": True,
                        "parentFolderId": "Inbox",
                        "from": {"emailAddress": {"address": "sender@example.com"}},
                        "toRecipients": [],
                    }
                ],
                "@odata.nextLink": "https://graph.test/v1.0/next-page",
            },
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    page = provider.list_messages(
        mailbox_id="exec-graph",
        folder="inbox",
        query=EmailMessageSearchQuery(
            received_after=datetime(2026, 3, 20, tzinfo=UTC),
            received_before=datetime(2026, 3, 21, tzinfo=UTC),
            unread=True,
            recipient_contains="accounts@example.com",
            subject_contains="invoice",
            text_contains="details",
            has_attachments=True,
        ),
        cursor=None,
        limit=10,
    )

    assert [message.message_id for message in page.messages] == ["graph-msg-1"]
    assert page.next_cursor == "https://graph.test/v1.0/next-page"


def test_graph_client_credentials_acquire_and_cache_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret")
    calls = {"token": 0, "graph": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.test":
            calls["token"] += 1
            assert request.url.path == "/tenant-id/oauth2/v2.0/token"
            form = parse_qs(request.content.decode("utf-8"))
            assert form == {
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
                "scope": ["https://graph.microsoft.com/.default"],
                "grant_type": ["client_credentials"],
            }
            return httpx.Response(
                200,
                json={"access_token": "acquired-token", "expires_in": 3600},
                request=request,
            )
        calls["graph"] += 1
        assert request.headers["Authorization"] == "Bearer acquired-token"
        return httpx.Response(200, json={"value": []}, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={
            "tenant_id": "GRAPH_TENANT_ID",
            "client_id": "GRAPH_CLIENT_ID",
            "client_secret": "GRAPH_CLIENT_SECRET",
        },
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
        oauth_authority_url="https://login.test",
        access_token_cache=GraphAccessTokenCache(),
    )

    for _ in range(2):
        provider.list_messages(
            mailbox_id="exec-graph",
            folder="inbox",
            query=EmailMessageSearchQuery(),
            cursor=None,
            limit=10,
        )

    assert calls == {"token": 1, "graph": 2}


def test_graph_client_credentials_cache_is_shared_across_provider_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_SHARED_TENANT_ID", "shared-tenant-id")
    monkeypatch.setenv("GRAPH_SHARED_CLIENT_ID", "shared-client-id")
    monkeypatch.setenv("GRAPH_SHARED_CLIENT_SECRET", "shared-client-secret")
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        return httpx.Response(
            200,
            json={"access_token": "shared-token", "expires_in": 3600},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider_args = {
        "folder_mapping": {"inbox": "Inbox"},
        "auth_secret_refs": {
            "tenant_id": "GRAPH_SHARED_TENANT_ID",
            "client_id": "GRAPH_SHARED_CLIENT_ID",
            "client_secret": "GRAPH_SHARED_CLIENT_SECRET",
        },
        "timeout_seconds": 5.0,
        "oauth_authority_url": "https://login-shared.test",
    }

    first_provider = GraphEmailProvider(
        account_identifier="first@example.com", **provider_args
    )
    rebuilt_provider = GraphEmailProvider(
        account_identifier="second@example.com", **provider_args
    )

    assert first_provider._access_token() == "shared-token"
    assert rebuilt_provider._access_token() == "shared-token"
    assert token_calls == 1


def test_graph_client_credentials_cache_serializes_concurrent_provider_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret")
    cache = GraphAccessTokenCache()
    barrier = Barrier(8)
    calls_lock = Lock()
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        with calls_lock:
            token_calls += 1
        return httpx.Response(
            200,
            json={"access_token": "concurrent-token", "expires_in": 3600},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    providers = [
        GraphEmailProvider(
            account_identifier=f"mailbox-{index}@example.com",
            folder_mapping={"inbox": "Inbox"},
            auth_secret_refs={
                "tenant_id": "GRAPH_TENANT_ID",
                "client_id": "GRAPH_CLIENT_ID",
                "client_secret": "GRAPH_CLIENT_SECRET",
            },
            timeout_seconds=5.0,
            oauth_authority_url="https://login-concurrent.test",
            access_token_cache=cache,
        )
        for index in range(8)
    ]

    def access_token(provider: GraphEmailProvider) -> str:
        barrier.wait()
        return provider._access_token()

    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        tokens = list(executor.map(access_token, providers))

    assert tokens == ["concurrent-token"] * len(providers)
    assert token_calls == 1


def test_graph_client_credentials_cache_isolated_by_credential_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID_A", "client-id-a")
    monkeypatch.setenv("GRAPH_CLIENT_ID_B", "client-id-b")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret")
    cache = GraphAccessTokenCache()
    issued_client_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        client_id = form["client_id"][0]
        issued_client_ids.append(client_id)
        return httpx.Response(
            200,
            json={"access_token": f"token-for-{client_id}", "expires_in": 3600},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )

    def provider(client_id_ref: str) -> GraphEmailProvider:
        return GraphEmailProvider(
            account_identifier="leader@example.com",
            folder_mapping={"inbox": "Inbox"},
            auth_secret_refs={
                "tenant_id": "GRAPH_TENANT_ID",
                "client_id": client_id_ref,
                "client_secret": "GRAPH_CLIENT_SECRET",
            },
            timeout_seconds=5.0,
            oauth_authority_url="https://login-isolated.test",
            access_token_cache=cache,
        )

    assert provider("GRAPH_CLIENT_ID_A")._access_token() == "token-for-client-id-a"
    assert provider("GRAPH_CLIENT_ID_B")._access_token() == "token-for-client-id-b"
    assert issued_client_ids == ["client-id-a", "client-id-b"]


def test_graph_client_credentials_secret_rotation_invalidates_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret-1")
    issued_secrets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        client_secret = form["client_secret"][0]
        issued_secrets.append(client_secret)
        return httpx.Response(
            200,
            json={
                "access_token": f"token-{len(issued_secrets)}",
                "expires_in": 3600,
            },
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={
            "tenant_id": "GRAPH_TENANT_ID",
            "client_id": "GRAPH_CLIENT_ID",
            "client_secret": "GRAPH_CLIENT_SECRET",
        },
        timeout_seconds=5.0,
        oauth_authority_url="https://login-rotation.test",
        access_token_cache=GraphAccessTokenCache(),
    )

    assert provider._access_token() == "token-1"
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret-2")
    assert provider._access_token() == "token-2"
    assert issued_secrets == ["client-secret-1", "client-secret-2"]


def test_graph_client_credentials_refresh_expired_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret")
    issued_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.test":
            token = f"acquired-token-{len(issued_tokens) + 1}"
            issued_tokens.append(token)
            return httpx.Response(
                200,
                json={"access_token": token, "expires_in": 0},
                request=request,
            )
        assert request.headers["Authorization"] == f"Bearer {issued_tokens[-1]}"
        return httpx.Response(200, json={"value": []}, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={
            "tenant_id": "GRAPH_TENANT_ID",
            "client_id": "GRAPH_CLIENT_ID",
            "client_secret": "GRAPH_CLIENT_SECRET",
        },
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
        oauth_authority_url="https://login.test",
        access_token_cache=GraphAccessTokenCache(),
    )

    provider.list_messages(
        mailbox_id="exec-graph",
        folder="inbox",
        query=EmailMessageSearchQuery(),
        cursor=None,
        limit=10,
    )
    provider.list_messages(
        mailbox_id="exec-graph",
        folder="inbox",
        query=EmailMessageSearchQuery(),
        cursor=None,
        limit=10,
    )

    assert issued_tokens == ["acquired-token-1", "acquired-token-2"]


def test_graph_client_credentials_token_failure_does_not_expose_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "do-not-expose-this-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"}, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={
            "tenant_id": "GRAPH_TENANT_ID",
            "client_id": "GRAPH_CLIENT_ID",
            "client_secret": "GRAPH_CLIENT_SECRET",
        },
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
        oauth_authority_url="https://login.test",
        access_token_cache=GraphAccessTokenCache(),
    )

    with pytest.raises(RuntimeError, match="token acquisition failed") as exc_info:
        provider.list_messages(
            mailbox_id="exec-graph",
            folder="inbox",
            query=EmailMessageSearchQuery(),
            cursor=None,
            limit=10,
        )

    assert "do-not-expose-this-secret" not in str(exc_info.value)


def test_graph_list_messages_orders_supported_structured_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "$search" not in request.url.params
        assert request.url.params["$orderby"] == "receivedDateTime DESC"
        assert request.url.params["$filter"] == (
            "receivedDateTime ge 1900-01-01T00:00:00Z and "
            "isRead eq false and hasAttachments eq true"
        )
        return httpx.Response(200, json={"value": []}, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    page = provider.list_messages(
        mailbox_id="exec-graph",
        folder="inbox",
        query=EmailMessageSearchQuery(unread=True, has_attachments=True),
        cursor=None,
        limit=10,
    )

    assert page.messages == []
    assert page.next_cursor is None


def test_graph_detail_maps_parent_folder_to_policy_folder() -> None:
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "AAMkAGInboxId", "drafts": "AAMkAGDraftsId"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )
    payload = {
        "id": "graph-msg-2",
        "conversationId": "conversation-1000",
        "isRead": False,
        "receivedDateTime": "2026-03-20T10:00:00Z",
        "parentFolderId": "AAMkAGInboxId",
        "from": {"emailAddress": {"address": "sender@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "person@example.com"}}],
        "subject": "Hello",
        "body": {"content": "Body"},
    }

    detail = provider._to_detail(mailbox_id="exec-graph", payload=payload)

    assert detail.folder == "inbox"


def test_graph_detail_maps_parent_folder_case_insensitively() -> None:
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )
    payload = {
        "id": "graph-msg-3",
        "conversationId": "conversation-1001",
        "isRead": False,
        "receivedDateTime": "2026-03-20T10:00:00Z",
        "parentFolderId": "inbox",
        "from": {"emailAddress": {"address": "sender@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "person@example.com"}}],
        "subject": "Hello",
        "body": {"content": "Body"},
    }

    detail = provider._to_detail(mailbox_id="exec-graph", payload=payload)

    assert detail.folder == "inbox"


def test_graph_detail_accepts_expanded_attachments_array() -> None:
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )
    payload = {
        "id": "graph-msg-4",
        "conversationId": "conversation-1002",
        "isRead": False,
        "receivedDateTime": "2026-03-20T10:00:00Z",
        "parentFolderId": "Inbox",
        "from": {"emailAddress": {"address": "sender@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "person@example.com"}}],
        "subject": "Hello",
        "body": {"content": "Body"},
        "attachments": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "att-1",
                "name": "report.pdf",
                "contentType": "application/pdf",
                "size": 7,
            }
        ],
    }

    detail = provider._to_detail(mailbox_id="exec-graph", payload=payload)

    assert detail.attachments[0].attachment_id == "att-1"
    assert detail.attachments[0].filename == "report.pdf"


def test_gmail_retries_transient_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if "messages/msg-123" in str(request.url) and attempts["count"] == 1:
            return httpx.Response(503, json={"error": "temporary"}, request=request)
        return httpx.Response(
            200,
            json={
                "id": "msg-123",
                "threadId": "thread-123",
                "labelIds": ["INBOX", "UNREAD"],
                "internalDate": "1700000000000",
                "snippet": "hello",
                "payload": {"headers": [{"name": "From", "value": "sender@example.com"}]},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("evander.email.http_utils.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")
    patch_provider_client(monkeypatch, "evander.email.gmail_provider.httpx.Client", transport)
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX", "drafts": "DRAFT"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    detail = provider.get_message(mailbox_id="ops-gmail", message_id="msg-123")

    assert detail.message_id == "msg-123"
    assert attempts["count"] == 2


def test_graph_auth_error_fails_fast_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "token")
    patch_provider_client(monkeypatch, "evander.email.graph_provider.httpx.Client", transport)
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    with pytest.raises(ProviderRequestError, match="auth/permission"):
        provider.get_message(mailbox_id="exec-graph", message_id="msg-auth")
    assert attempts["count"] == 1


def test_gmail_create_draft_post_does_not_retry_transient_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503, json={"error": "temporary"}, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")
    patch_provider_client(monkeypatch, "evander.email.gmail_provider.httpx.Client", transport)
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX", "drafts": "DRAFT"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    with pytest.raises(ProviderRequestError, match="non-idempotent method"):
        provider.create_draft(
            mailbox_id="ops-gmail",
            thread_or_message_id=None,
            subject="Hello",
            body="Body",
            recipients=["person@example.com"],
        )
    assert attempts["count"] == 1


def test_gmail_send_draft_posts_to_provider_send_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/users/me/drafts/send")
        assert request.headers["Authorization"] == "Bearer token"
        assert json.loads(request.content.decode("utf-8")) == {"id": "draft-123"}
        return httpx.Response(
            200,
            json={"id": "gmail-message-1", "threadId": "thread-1"},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.gmail_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX", "drafts": "DRAFT"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://gmail.test/gmail/v1",
    )

    result = provider.send_draft(mailbox_id="ops-gmail", draft_id="draft-123")

    assert result.status == "sent"
    assert result.message_id == "gmail-message-1"
    assert result.metadata["gmail_thread_id"] == "thread-1"


def test_graph_send_draft_posts_to_provider_send_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/users/leader@example.com/messages/draft-456/send")
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(202, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    result = provider.send_draft(mailbox_id="exec-graph", draft_id="draft-456")

    assert result.status == "sent"
    assert result.message_id == "draft-456"
    assert result.metadata["graph_message_id"] == "draft-456"


def _gmail_full_message_with_attachment() -> dict:
    return {
        "id": "msg-attach",
        "threadId": "thread-attach",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1700000000000",
        "snippet": "attachment",
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "agent@example.com"},
                {"name": "Subject", "value": "Report"},
            ],
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8"},
                },
                {
                    "partId": "1",
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment; filename=report.pdf"}
                    ],
                    "body": {"attachmentId": "att-1", "size": 7},
                },
            ],
        },
    }


def test_gmail_detail_lists_attachment_metadata() -> None:
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    detail = provider._to_detail(
        mailbox_id="ops-gmail", payload=_gmail_full_message_with_attachment()
    )

    assert detail.attachments[0].attachment_id == "att-1"
    assert detail.attachments[0].filename == "report.pdf"
    assert detail.attachments[0].byte_count == 7
    assert detail.attachments[0].metadata["gmail_part_id"] == "1"


def test_gmail_download_attachment_decodes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages/msg-attach"):
            return httpx.Response(200, json=_gmail_full_message_with_attachment(), request=request)
        if request.url.path.endswith("/messages/msg-attach/attachments/att-1"):
            return httpx.Response(200, json={"data": "cGF5bG9hZA"}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.gmail_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://gmail.test/gmail/v1",
    )

    content = provider.download_attachment("ops-gmail", "msg-attach", "att-1")

    assert content.filename == "report.pdf"
    assert content.content == b"payload"


def test_gmail_download_attachment_rejects_unknown_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gmail_full_message_with_attachment(), request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.gmail_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://gmail.test/gmail/v1",
    )

    with pytest.raises(KeyError):
        provider.download_attachment("ops-gmail", "msg-attach", "missing")


def test_gmail_create_draft_generates_mime_attachment_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "secret-token")
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(request.read() and __import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={"id": "draft-1", "message": {"id": "msg-1", "threadId": "thread-1"}},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.gmail_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GmailEmailProvider(
        account_identifier="me@example.com",
        folder_mapping={},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://gmail.test/gmail/v1",
    )

    draft = provider.create_draft(
        mailbox_id="ops-gmail",
        thread_or_message_id=None,
        subject="Attached",
        body="See attached",
        recipients=["recipient@example.com"],
        attachments=[
            EmailDraftAttachment(filename="note.txt", content=b"hello", mime_type="text/plain")
        ],
    )

    raw = seen_payloads[0]["message"]["raw"]
    decoded = message_from_bytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
        policy=policy.default,
    )
    attachment = next(decoded.iter_attachments())
    assert draft.metadata["attachment_count"] == 1
    assert attachment.get_filename() == "note.txt"
    assert attachment.get_content_type() == "text/plain"
    assert attachment.get_payload(decode=True) == b"hello"


def test_graph_list_and_download_file_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages/msg-1/attachments"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "id": "att-1",
                            "name": "report.xlsx",
                            "contentType": "application/vnd.ms-excel",
                            "size": 4,
                            "isInline": False,
                        }
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/messages/msg-1/attachments/att-1"):
            return httpx.Response(
                200,
                json={
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-1",
                    "name": "report.xlsx",
                    "contentType": "application/vnd.ms-excel",
                    "size": 4,
                    "contentBytes": base64.b64encode(b"book").decode("ascii"),
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    attachments = provider.list_attachments("exec-graph", "msg-1")
    content = provider.download_attachment("exec-graph", "msg-1", "att-1")

    assert attachments[0].filename == "report.xlsx"
    assert content.content == b"book"


def test_graph_download_rejects_non_file_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"@odata.type": "#microsoft.graph.itemAttachment", "id": "att-1"},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    with pytest.raises(ValueError, match="not a fileAttachment"):
        provider.download_attachment("exec-graph", "msg-1", "att-1")


def test_graph_create_draft_embeds_small_attachment_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "secret-token")
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={"id": "draft-1", "conversationId": "conversation-1"},
            request=request,
        )

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    draft = provider.create_draft(
        mailbox_id="exec-graph",
        thread_or_message_id=None,
        subject="Attached",
        body="See attached",
        recipients=["recipient@example.com"],
        attachments=[
            EmailDraftAttachment(filename="note.txt", content=b"hello", mime_type="text/plain")
        ],
    )

    attachment_payload = seen_payloads[0]["attachments"][0]
    assert draft.metadata["attachment_count"] == 1
    assert attachment_payload["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert attachment_payload["contentBytes"] == base64.b64encode(b"hello").decode("ascii")


def test_graph_create_draft_uses_upload_session_for_large_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "secret-token")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"id": "draft-large"}, request=request)
        if request.url.path.endswith("/attachments/createUploadSession"):
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.test/session"},
                request=request,
            )
        if str(request.url) == "https://upload.test/session":
            return httpx.Response(201, json={}, request=request)
        return httpx.Response(404, request=request)

    patch_provider_client(
        monkeypatch,
        "evander.email.graph_provider.httpx.Client",
        httpx.MockTransport(handler),
    )
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        api_base_url="https://graph.test/v1.0",
    )

    draft = provider.create_draft(
        mailbox_id="exec-graph",
        thread_or_message_id=None,
        subject="Large",
        body="See attached",
        recipients=["recipient@example.com"],
        attachments=[
            EmailDraftAttachment(
                filename="large.bin",
                content=b"x" * (GraphEmailProvider._upload_chunk_size + 1),
                mime_type="application/octet-stream",
            )
        ],
    )

    create_payload = __import__("json").loads(requests[0].content)
    upload_puts = [request for request in requests if request.method == "PUT"]
    assert "attachments" not in create_payload
    assert draft.metadata["large_attachment_count"] == 1
    assert GraphEmailProvider._upload_chunk_size % (320 * 1024) == 0
    assert len(upload_puts) == 2
    assert int(upload_puts[0].headers["content-length"]) == (GraphEmailProvider._upload_chunk_size)
    assert upload_puts[0].headers["content-range"] == (
        f"bytes 0-{GraphEmailProvider._upload_chunk_size - 1}/"
        f"{GraphEmailProvider._upload_chunk_size + 1}"
    )
