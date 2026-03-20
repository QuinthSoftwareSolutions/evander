from __future__ import annotations

from agent_sandbox.config import GatewayConfig
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider


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
