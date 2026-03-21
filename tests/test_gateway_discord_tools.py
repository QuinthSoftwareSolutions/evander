from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.config import GatewayConfig
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.email.providers import EmailDraft, EmailMessageDetail, EmailMessageSummary
from agent_sandbox.gateway import create_server
from agent_sandbox.models import (
    DiscordCommandEnvelope,
    SessionManifest,
    ToolName,
)
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import build_runtime


class FakeDiscordService:
    def __init__(self, commands: list[DiscordCommandEnvelope]) -> None:
        self.commands = commands
        self.last_since_cursor: str | None = None
        self.post_calls: list[tuple[str, str, str]] = []

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        self.last_since_cursor = since_cursor
        return self.commands

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, str]:
        self.post_calls.append((message, channel_id, session_id))
        return {
            "status": "posted",
            "message_id": "discord-msg-1",
            "channel_id": channel_id,
            "session_id": session_id,
        }


def make_context(tmp_path: Path):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    return build_runtime(config)


def read_audit_events(path: Path) -> list[dict[str, object]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def test_discord_get_commands_passes_since_cursor_and_filters_unallowlisted_sources(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(
        commands=[
            DiscordCommandEnvelope(
                command_id="cmd-1",
                author_id="user-1",
                channel_id="chan-allowlisted",
                content="!status",
                metadata={
                    "source": "discord_bot_api",
                    "channel_id": "chan-allowlisted",
                    "author_id": "user-1",
                },
            ),
            DiscordCommandEnvelope(
                command_id="cmd-2",
                author_id="user-2",
                channel_id="chan-blocked",
                content="!status",
                metadata={
                    "source": "discord_bot_api",
                    "channel_id": "chan-blocked",
                    "author_id": "user-2",
                },
            ),
        ]
    )
    context.discord_service = fake_discord
    server = create_server(context)

    result = call_tool(
        server,
        "discord_get_commands",
        {"session_id": "session-1", "since_cursor": "cursor-42"},
    )

    assert fake_discord.last_since_cursor == "cursor-42"
    assert isinstance(result, dict)
    assert [entry["command_id"] for entry in result["commands"]] == ["cmd-1"]


def test_discord_post_status_posts_to_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    context.discord_service = fake_discord
    server = create_server(context)

    result = call_tool(
        server,
        "discord_post_status",
        {
            "session_id": "session-1",
            "channel_id": "chan-allowlisted",
            "message": "Host relay is healthy.",
        },
    )

    assert result["status"] == "posted"
    assert fake_discord.post_calls == [
        ("Host relay is healthy.", "chan-allowlisted", "session-1"),
    ]


def test_discord_post_status_rejects_non_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.discord_service = FakeDiscordService(commands=[])
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowlisted"):
        asyncio.run(
            server.call_tool(
                "discord_post_status",
                {
                    "session_id": "session-1",
                    "channel_id": "chan-blocked",
                    "message": "This should be denied.",
                },
            )
        )


def test_discord_tool_contract_signatures_and_shapes_stable(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
          session_id="session-1",
          allowed_tools=[ToolName.DISCORD_GET_COMMANDS, ToolName.DISCORD_POST_STATUS],
          discord_channel_allowlist=["chan-allowlisted"],
          expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.discord_service = FakeDiscordService(
        commands=[
            DiscordCommandEnvelope(
                command_id="cmd-1",
                author_id="user-1",
                channel_id="chan-allowlisted",
                content="!status",
                metadata={
                  "source": "discord_bot_api",
                  "channel_id": "chan-allowlisted",
                  "author_id": "user-1",
                },
            )
        ]
    )
    server = create_server(context)
    assert list(signature(FakeDiscordService.get_commands).parameters) == [
        "self",
        "since_cursor",
    ]
    assert signature(FakeDiscordService.get_commands).parameters["since_cursor"].default is None
    assert list(signature(FakeDiscordService.post_status).parameters) == [
        "self",
        "message",
        "channel_id",
        "session_id",
    ]

    get_result = call_tool(server, "discord_get_commands", {"session_id": "session-1", "since_cursor": None})
    post_result = call_tool(
        server,
        "discord_post_status",
        {
            "session_id": "session-1",
            "channel_id": "chan-allowlisted",
            "message": "ok",
        },
    )

    assert isinstance(get_result, dict)
    assert get_result["commands"]
    assert {"command_id", "author_id", "channel_id", "content", "metadata"}.issubset(get_result["commands"][0])

    assert set(post_result) == {"status", "message_id", "channel_id", "session_id"}
    assert post_result["status"] == "posted"
def test_discord_get_commands_audits_adapter_http_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    fake_discord.get_commands = lambda since_cursor=None: (_ for _ in ()).throw(
        httpx.ReadTimeout("discord timeout")
    )
    context.discord_service = fake_discord
    server = create_server(context)

    with pytest.raises(ToolError, match="discord timeout"):
        asyncio.run(
            server.call_tool(
                "discord_get_commands",
                {"session_id": "session-1", "since_cursor": "cursor-42"},
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "discord_get_commands"
    assert events[-1]["result"] == {
        "error": "discord timeout",
        "error_class": "adapter_http_error",
    }

def test_discord_post_status_audits_unexpected_adapter_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    fake_discord.post_status = lambda message, channel_id, session_id: (_ for _ in ()).throw(
        RuntimeError("discord adapter exploded")
    )
    context.discord_service = fake_discord
    server = create_server(context)

    with pytest.raises(ToolError, match="discord adapter exploded"):
        asyncio.run(
            server.call_tool(
                "discord_post_status",
                {
                    "session_id": "session-1",
                    "channel_id": "chan-allowlisted",
                    "message": "Host relay is healthy.",
                },
            )
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["decision"] == "error"
    assert events[-1]["tool_name"] == "discord_post_status"
    assert events[-1]["result"] == {
        "error": "discord adapter exploded",
        "error_class": "unexpected_error",
    }


def test_discord_get_commands_emits_denied_and_allowed_audit_events(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.discord_service = FakeDiscordService(
        commands=[
            DiscordCommandEnvelope(
                command_id="cmd-1",
                author_id="user-1",
                channel_id="chan-blocked",
                content="!status",
                metadata={
                  "source": "discord_bot_api",
                  "channel_id": "chan-blocked",
                  "author_id": "user-1",
                },
            )
        ]
    )
    server = create_server(context)
    result = call_tool(server, "discord_get_commands", {"session_id": "session-1"})

    assert result["commands"] == []
    events = read_audit_events(context.config.audit_log_path)
    decisions = [event["decision"] for event in events]
    assert decisions == ["denied", "allowed"]
  
def test_email_tools_require_and_return_mailbox_id(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.EMAIL_LIST_UNREAD,
                ToolName.EMAIL_GET_MESSAGE,
                ToolName.EMAIL_CREATE_DRAFT,
            ],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox", "drafts"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    context.email_service.provider.email_store.put(
        "msg-1",
        context.email_service.provider.email_store.model(
            message_id="msg-1",
            mailbox_id="primary",
            folder="inbox",
            sender="from@example.com",
            recipients=["to@example.com"],
            subject="hello",
            body_text="world",
        ),
    )

    server = create_server(context)

    listed = call_tool(
        server,
        "email_list_unread",
        {"session_id": "session-1", "mailbox_id": "primary", "folder": "inbox", "limit": 10},
    )
    assert listed[0]["mailbox_id"] == "primary"

    fetched = call_tool(
        server,
        "email_get_message",
        {"session_id": "session-1", "mailbox_id": "primary", "message_id": "msg-1"},
    )
    assert fetched["mailbox_id"] == "primary"

    draft = call_tool(
        server,
        "email_create_draft",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Re: hello",
        },
    )
    assert draft["mailbox_id"] == "primary"


def test_email_tools_enforce_mailbox_and_folder_allowlists(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="mailbox is not allowlisted"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {"session_id": "session-1", "mailbox_id": "secondary", "folder": "inbox", "limit": 10},
            )
        )

    with pytest.raises(ToolError, match="folder is not allowlisted"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {"session_id": "session-1", "mailbox_id": "primary", "folder": "sent", "limit": 10},
            )
        )


def test_email_create_draft_enforces_recipient_allowlist(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Draft recipients are not allowlisted"):
        asyncio.run(
            server.call_tool(
                "email_create_draft",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "body": "draft body",
                    "subject": "Re: hello",
                    "recipients": ["denied@example.com"],
                },
            )
        )


def test_email_send_approved_is_blocked_when_send_disabled(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_SEND_APPROVED],
            email_mailbox_allowlist=["primary"],
            email_send_enabled=False,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Email send is disabled"):
        asyncio.run(
            server.call_tool(
                "email_send_approved",
                {
                    "session_id": "session-1",
                    "request_id": "apr-1",
                    "payload": {"mailbox_id": "primary", "subject": "Blocked"},
                },
            )
        )


def test_email_get_message_prevents_cross_mailbox_access(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_GET_MESSAGE],
            email_mailbox_allowlist=["primary", "secondary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"], "secondary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    context.email_service.provider.email_store.put(
        "msg-primary-1",
        context.email_service.provider.email_store.model(
            message_id="msg-primary-1",
            mailbox_id="primary",
            folder="inbox",
            sender="from@example.com",
            recipients=["to@example.com"],
            subject="hello",
            body_text="world",
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Unknown message for mailbox secondary"):
        asyncio.run(
            server.call_tool(
                "email_get_message",
                {"session_id": "session-1", "mailbox_id": "secondary", "message_id": "msg-primary-1"},
            )
        )


class _RaisingEmailService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        raise self.error

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        raise self.error

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
    ) -> EmailDraft:
        raise self.error


class _FakePrFeedbackService:
    def __init__(self) -> None:
        self.last_streams: list[dict[str, str]] = []
        self.last_cursor: str | None = None

    def has_pending_feedback(
        self, *, streams: list[dict[str, str]], cursor: str | None = None
    ) -> SimpleNamespace:
        self.last_streams = streams
        self.last_cursor = cursor
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "pending": True,
                "next_cursor": "2026-01-01T00:00:00Z",
            }
        )

    def list_unread_feedback(
        self, *, streams: list[dict[str, str]], cursor: str | None = None
    ) -> SimpleNamespace:
        self.last_streams = streams
        self.last_cursor = cursor
        payload = {
            "feedback": [
                {
                    "feedback_id": "github:review_comment:7",
                    "provider": "github",
                    "repo": "org/repo",
                    "pr_id": "17",
                    "author": "reviewer",
                    "body": "Please rename this variable.",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "next_cursor": "2026-01-01T00:00:00Z",
        }
        return SimpleNamespace(
            feedback=[payload["feedback"][0]],
            next_cursor=payload["next_cursor"],
            model_dump=lambda mode="json": payload,
        )


class _FakePrReplyService:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def post_reply(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        idempotency_key: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "provider": provider,
                "repo": repo,
                "pr_id": pr_id,
                "thread_id": thread_id,
                "comment_id": comment_id,
                "body_text": body_text,
                "idempotency_key": idempotency_key,
            }
        )
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "status": "posted",
                "provider": provider,
                "repo": repo,
                "pr_id": pr_id,
                "thread_id": thread_id,
                "comment_id": comment_id,
                "reply_id": "reply-1",
                "reply_url": "https://example.test/replies/1",
                "idempotency_key": idempotency_key,
            }
        )


def test_email_provider_auth_failures_are_audited(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.email_service = _RaisingEmailService(
        ProviderRequestError("provider auth failed", status_code=401, attempt=1, max_attempts=1)
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="provider auth failed"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {"session_id": "session-1", "mailbox_id": "primary", "folder": "inbox", "limit": 10},
            )
        )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.EMAIL_LIST_UNREAD.value
    assert event["decision"] == "error"
    assert event["result"] == {
        "error": "provider auth failed",
        "error_class": "adapter_http_error",
    }


def test_email_provider_timeouts_are_audited(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.email_service = _RaisingEmailService(httpx.TimeoutException("provider timed out"))
    server = create_server(context)

    with pytest.raises(ToolError, match="provider timed out"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {"session_id": "session-1", "mailbox_id": "primary", "folder": "inbox", "limit": 10},
            )
        )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.EMAIL_LIST_UNREAD.value
    assert event["decision"] == "error"
    assert event["result"] == {
        "error": "provider timed out",
        "error_class": "adapter_http_error",
    }


def test_audit_success_recursively_redacts_nested_payload_fields(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    payload = {
        "mailbox_id": "primary",
        "body": "Top secret body",
        "authorization": "Bearer secret",
        "nested": {
            "token": "nested-token",
            "safe_key": "safe-value",
            "body": "nested secret body",
        },
        "recipients": [
            "safe@example.com",
            {"email": "also-safe@example.com", "token": "recipient-token"},
        ],
        "actions": [
            {"type": "notify", "payload": {"body": "action secret", "status": "ok"}},
        ],
    }

    asyncio.run(
        server.call_tool(
            "approval_submit",
            {
                "session_id": "session-1",
                "action_type": "email_send",
                "payload": payload,
                "human_summary": "send summary",
            },
        )
    )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["decision"] == "allowed"
    assert event["args"]["payload"]["mailbox_id"] == "primary"
    assert event["args"]["payload"]["body"] == "<redacted>"
    assert event["args"]["payload"]["authorization"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["token"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["body"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["safe_key"] == "safe-value"
    assert event["args"]["payload"]["recipients"][0] == "safe@example.com"
    assert event["args"]["payload"]["recipients"][1]["email"] == "also-safe@example.com"
    assert event["args"]["payload"]["recipients"][1]["token"] == "<redacted>"
    assert event["args"]["payload"]["actions"][0]["payload"]["body"] == "<redacted>"
    assert event["args"]["payload"]["actions"][0]["payload"]["status"] == "ok"


def test_audit_failure_recursively_redacts_nested_payload_fields(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="is not allowed"):
        asyncio.run(
            server.call_tool(
                "approval_submit",
                {
                    "session_id": "session-1",
                    "action_type": "email_send",
                    "payload": {
                        "body": "Denied secret body",
                        "authorization": "Bearer denied-secret",
                        "nested": {"token": "denied-token", "safe": "visible"},
                        "recipients": [
                            {"email": "still-visible@example.com", "token": "recipient-token"}
                        ],
                    },
                    "human_summary": "should fail",
                },
            )
        )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["decision"] == "denied"
    assert event["args"]["payload"]["body"] == "<redacted>"
    assert event["args"]["payload"]["authorization"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["token"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["safe"] == "visible"
    assert event["args"]["payload"]["recipients"][0]["email"] == "still-visible@example.com"
    assert event["args"]["payload"]["recipients"][0]["token"] == "<redacted>"


def test_has_pending_feedback_returns_minimal_heartbeat_payload(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.HAS_PENDING_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    )
    context.pr_feedback_service = _FakePrFeedbackService()
    server = create_server(context)

    result = call_tool(server, "has_pending_feedback", {"session_id": "session-1"})

    assert result == {"pending": True, "next_cursor": "2026-01-01T00:00:00Z"}
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.HAS_PENDING_FEEDBACK.value
    assert event["result"] == {"pending": True}


def test_list_unread_feedback_returns_items_and_audits_result_count(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_UNREAD_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    )
    context.pr_feedback_service = _FakePrFeedbackService()
    server = create_server(context)

    result = call_tool(
        server,
        "list_unread_feedback",
        {"session_id": "session-1", "cursor": "2025-12-31T00:00:00Z"},
    )

    assert result["feedback"][0]["feedback_id"] == "github:review_comment:7"
    assert result["next_cursor"] == "2026-01-01T00:00:00Z"
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.LIST_UNREAD_FEEDBACK.value
    assert event["result"] == {"count": 1, "next_cursor": "2026-01-01T00:00:00Z"}


def test_post_pr_feedback_reply_calls_provider_and_audits_reply_identifiers(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    )
    fake_service = _FakePrReplyService()
    context.pr_reply_service = fake_service
    server = create_server(context)

    result = call_tool(
        server,
        "post_pr_feedback_reply",
        {
            "session_id": "session-1",
            "provider": "github",
            "repo": "org/repo",
            "pr_id": "17",
            "comment_id": "91",
            "body_text": "Applied in the latest patch.",
            "idempotency_key": "reply-key-1",
        },
    )

    assert result["reply_id"] == "reply-1"
    assert fake_service.calls[0]["comment_id"] == "91"
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.POST_PR_FEEDBACK_REPLY.value
    assert event["result"] == {
        "status": "posted",
        "provider": "github",
        "reply_id": "reply-1",
        "reply_url": "https://example.test/replies/1",
    }


def test_post_pr_feedback_reply_rejects_missing_target_identifiers(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    )
    context.pr_reply_service = _FakePrReplyService()
    server = create_server(context)

    with pytest.raises(ToolError, match="Either thread_id or comment_id is required"):
        call_tool(
            server,
            "post_pr_feedback_reply",
            {
                "session_id": "session-1",
                "provider": "github",
                "repo": "org/repo",
                "pr_id": "17",
                "body_text": "This should fail.",
            },
        )
