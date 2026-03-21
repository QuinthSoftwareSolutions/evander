from __future__ import annotations

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider
from agent_sandbox.email.http_utils import ProviderRequestError


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
                    "auth_secret_refs": {"access_token": "GRAPH_ACCESS_TOKEN"},
                    "folder_mapping": {"inbox": "Inbox", "drafts": "Drafts"},
                },
            ]
        }
    )

    assert config.email_mailboxes[0].mailbox_id == "ops-gmail"
    assert config.email_mailboxes[0].provider == "gmail"
    assert config.email_mailboxes[1].provider == "graph"


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
    monkeypatch.setattr("agent_sandbox.email.http_utils.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")
    patch_provider_client(monkeypatch, "agent_sandbox.email.gmail_provider.httpx.Client", transport)
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
    patch_provider_client(monkeypatch, "agent_sandbox.email.graph_provider.httpx.Client", transport)
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox", "drafts": "Drafts"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    with pytest.raises(ProviderRequestError, match="auth/permission"):
        provider.get_message(mailbox_id="exec-graph", message_id="msg-auth")
    assert attempts["count"] == 1


def test_gmail_create_draft_post_does_not_retry_transient_503(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503, json={"error": "temporary"}, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "token")
    patch_provider_client(monkeypatch, "agent_sandbox.email.gmail_provider.httpx.Client", transport)
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
