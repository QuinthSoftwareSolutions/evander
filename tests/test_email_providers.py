from __future__ import annotations


import base64


import json


from datetime import UTC, datetime


from email import message_from_bytes, policy


import httpx


import pytest


from evander.config import GatewayConfig


from evander.email.gmail_provider import GmailEmailProvider


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
            assert 'from:"vendor@example.com"' in query
            assert 'subject:"invoice"' in query
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
