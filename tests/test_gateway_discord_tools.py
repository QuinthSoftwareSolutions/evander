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

from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService, DirectoryStore
from evander.email.http_utils import ProviderRequestError
from evander.email.providers import EmailDraft, EmailMessageDetail, EmailMessageSummary
from evander.gateway import create_server
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.models import (
    ApprovalStatus,
    DiscordCommandEnvelope,
    DiscordMessageEnvelope,
    EmailMailboxGrant,
    LocalIssueRecord,
    SessionManifest,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
    ToolName,
)
from evander.runtime import build_runtime


class FakeDiscordService:
    def __init__(self, commands: list[DiscordCommandEnvelope]) -> None:
        self.commands = commands
        self.last_since_cursor: str | None = None
        self.post_calls: list[tuple[str, str, str]] = []
        self.reaction_calls: list[tuple[str, str, str, str]] = []
        self.reaction_error_by_message_id: dict[str, Exception] = {}

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

    def add_reaction(
        self, channel_id: str, message_id: str, emoji: str, session_id: str
    ) -> dict[str, str]:
        self.reaction_calls.append((channel_id, message_id, emoji, session_id))
        if message_id in self.reaction_error_by_message_id:
            raise self.reaction_error_by_message_id[message_id]
        return {
            "status": "reacted",
            "channel_id": channel_id,
            "message_id": message_id,
            "emoji": emoji,
            "reaction": emoji,
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
    context = build_runtime(config)
    ensure_directories(context, "root/team-a", "root/team-b")
    return context


def patch_gateway_admin_runtime(monkeypatch: pytest.MonkeyPatch, **service_factories):
    def fake_build_admin_runtime(runtime_dir: Path):
        return SimpleNamespace(
            **{name: factory(runtime_dir) for name, factory in service_factories.items()}
        )

    monkeypatch.setattr("evander.gateway.build_admin_runtime", fake_build_admin_runtime)


def ensure_directories(context, *directory_paths: str) -> None:
    service = DirectoryService(context.directories)
    for directory_path in directory_paths:
        current = "root"
        for segment in [part for part in directory_path.split("/")[1:] if part]:
            next_path = f"{current}/{segment}"
            try:
                service.show(next_path)
            except ValueError:
                service.create(parent=current, name=segment)
            current = next_path


def write_root_policy_overlay(context, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(context.config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=context.config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


def write_root_communication_targets(context, targets: list[dict[str, object]]) -> None:
    context.directories.ensure_root()
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(context.config.runtime_dir)
    )
    repository.write_overlay("root", {"targets": targets})


def set_discord_service(context, service) -> None:
    context.discord_service = service


def set_email_service(context, service) -> None:
    context.email_service = service


def set_pr_reply_service(context, service) -> None:
    context.pr_reply_service = service


def _seed_discord_messages(path: Path, messages: list[DiscordMessageEnvelope]) -> None:
    store = DiscordInboxStore(path)
    assert store.add_messages_if_new(messages) == len(messages)


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


def call_tool_with_context(
    server,
    name: str,
    arguments: dict[str, object],
    context: object,
):
    tool_manager_call = server._tool_manager.call_tool
    try:
        result = asyncio.run(tool_manager_call(name, arguments, context=context))
    except TypeError as exc:
        if "context" not in str(exc):
            raise
        result = asyncio.run(tool_manager_call(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def make_request_context(
    *,
    session_id: str | None = None,
    transport_session_id: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
):
    query_params: dict[str, str] = {}
    if session_id is not None:
        query_params["session_id"] = session_id
    headers: dict[str, str] = {}
    if transport_session_id is not None:
        headers["mcp-session-id"] = transport_session_id
    if request_id is not None:
        headers["x-request-id"] = request_id
    if correlation_id is not None:
        headers["x-correlation-id"] = correlation_id
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params=query_params, headers=headers)
        )
    )


def test_discord_get_commands_passes_since_cursor_and_filters_unallowlisted_sources(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    set_discord_service(context, fake_discord)
    server = create_server(context)

    result = call_tool(
        server,
        "discord_get_commands",
        {"session_id": "session-1", "since_cursor": "cursor-42"},
    )

    assert fake_discord.last_since_cursor == "cursor-42"
    assert isinstance(result, dict)
    assert [entry["command_id"] for entry in result["commands"]] == ["cmd-1"]


def test_list_unread_discord_messages_does_not_apply_read_reactions(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_UNREAD_DISCORD_MESSAGES],
            discord_channel_allowlist=["chan-a"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    inbox_store_path = context.config.runtime_dir / "discord-inbox-store.json"
    _seed_discord_messages(
        inbox_store_path,
        [
            DiscordMessageEnvelope(
                message_id="discord:1",
                channel_id="chan-a",
                author_id="bot-user",
                author_name="Bot",
                content="Self echo",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                raw={"source": "discord_bot_api_status", "session_id": "session-1"},
            ),
            DiscordMessageEnvelope(
                message_id="discord:2",
                channel_id="chan-a",
                author_id="user-a",
                author_name="Alice",
                content="Human reply",
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
                raw={"source": "discord_bot_api"},
            ),
            DiscordMessageEnvelope(
                message_id="discord:3",
                channel_id="chan-a",
                author_id="user-b",
                author_name="Bob",
                content="Another human reply",
                created_at=datetime(2026, 1, 3, tzinfo=UTC),
                raw={"source": "discord_bot_api"},
            ),
        ],
    )
    fake_discord = FakeDiscordService(commands=[])
    set_discord_service(context, fake_discord)
    server = create_server(context)

    result = call_tool(server, "list_unread_discord_messages", {"session_id": "session-1"})

    assert [entry["message_id"] for entry in result["messages"]] == ["discord:2", "discord:3"]
    assert fake_discord.reaction_calls == []


def test_add_message_reaction_adds_discord_reaction_to_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.ADD_MESSAGE_REACTION],
            discord_channel_allowlist=["chan-a"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    set_discord_service(context, fake_discord)
    server = create_server(context)

    result = call_tool(
        server,
        "add_message_reaction",
        {
            "session_id": "session-1",
            "provider": "discord",
            "channel_id": "chan-a",
            "message_id": "discord:2",
            "emoji": "✅",
        },
    )

    assert fake_discord.reaction_calls == [("chan-a", "discord:2", "✅", "session-1")]
    assert result["status"] == "reacted"


def test_add_message_reaction_audits_discord_adapter_failure(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.ADD_MESSAGE_REACTION],
            discord_channel_allowlist=["chan-a"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    request = httpx.Request(
        "PUT", "https://discord.test/channels/chan-a/messages/discord:2/reactions"
    )
    response = httpx.Response(500, request=request, text='{"error":"failure"}')
    fake_discord.reaction_error_by_message_id["discord:2"] = httpx.HTTPStatusError(
        "boom",
        request=request,
        response=response,
    )
    set_discord_service(context, fake_discord)
    server = create_server(context)

    with pytest.raises(ToolError):
        call_tool(
            server,
            "add_message_reaction",
            {
                "session_id": "session-1",
                "provider": "discord",
                "channel_id": "chan-a",
                "message_id": "discord:2",
                "emoji": "👀",
            },
        )

    events = read_audit_events(context.config.audit_log_path)
    assert any(
        event["tool_name"] == ToolName.ADD_MESSAGE_REACTION.value
        and event["result"].get("error_class") == "adapter_http_error"
        and event["args"].get("message_id") == "discord:2"
        for event in events
    )


def test_list_unread_discord_messages_repeated_reads_do_not_retry_reactions(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_UNREAD_DISCORD_MESSAGES],
            discord_channel_allowlist=["chan-a"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    inbox_store_path = context.config.runtime_dir / "discord-inbox-store.json"
    _seed_discord_messages(
        inbox_store_path,
        [
            DiscordMessageEnvelope(
                message_id="discord:2",
                channel_id="chan-a",
                author_id="user-a",
                author_name="Alice",
                content="Human reply",
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
                raw={"source": "discord_bot_api"},
            ),
        ],
    )
    fake_discord = FakeDiscordService(commands=[])
    request = httpx.Request(
        "PUT", "https://discord.test/channels/chan-a/messages/discord:2/reactions"
    )
    response = httpx.Response(500, request=request, text='{"error":"failure"}')
    fake_discord.reaction_error_by_message_id["discord:2"] = httpx.HTTPStatusError(
        "boom",
        request=request,
        response=response,
    )
    set_discord_service(context, fake_discord)
    server = create_server(context)

    first = call_tool(server, "list_unread_discord_messages", {"session_id": "session-1"})
    second = call_tool(server, "list_unread_discord_messages", {"session_id": "session-1"})

    assert [entry["message_id"] for entry in first["messages"]] == ["discord:2"]
    assert second["messages"] == []
    assert fake_discord.reaction_calls == []
    reaction_failure_events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] == ToolName.LIST_UNREAD_DISCORD_MESSAGES.value
        and event["result"].get("error_class") == "reaction_side_effect_error"
    ]
    assert reaction_failure_events == []


def test_create_local_issue_uses_session_directory_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="bug-finder-vm",
            allowed_tools=[ToolName.CREATE_LOCAL_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"automation_role": "bug_finder"},
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def create_local_issue(
            self,
            *,
            title: str,
            body: str,
            managed_repo_id: str | None,
            repo_source: str | None,
            directory_path: str,
            metadata: dict[str, object] | None = None,
        ) -> object:
            captured["payload"] = {
                "title": title,
                "body": body,
                "managed_repo_id": managed_repo_id,
                "repo_source": repo_source,
                "directory_path": directory_path,
                "metadata": metadata,
            }
            return SimpleNamespace(
                issue_id="local-11",
                issue_number="11",
                directory_path=directory_path,
                metadata=metadata or {},
                model_dump=lambda mode="json": {  # noqa: ARG005
                    "issue_id": "local-11",
                    "issue_number": "11",
                    "directory_path": directory_path,
                    "title": title,
                    "body": body,
                    "metadata": metadata or {},
                },
            )

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "create_local_issue",
        {
            "session_id": "session-1",
            "title": "Add local issue MCP tool",
            "body": "Needs to scope to session directory.",
            "repo_source": "/tmp/repo",
        },
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "title": "Add local issue MCP tool",
        "body": "Needs to scope to session directory.",
        "managed_repo_id": None,
        "repo_source": "/tmp/repo",
        "directory_path": "root/team-a",
        "metadata": {
            "generated_by_session_id": "session-1",
            "generated_by_vm_id": "bug-finder-vm",
            "generated_by_role": "bug_finder",
            "issue_kind": "bug",
        },
    }
    assert result["issue_number"] == "11"
    assert result["directory_path"] == "root/team-a"
    assert result["metadata"]["issue_kind"] == "bug"


def test_list_issues_defaults_to_open_states_in_session_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ISSUES],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def list_local_issues(
            self,
            *,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[LocalIssueRecord]:
            captured["list_args"] = {"directory_path": directory_path, "direct_only": direct_only}
            return [
                LocalIssueRecord(
                    issue_number="1",
                    issue_id="local-1",
                    title="Open issue",
                    body="",
                    yaml_path="runtime/issues/issue-1.yaml",
                    directory_path="root/team-a",
                    state="New",
                ),
                LocalIssueRecord(
                    issue_number="2",
                    issue_id="local-2",
                    title="Completed issue",
                    body="",
                    yaml_path="runtime/issues/issue-2.yaml",
                    directory_path="root/team-a",
                    state="Completed",
                ),
                LocalIssueRecord(
                    issue_number="3",
                    issue_id="local-3",
                    title="Unset state issue",
                    body="",
                    yaml_path="runtime/issues/issue-3.yaml",
                    directory_path="root/team-a",
                    state=None,
                ),
            ]

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(server, "list_issues", {"session_id": "session-1"})

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["list_args"] == {"directory_path": "root/team-a", "direct_only": True}
    assert [entry["issue_number"] for entry in result["issues"]] == ["1", "3"]


def test_list_issues_filters_by_requested_status_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ISSUES],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubAdminService:
        def __init__(self, _runtime_dir: Path) -> None:
            pass

        def list_local_issues(
            self,
            *,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[LocalIssueRecord]:
            return [
                LocalIssueRecord(
                    issue_number="1",
                    issue_id="local-1",
                    title="Committed issue",
                    body="",
                    yaml_path="runtime/issues/issue-1.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="Committed",
                ),
                LocalIssueRecord(
                    issue_number="2",
                    issue_id="local-2",
                    title="In progress issue",
                    body="",
                    yaml_path="runtime/issues/issue-2.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="In Progress",
                ),
            ]

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "list_issues",
        {"session_id": "session-1", "status": [" in progress "]},
    )

    assert [entry["issue_number"] for entry in result["issues"]] == ["2"]


def test_list_issues_exposes_rejection_reason_from_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ISSUES],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubAdminService:
        def __init__(self, _runtime_dir: Path) -> None:
            pass

        def list_local_issues(
            self,
            *,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[LocalIssueRecord]:
            return [
                LocalIssueRecord(
                    issue_number="42",
                    issue_id="local-42",
                    title="Rejected issue",
                    body="",
                    yaml_path="runtime/issues/issue-42.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="Closed",
                    metadata={"fields": {"Reason": "Out of scope"}},
                ),
            ]

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "list_issues",
        {"session_id": "session-1", "status": ["closed"]},
    )

    assert result["issues"] == [
        {
            "issue_id": "local-42",
            "issue_number": "42",
            "title": "Rejected issue",
            "body": "",
            "description": "",
            "reason": "Out of scope",
            "rejection_reason": "Out of scope",
            "yaml_path": "runtime/issues/issue-42.yaml",
            "directory_path": "root/team-a",
            "managed_repo_id": None,
            "source_type": "local_manual",
            "remote_reference": None,
            "tags": [],
            "state": "Closed",
            "url": None,
            "comments": [],
            "forwarding_policy": None,
            "source": "local_gui",
            "metadata": {"fields": {"Reason": "Out of scope"}},
            "created_at": result["issues"][0]["created_at"],
            "updated_at": result["issues"][0]["updated_at"],
        }
    ]


def test_list_issues_treats_rejected_closed_and_completed_as_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ISSUES],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubAdminService:
        def __init__(self, _runtime_dir: Path) -> None:
            pass

        def list_local_issues(
            self,
            *,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[LocalIssueRecord]:
            return [
                LocalIssueRecord(
                    issue_number="1",
                    issue_id="local-1",
                    title="Rejected issue",
                    body="",
                    yaml_path="runtime/issues/issue-1.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="Closed",
                    metadata={"fields": {"Reason": "Out of scope"}},
                ),
                LocalIssueRecord(
                    issue_number="2",
                    issue_id="local-2",
                    title="Closed issue",
                    body="",
                    yaml_path="runtime/issues/issue-2.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="Closed",
                ),
                LocalIssueRecord(
                    issue_number="3",
                    issue_id="local-3",
                    title="Completed issue",
                    body="",
                    yaml_path="runtime/issues/issue-3.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="Completed",
                ),
                LocalIssueRecord(
                    issue_number="4",
                    issue_id="local-4",
                    title="In progress issue",
                    body="",
                    yaml_path="runtime/issues/issue-4.yaml",
                    directory_path=directory_path or "root/team-a",
                    state="In Progress",
                ),
            ]

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "list_issues",
        {
            "session_id": "session-1",
            "status": [" Rejected ", "Closed", "Completed"],
        },
    )

    issue_numbers = [entry["issue_number"] for entry in result["issues"]]
    assert issue_numbers == ["1", "2", "3"]
    assert issue_numbers.count("1") == 1
    assert "4" not in issue_numbers


def test_get_issue_passes_through_backend_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def get_issue(
            self,
            issue_id: str,
            *,
            include_remote_comments: bool = True,
            sync_proxy_cache: bool = True,
        ) -> dict[str, object]:
            captured["payload"] = {
                "issue_id": issue_id,
                "include_remote_comments": include_remote_comments,
                "sync_proxy_cache": sync_proxy_cache,
            }
            return {
                "issue_id": "local-42",
                "issue_number": "42",
                "source_type": "local",
                "title": "Rejected issue",
                "state": "Closed",
                "reason": "Out of scope",
                "rejection_reason": "Out of scope",
            }

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "get_issue",
        {
            "session_id": "session-1",
            "issue_id": "42",
            "include_remote_comments": False,
            "sync_proxy_cache": False,
        },
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "issue_id": "42",
        "include_remote_comments": False,
        "sync_proxy_cache": False,
    }
    assert result["issue_number"] == "42"
    assert result["rejection_reason"] == "Out of scope"


def test_discord_post_status_posts_to_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    set_discord_service(context, fake_discord)
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


def test_discord_post_status_resolves_communication_target_id(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    write_root_communication_targets(
        context,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["discord-ops"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    set_discord_service(context, fake_discord)
    server = create_server(context)

    result = call_tool(
        server,
        "discord_post_status",
        {
            "session_id": "session-1",
            "channel_id": "discord-ops",
            "message": "Host relay is healthy.",
        },
    )

    assert result["status"] == "posted"
    assert fake_discord.post_calls == [
        ("Host relay is healthy.", "123456789012345678", "session-1"),
    ]


def test_create_local_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "create_local_issue",
                {
                    "session_id": "session-1",
                    "title": "Denied",
                },
            )
        )


def test_list_issues_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "list_issues",
                {"session_id": "session-1"},
            )
        )


def test_get_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "get_issue",
                {
                    "session_id": "session-1",
                    "issue_id": "42",
                },
            )
        )


def test_approve_issue_sets_approved_state_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVE_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def update_issue(
            self,
            issue_id: str,
            *,
            state: str | None = None,
            fields: dict[str, object] | None = None,
            sync_proxy_cache: bool = True,  # noqa: ARG002
        ) -> dict[str, object]:
            captured["payload"] = {
                "issue_id": issue_id,
                "state": state,
                "fields": fields,
            }
            return {"issue_number": issue_id, "source_type": "local", "updated": True}

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "approve_issue",
        {
            "session_id": "session-1",
            "issue_id": "42",
            "priority": "P1",
            "reason": "Capacity confirmed",
        },
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "issue_id": "42",
        "state": "Approved",
        "fields": {"Priority": "P1", "Reason": "Capacity confirmed"},
    }
    assert result["issue_number"] == "42"


def test_reject_issue_sets_closed_state_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.REJECT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def update_issue(
            self,
            issue_id: str,
            *,
            state: str | None = None,
            fields: dict[str, object] | None = None,
            sync_proxy_cache: bool = True,  # noqa: ARG002
        ) -> dict[str, object]:
            captured["payload"] = {
                "issue_id": issue_id,
                "state": state,
                "fields": fields,
            }
            return {"issue_number": issue_id, "source_type": "local", "updated": True}

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "reject_issue",
        {"session_id": "session-1", "issue_id": "42", "reason": "Out of scope"},
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "issue_id": "42",
        "state": "Closed",
        "fields": {"Reason": "Out of scope"},
    }
    assert result["issue_number"] == "42"


def test_commit_issue_sets_committed_state_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.COMMIT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def update_issue(
            self,
            issue_id: str,
            *,
            state: str | None = None,
            fields: dict[str, object] | None = None,
            sync_proxy_cache: bool = True,  # noqa: ARG002
        ) -> dict[str, object]:
            captured["payload"] = {
                "issue_id": issue_id,
                "state": state,
                "fields": fields,
            }
            return {"issue_number": issue_id, "source_type": "local", "updated": True}

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "commit_issue",
        {"session_id": "session-1", "issue_id": "42", "reason": "Implementation has started"},
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "issue_id": "42",
        "state": "Committed",
        "fields": {"Reason": "Implementation has started"},
    }
    assert result["issue_number"] == "42"


def test_commit_issue_reuses_mapped_transport_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.COMMIT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {"payloads": []}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def update_issue(
            self,
            issue_id: str,
            *,
            state: str | None = None,
            fields: dict[str, object] | None = None,
            sync_proxy_cache: bool = True,  # noqa: ARG002
        ) -> dict[str, object]:
            payload = {
                "issue_id": issue_id,
                "state": state,
                "fields": fields,
            }
            cast_payloads = captured["payloads"]
            assert isinstance(cast_payloads, list)
            cast_payloads.append(payload)
            return {"issue_number": issue_id, "source_type": "local", "updated": True}

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    seeded_result = call_tool_with_context(
        server,
        "commit_issue",
        {
            "issue_id": "42",
            "reason": "Implementation has started",
        },
        make_request_context(session_id="session-1", transport_session_id="transport-1"),
    )
    mapped_result = call_tool_with_context(
        server,
        "commit_issue",
        {
            "issue_id": "43",
            "reason": "Implementation has started",
        },
        make_request_context(transport_session_id="transport-1"),
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payloads"] == [
        {
            "issue_id": "42",
            "state": "Committed",
            "fields": {"Reason": "Implementation has started"},
        },
        {
            "issue_id": "43",
            "state": "Committed",
            "fields": {"Reason": "Implementation has started"},
        },
    ]
    assert seeded_result["issue_number"] == "42"
    assert mapped_result["issue_number"] == "43"

    events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] == ToolName.COMMIT_ISSUE.value
    ]
    assert [event["session_id"] for event in events] == ["session-1", "session-1"]
    assert all(event["decision"] == "allowed" for event in events)
    assert "session_id is required" not in context.config.audit_log_path.read_text(encoding="utf-8")


def test_commit_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "commit_issue",
                {
                    "session_id": "session-1",
                    "issue_id": "42",
                },
            )
        )


def test_claim_next_issue_passes_through_backend_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def claim_next_issue(self, *, session_id: str) -> object:
            captured["payload"] = {"session_id": session_id}
            return SimpleNamespace(
                issue_number="99",
                source_type="local",
                model_dump=lambda mode="json": {  # noqa: ARG005
                    "issue_number": "99",
                    "issue_id": "local-99",
                    "source_type": "local",
                    "title": "Claimed issue",
                },
            )

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(server, "claim_next_issue", {"session_id": "session-1"})

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {"session_id": "session-1"}
    assert result["issue_number"] == "99"
    assert result["title"] == "Claimed issue"


def test_complete_issue_passes_through_backend_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.COMPLETE_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def complete_issue(
            self, *, session_id: str, issue_id: str, summary: str
        ) -> dict[str, object]:
            captured["payload"] = {
                "session_id": session_id,
                "issue_id": issue_id,
                "summary": summary,
            }
            return {
                "issue_number": issue_id,
                "source_type": "local",
                "updated": True,
            }

    patch_gateway_admin_runtime(monkeypatch, issue_git_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "complete_issue",
        {
            "session_id": "session-1",
            "issue_id": "42",
            "summary": "Implemented and pushed.",
        },
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["payload"] == {
        "session_id": "session-1",
        "issue_id": "42",
        "summary": "Implemented and pushed.",
    }
    assert result["issue_number"] == "42"
    assert result["updated"] is True


def test_approve_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "approve_issue",
                {
                    "session_id": "session-1",
                    "issue_id": "42",
                },
            )
        )


def test_reject_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "reject_issue",
                {
                    "session_id": "session-1",
                    "issue_id": "42",
                },
            )
        )


def test_claim_next_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "claim_next_issue",
                {"session_id": "session-1"},
            )
        )


def test_complete_issue_requires_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "complete_issue",
                {
                    "session_id": "session-1",
                    "issue_id": "42",
                    "summary": "done",
                },
            )
        )


def test_get_agent_status_maps_backend_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="vm-123",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def list_vms(
            self,
            *,
            include_inactive: bool,
            vm_user: str,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[SimpleNamespace]:
            captured["list_args"] = {
                "include_inactive": include_inactive,
                "vm_user": vm_user,
                "directory_path": directory_path,
                "direct_only": direct_only,
            }
            return [
                SimpleNamespace(
                    vm_name="vm-123",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.12",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                )
            ]

    patch_gateway_admin_runtime(monkeypatch, vm_agent_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(server, "get_agent_status", {"session_id": "session-1", "vm_name": "vm-123"})

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["list_args"] == {
        "include_inactive": True,
        "vm_user": "agent",
        "directory_path": "root/team-a",
        "direct_only": True,
    }
    assert result["status"] == "active"
    assert result["agent_state"] == "running"
    assert result["last_state_update_at"] == "2026-03-29T12:00:00+00:00"


def test_get_agent_status_allows_same_directory_peer_vm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="vm-self",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubAdminService:
        def __init__(self, _runtime_dir: Path) -> None:
            pass

        def list_vms(
            self,
            *,
            include_inactive: bool,
            vm_user: str,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[SimpleNamespace]:
            assert include_inactive is True
            assert vm_user == "agent"
            assert directory_path == "root/team-a"
            assert direct_only is True
            return [
                SimpleNamespace(
                    vm_name="vm-self",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.10",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                ),
                SimpleNamespace(
                    vm_name="vm-peer",
                    agent_state="idle",
                    libvirt_state="running",
                    ip_address="10.0.0.11",
                    agent_repo_names=["repo-b"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 5, tzinfo=UTC),
                ),
            ]

    patch_gateway_admin_runtime(monkeypatch, vm_agent_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server, "get_agent_status", {"session_id": "session-1", "vm_name": "vm-peer"}
    )

    assert result["vm_name"] == "vm-peer"
    assert result["status"] == "idle"


def test_get_agent_status_rejects_cross_directory_vm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="vm-self",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubAdminService:
        def __init__(self, _runtime_dir: Path) -> None:
            pass

        def list_vms(
            self,
            *,
            include_inactive: bool,
            vm_user: str,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[SimpleNamespace]:
            assert include_inactive is True
            assert vm_user == "agent"
            assert directory_path == "root/team-a"
            assert direct_only is True
            return [
                SimpleNamespace(
                    vm_name="vm-self",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.10",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                )
            ]

    patch_gateway_admin_runtime(monkeypatch, vm_agent_admin=StubAdminService)
    server = create_server(context)

    with pytest.raises(ToolError, match="outside the session directory scope 'root/team-a'"):
        asyncio.run(
            server.call_tool("get_agent_status", {"session_id": "session-1", "vm_name": "vm-other"})
        )


def test_get_agent_logs_returns_tail_from_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="vm-self",
            allowed_tools=[ToolName.GET_AGENT_LOGS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def list_vms(
            self,
            *,
            include_inactive: bool,
            vm_user: str,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[SimpleNamespace]:
            captured["list_args"] = {
                "include_inactive": include_inactive,
                "vm_user": vm_user,
                "directory_path": directory_path,
                "direct_only": direct_only,
            }
            return [
                SimpleNamespace(
                    vm_name="vm-peer",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.12",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                )
            ]

        def get_agent_log_snapshot(
            self, target: str, *, lines: int, repo_name: str | None, vm_user: str
        ) -> str:
            captured["payload"] = {
                "target": target,
                "lines": lines,
                "repo_name": repo_name,
                "vm_user": vm_user,
            }
            return "log-line-1\nlog-line-2"

    patch_gateway_admin_runtime(monkeypatch, vm_agent_admin=StubAdminService)
    server = create_server(context)

    result = call_tool(
        server,
        "get_agent_logs",
        {"session_id": "session-1", "vm_name": "vm-peer", "lines": 20, "repo_name": "repo-a"},
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["list_args"] == {
        "include_inactive": True,
        "vm_user": "agent",
        "directory_path": "root/team-a",
        "direct_only": True,
    }
    assert captured["payload"] == {
        "target": "vm-peer",
        "lines": 20,
        "repo_name": "repo-a",
        "vm_user": "agent",
    }
    assert result["lines"] == 20
    assert result["log"] == "log-line-1\nlog-line-2"


def test_get_agent_status_and_logs_reuse_mapped_transport_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            vm_id="vm-123",
            allowed_tools=[ToolName.GET_AGENT_STATUS, ToolName.GET_AGENT_LOGS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubAdminService:
        def __init__(self, runtime_dir: Path) -> None:
            captured["runtime_dir"] = runtime_dir

        def list_vms(
            self,
            *,
            include_inactive: bool,
            vm_user: str,
            directory_path: str | None = None,
            direct_only: bool = False,
        ) -> list[SimpleNamespace]:
            captured["list_args"] = {
                "include_inactive": include_inactive,
                "vm_user": vm_user,
                "directory_path": directory_path,
                "direct_only": direct_only,
            }
            return [
                SimpleNamespace(
                    vm_name="vm-123",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.12",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                ),
                SimpleNamespace(
                    vm_name="vm-peer",
                    agent_state="running",
                    libvirt_state="running",
                    ip_address="10.0.0.13",
                    agent_repo_names=["repo-a"],
                    last_state_update_at=datetime(2026, 3, 29, 12, 5, tzinfo=UTC),
                ),
            ]

        def get_agent_log_snapshot(
            self, target: str, *, lines: int, repo_name: str | None, vm_user: str
        ) -> str:
            captured["payload"] = {
                "target": target,
                "lines": lines,
                "repo_name": repo_name,
                "vm_user": vm_user,
            }
            return "log-line-1\nlog-line-2"

    patch_gateway_admin_runtime(monkeypatch, vm_agent_admin=StubAdminService)
    server = create_server(context)

    seeded_status = call_tool_with_context(
        server,
        "get_agent_status",
        {"vm_name": "vm-123"},
        make_request_context(session_id="session-1", transport_session_id="transport-1"),
    )
    mapped_status = call_tool_with_context(
        server,
        "get_agent_status",
        {"vm_name": "vm-123"},
        make_request_context(transport_session_id="transport-1"),
    )
    mapped_logs = call_tool_with_context(
        server,
        "get_agent_logs",
        {"vm_name": "vm-peer", "lines": 20, "repo_name": "repo-a"},
        make_request_context(transport_session_id="transport-1"),
    )

    assert captured["runtime_dir"] == context.config.runtime_dir
    assert captured["list_args"] == {
        "include_inactive": True,
        "vm_user": "agent",
        "directory_path": "root/team-a",
        "direct_only": True,
    }
    assert captured["payload"] == {
        "target": "vm-peer",
        "lines": 20,
        "repo_name": "repo-a",
        "vm_user": "agent",
    }
    assert seeded_status["status"] == "active"
    assert seeded_status["agent_state"] == "running"
    assert seeded_status["last_state_update_at"] == "2026-03-29T12:00:00+00:00"
    assert mapped_status["status"] == "active"
    assert mapped_status["agent_state"] == "running"
    assert mapped_status["last_state_update_at"] == "2026-03-29T12:00:00+00:00"
    assert mapped_logs["lines"] == 20
    assert mapped_logs["log"] == "log-line-1\nlog-line-2"

    events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] in {ToolName.GET_AGENT_STATUS.value, ToolName.GET_AGENT_LOGS.value}
    ]
    assert [event["session_id"] for event in events] == ["session-1", "session-1", "session-1"]
    assert all(event["decision"] == "allowed" for event in events)
    assert "session_id is required" not in context.config.audit_log_path.read_text(encoding="utf-8")


def test_create_server_uses_configured_host_and_port(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.config.host = "0.0.0.0"
    context.config.port = 8000

    server = create_server(context)

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 8000


def test_discord_post_status_rejects_non_allowlisted_channel(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    set_discord_service(context, FakeDiscordService(commands=[]))
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS, ToolName.DISCORD_POST_STATUS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    set_discord_service(
        context,
        FakeDiscordService(
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
        ),
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

    get_result = call_tool(
        server, "discord_get_commands", {"session_id": "session-1", "since_cursor": None}
    )
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
    assert {"command_id", "author_id", "channel_id", "content", "metadata"}.issubset(
        get_result["commands"][0]
    )

    assert set(post_result) == {"status", "message_id", "channel_id", "session_id"}
    assert post_result["status"] == "posted"


def test_discord_get_commands_audits_adapter_http_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    fake_discord = FakeDiscordService(commands=[])
    fake_discord.get_commands = lambda since_cursor=None: (_ for _ in ()).throw(
        httpx.ReadTimeout("discord timeout")
    )
    set_discord_service(context, fake_discord)
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
    context.session_repository.upsert_session(
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
    set_discord_service(context, fake_discord)
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["chan-allowlisted"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    set_discord_service(
        context,
        FakeDiscordService(
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
        ),
    )
    server = create_server(context)
    result = call_tool(server, "discord_get_commands", {"session_id": "session-1"})

    assert result["commands"] == []
    events = read_audit_events(context.config.audit_log_path)
    decisions = [event["decision"] for event in events]
    assert decisions == ["denied", "allowed"]


def test_email_tools_require_and_return_mailbox_id(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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


def test_email_list_messages_pages_filtered_results_and_binds_cursor(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_MESSAGES],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    store = context.email_service.provider.email_store
    for message_id, received_at, unread, subject in (
        ("msg-new", datetime(2026, 3, 20, 12, tzinfo=UTC), False, "Invoice March"),
        ("msg-old", datetime(2026, 3, 19, 12, tzinfo=UTC), True, "Invoice February"),
        ("msg-ignore", datetime(2026, 3, 18, 12, tzinfo=UTC), False, "Newsletter"),
    ):
        store.put(
            message_id,
            store.model(
                message_id=message_id,
                mailbox_id="primary",
                folder="inbox",
                sender="vendor@example.com",
                recipients=["accounts@example.com"],
                subject=subject,
                body_text="payment details",
                unread=unread,
                received_at=received_at,
            ),
        )

    server = create_server(context)
    first_page = call_tool(
        server,
        "email_list_messages",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "folder": "inbox",
            "subject_contains": "invoice",
            "limit": 1,
        },
    )

    assert [item["message_id"] for item in first_page["messages"]] == ["msg-new"]
    assert first_page["messages"][0]["subject"] == "Invoice March"
    assert first_page["messages"][0]["unread"] is False
    assert first_page["next_cursor"]

    second_page = call_tool(
        server,
        "email_list_messages",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "folder": "inbox",
            "subject_contains": "invoice",
            "cursor": first_page["next_cursor"],
            "limit": 1,
        },
    )
    assert [item["message_id"] for item in second_page["messages"]] == ["msg-old"]
    assert second_page["next_cursor"] is None

    with pytest.raises(ToolError, match="email page cursor"):
        asyncio.run(
            server.call_tool(
                "email_list_messages",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "folder": "inbox",
                    "subject_contains": "newsletter",
                    "cursor": first_page["next_cursor"],
                    "limit": 1,
                },
            )
        )
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.EMAIL_LIST_MESSAGES.value
    assert event["decision"] == "error"
    assert event["result"]["error_class"] == "validation_error"


def test_email_list_messages_requires_explicit_tool_grant(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "email_list_messages",
                {"session_id": "session-1", "mailbox_id": "primary", "folder": "inbox"},
            )
        )


def test_email_tools_enforce_mailbox_and_folder_allowlists(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                {
                    "session_id": "session-1",
                    "mailbox_id": "secondary",
                    "folder": "inbox",
                    "limit": 10,
                },
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
    context.session_repository.upsert_session(
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
    context.session_repository.upsert_session(
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


def test_email_send_sends_recorded_draft(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_CREATE_DRAFT, ToolName.EMAIL_SEND],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com"],
            email_send_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    draft = call_tool(
        server,
        "email_create_draft",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
        },
    )
    result = call_tool(
        server,
        "email_send",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "draft_id": draft["draft_id"],
            "subject": "Status",
            "recipients": ["approved@example.com"],
        },
    )

    assert result["status"] == "local_sent"
    assert result["draft_id"] == draft["draft_id"]
    assert result["mailbox_id"] == "primary"
    snapshot = context.drafts.get(draft["draft_id"])
    assert snapshot is not None
    assert snapshot.send_status == "local_sent"
    assert snapshot.sent_at is not None


def test_canonical_email_send_requires_granted_recipient_target(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    write_root_communication_targets(
        context,
        [
            {
                "id": "primary-approved",
                "provider": "email",
                "kind": "email",
                "address": "approved@example.com",
                "policy": {"capabilities": ["write"], "auth_profile_ref": "email.primary"},
            }
        ],
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_CREATE_DRAFT, ToolName.EMAIL_SEND],
            communication_target_ids=["primary-approved"],
            email_mailbox_grants=[
                EmailMailboxGrant(mailbox_id="primary", access="send", folders=["drafts"])
            ],
            email_draft_recipient_allowlist=["approved@example.com"],
            email_send_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    server = create_server(context)

    draft = call_tool(
        server,
        "email_create_draft",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
        },
    )
    result = call_tool(
        server,
        "email_send",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "draft_id": draft["draft_id"],
            "subject": "Status",
            "recipients": ["approved@example.com"],
        },
    )

    assert result["communication_target_ids"] == ["primary-approved"]


def test_read_only_mailbox_grant_cannot_create_draft(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
            email_mailbox_grants=[
                EmailMailboxGrant(mailbox_id="primary", access="read", folders=["inbox"])
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="requires draft access"):
        call_tool(
            server,
            "email_create_draft",
            {"session_id": "session-1", "mailbox_id": "primary", "body": "draft"},
        )


def test_email_send_rejects_recipient_mismatch(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_CREATE_DRAFT, ToolName.EMAIL_SEND],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com", "other@example.com"],
            email_send_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    draft = call_tool(
        server,
        "email_create_draft",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
        },
    )

    with pytest.raises(ToolError, match="recipients must match"):
        asyncio.run(
            server.call_tool(
                "email_send",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "draft_id": draft["draft_id"],
                    "subject": "Status",
                    "recipients": ["other@example.com"],
                },
            )
        )


def test_email_send_rejects_attached_draft_without_attachment_send_tool(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                ToolName.EMAIL_SEND,
            ],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com"],
            email_send_enabled=True,
            communication_file_send_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    artifact = CommunicationArtifactStore(context.config.runtime_dir).stage(
        provider="email",
        session_id="session-1",
        content=b"report contents",
        original_filename="report.txt",
        mime_type="text/plain",
        source_mailbox="primary",
    )
    server = create_server(context)
    draft = call_tool(
        server,
        "email_create_draft_with_attachments",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
            "artifact_ids": [artifact.artifact_id],
        },
    )

    with pytest.raises(ToolError, match="use email_send_with_attachments"):
        asyncio.run(
            server.call_tool(
                "email_send",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "draft_id": draft["draft_id"],
                    "subject": "Status",
                    "recipients": ["approved@example.com"],
                },
            )
        )


def test_email_send_with_attachments_requires_exact_file_send_approval(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
            ],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com"],
            email_send_enabled=True,
            communication_file_send_enabled=True,
            communication_file_require_approval_for_send=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    artifact = CommunicationArtifactStore(context.config.runtime_dir).stage(
        provider="email",
        session_id="session-1",
        content=b"report contents",
        original_filename="report.txt",
        mime_type="text/plain",
        source_mailbox="primary",
    )
    server = create_server(context)
    draft = call_tool(
        server,
        "email_create_draft_with_attachments",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
            "artifact_ids": [artifact.artifact_id],
        },
    )
    send_arguments = {
        "session_id": "session-1",
        "mailbox_id": "primary",
        "draft_id": draft["draft_id"],
        "subject": "Status",
        "recipients": ["approved@example.com"],
        "artifact_ids": [artifact.artifact_id],
    }

    with pytest.raises(ToolError, match="requires approval"):
        asyncio.run(server.call_tool("email_send_with_attachments", send_arguments))

    approval_payload = {
        "session_id": "session-1",
        "provider": "email",
        "mailbox_id": "primary",
        "draft_id": draft["draft_id"],
        "recipients": ["approved@example.com"],
        "artifact_ids": [artifact.artifact_id],
        "subject": "Status",
    }
    approval = context.policy.create_approval(
        "session-1",
        ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value,
        approval_payload,
        "Send Status with report.txt",
    )
    approval.status = ApprovalStatus.APPROVED
    context.approvals.put(approval.request_id, approval)

    result = call_tool(
        server,
        "email_send_with_attachments",
        {**send_arguments, "request_id": approval.request_id},
    )

    assert result["status"] == "local_sent"
    assert result["artifact_ids"] == [artifact.artifact_id]
    events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] == ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value
    ]
    assert [event["decision"] for event in events] == ["denied", "allowed"]
    assert events[-1]["approval_request_id"] == approval.request_id
    assert events[-1]["result"]["approval_decision"] == ApprovalStatus.APPROVED.value


def test_email_send_with_attachments_separates_mcp_and_file_approval_ids(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
            ],
            approval_required_tools=[ToolName.EMAIL_SEND_WITH_ATTACHMENTS],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["drafts"]},
            email_draft_recipient_allowlist=["approved@example.com"],
            email_send_enabled=True,
            communication_file_send_enabled=True,
            communication_file_require_approval_for_send=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    artifact = CommunicationArtifactStore(context.config.runtime_dir).stage(
        provider="email",
        session_id="session-1",
        content=b"report contents",
        original_filename="report.txt",
        mime_type="text/plain",
        source_mailbox="primary",
    )
    server = create_server(context)
    draft = call_tool(
        server,
        "email_create_draft_with_attachments",
        {
            "session_id": "session-1",
            "mailbox_id": "primary",
            "body": "draft body",
            "subject": "Status",
            "recipients": ["approved@example.com"],
            "artifact_ids": [artifact.artifact_id],
        },
    )
    send_arguments = {
        "session_id": "session-1",
        "mailbox_id": "primary",
        "draft_id": draft["draft_id"],
        "subject": "Status",
        "recipients": ["approved@example.com"],
        "artifact_ids": [artifact.artifact_id],
    }
    file_approval_payload = {
        "session_id": "session-1",
        "provider": "email",
        "mailbox_id": "primary",
        "draft_id": draft["draft_id"],
        "recipients": ["approved@example.com"],
        "artifact_ids": [artifact.artifact_id],
        "subject": "Status",
    }
    file_approval = context.policy.create_approval(
        "session-1",
        ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value,
        file_approval_payload,
        "Send Status with report.txt",
    )
    file_approval.status = ApprovalStatus.APPROVED
    context.approvals.put(file_approval.request_id, file_approval)
    mcp_arguments = {**send_arguments, "request_id": file_approval.request_id}
    mcp_approval_payload = {
        "tool_name": ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value,
        "session_id": "session-1",
        "arguments": mcp_arguments,
    }
    mcp_approval = context.policy.create_approval(
        "session-1",
        "mcp_tool_invocation",
        mcp_approval_payload,
        "Allow email_send_with_attachments for session-1",
        tool_name=ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value,
    )
    mcp_approval.status = ApprovalStatus.APPROVED
    context.approvals.put(mcp_approval.request_id, mcp_approval)

    result = call_tool(
        server,
        "email_send_with_attachments",
        {**mcp_arguments, "approval_request_id": mcp_approval.request_id},
    )

    assert result["status"] == "local_sent"
    assert result["artifact_ids"] == [artifact.artifact_id]
    events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event["tool_name"] == ToolName.EMAIL_SEND_WITH_ATTACHMENTS.value
    ]
    assert [event["decision"] for event in events] == ["allowed"]
    assert events[0]["approval_request_id"] == mcp_approval.request_id
    assert events[0]["args"]["request_id"] == file_approval.request_id
    assert events[0]["result"]["approval_request_id"] == file_approval.request_id
    assert events[0]["result"]["approval_decision"] == ApprovalStatus.APPROVED.value
    consumed_mcp_approval = context.approvals.get(mcp_approval.request_id)
    assert consumed_mcp_approval is not None
    assert consumed_mcp_approval.status == ApprovalStatus.EXHAUSTED
    accepted_file_approval = context.approvals.get(file_approval.request_id)
    assert accepted_file_approval is not None
    assert accepted_file_approval.status == ApprovalStatus.APPROVED


def test_email_get_message_prevents_cross_mailbox_access(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                {
                    "session_id": "session-1",
                    "mailbox_id": "secondary",
                    "message_id": "msg-primary-1",
                },
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
        self.last_limit: int | None = None
        self.last_session_id: str | None = None

    def has_pending_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
    ) -> SimpleNamespace:
        self.last_streams = streams
        self.last_session_id = session_id
        self.last_cursor = cursor
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "pending": True,
                "next_cursor": "2026-01-01T00:00:00Z",
            }
        )

    def list_unread_feedback(
        self,
        *,
        streams: list[dict[str, str]],
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> SimpleNamespace:
        self.last_streams = streams
        self.last_session_id = session_id
        self.last_cursor = cursor
        self.last_limit = limit
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
                    "inline_media": [
                        {
                            "source_kind": "pr_feedback",
                            "provider": "github",
                            "resource_id": "github:review_comment:7",
                            "comment_id": "7",
                            "media_id": "markdown-image-0",
                            "filename": "actual.png",
                            "mime_type": "image/png",
                            "url": "https://example.test/actual.png",
                        }
                    ],
                }
            ],
            "next_cursor": "2026-01-01T00:00:00Z",
        }
        return SimpleNamespace(
            feedback=[payload["feedback"][0]],
            next_cursor=payload["next_cursor"],
            model_dump=lambda mode="json": payload,
        )

    def list_all_feedback(
        self, *, streams: list[dict[str, str]], limit: int | None = None
    ) -> SimpleNamespace:
        self.last_streams = streams
        self.last_cursor = None
        self.last_limit = limit
        payload = {
            "feedback": [
                {
                    "feedback_id": "github:review_comment:1",
                    "provider": "github",
                    "repo": "org/repo",
                    "pr_id": "17",
                    "author": "reviewer",
                    "body": "Older comment",
                    "created_at": "2025-12-31T00:00:00Z",
                },
                {
                    "feedback_id": "github:review_comment:7",
                    "provider": "github",
                    "repo": "org/repo",
                    "pr_id": "17",
                    "author": "reviewer",
                    "body": "Please rename this variable.",
                    "created_at": "2026-01-01T00:00:00Z",
                    "inline_media": [
                        {
                            "source_kind": "pr_feedback",
                            "provider": "github",
                            "resource_id": "github:review_comment:7",
                            "comment_id": "7",
                            "media_id": "markdown-image-0",
                            "filename": "actual.png",
                            "mime_type": "image/png",
                            "url": "https://example.test/actual.png",
                        }
                    ],
                },
            ],
            "next_cursor": "2026-01-01T00:00:00Z",
        }
        return SimpleNamespace(
            feedback=payload["feedback"],
            next_cursor=payload["next_cursor"],
            model_dump=lambda mode="json": payload,
        )


class _FakePrReplyService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post_reply(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, object]] | None = None,
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
                "mentions": mentions or [],
                "attachments": attachments,
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    set_email_service(
        context,
        _RaisingEmailService(
            ProviderRequestError("provider auth failed", status_code=401, attempt=1, max_attempts=1)
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="provider auth failed"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "folder": "inbox",
                    "limit": 10,
                },
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
            email_mailbox_allowlist=["primary"],
            email_folder_allowlist_by_mailbox={"primary": ["inbox"]},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    set_email_service(context, _RaisingEmailService(httpx.TimeoutException("provider timed out")))
    server = create_server(context)

    with pytest.raises(ToolError, match="provider timed out"):
        asyncio.run(
            server.call_tool(
                "email_list_unread",
                {
                    "session_id": "session-1",
                    "mailbox_id": "primary",
                    "folder": "inbox",
                    "limit": 10,
                },
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "automation_role": "review-bot",
                "user_id": "user-42",
                "workflow_run_id": "workflow-17",
                "trace_id": "trace-17",
            },
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

    result = call_tool_with_context(
        server,
        "approval_submit",
        {
            "session_id": "session-1",
            "action_type": "email_send",
            "payload": payload,
            "human_summary": "send summary",
        },
        make_request_context(
            session_id="session-1",
            transport_session_id="transport-1",
            request_id="request-1",
            correlation_id="correlation-1",
        ),
    )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["decision"] == "allowed"
    assert event["directory_path"] == "root/team-a"
    assert event["agent_role"] == "review-bot"
    assert event["actor_user_id"] == "user-42"
    assert event["request_id"] == "request-1"
    assert event["correlation_id"] == "correlation-1"
    assert event["approval_request_id"] == result["request_id"]
    assert event["workflow_run_id"] == "workflow-17"
    assert event["trace_id"] == "trace-17"
    assert event["duration_ms"] is not None
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "automation_role": "review-bot",
                "user_id": "user-42",
                "workflow_run_id": "workflow-17",
                "trace_id": "trace-17",
            },
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="is not allowed"):
        call_tool_with_context(
            server,
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
            make_request_context(
                session_id="session-1",
                transport_session_id="transport-1",
                request_id="request-denied",
                correlation_id="correlation-denied",
            ),
        )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["decision"] == "denied"
    assert event["directory_path"] == "root/team-a"
    assert event["agent_role"] == "review-bot"
    assert event["actor_user_id"] == "user-42"
    assert event["request_id"] == "request-denied"
    assert event["correlation_id"] == "correlation-denied"
    assert event["workflow_run_id"] == "workflow-17"
    assert event["trace_id"] == "trace-17"
    assert event["duration_ms"] is not None
    assert event["args"]["payload"]["body"] == "<redacted>"
    assert event["args"]["payload"]["authorization"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["token"] == "<redacted>"
    assert event["args"]["payload"]["nested"]["safe"] == "visible"
    assert event["args"]["payload"]["recipients"][0]["email"] == "still-visible@example.com"
    assert event["args"]["payload"]["recipients"][0]["token"] == "<redacted>"


def test_audit_redaction_uses_directory_policy_redacted_keys(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    write_root_policy_overlay(context, {"redacted_keys": ["api_key"]})
    context.session_repository.upsert_session(
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
                    "payload": {"api_key": "secret", "safe": "visible"},
                    "human_summary": "should fail",
                },
            )
        )

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["args"]["payload"]["api_key"] == "<redacted>"
    assert event["args"]["payload"]["safe"] == "visible"


def test_has_pending_feedback_returns_minimal_heartbeat_payload(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.HAS_PENDING_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_UNREAD_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )
    context.pr_feedback_service = _FakePrFeedbackService()
    server = create_server(context)

    result = call_tool(
        server,
        "list_unread_feedback",
        {"session_id": "session-1", "cursor": "2025-12-31T00:00:00Z", "limit": 1},
    )

    assert result["feedback"][0]["feedback_id"] == "github:review_comment:7"
    assert result["feedback"][0]["inline_media"][0]["media_id"] == "markdown-image-0"
    assert result["feedback"][0]["inline_media"][0]["url"] == "https://example.test/actual.png"
    assert result["next_cursor"] == "2026-01-01T00:00:00Z"
    assert context.pr_feedback_service.last_limit == 1
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.LIST_UNREAD_FEEDBACK.value
    assert event["result"] == {"count": 1, "next_cursor": "2026-01-01T00:00:00Z"}


def test_schedule_resume_persists_request_and_audits_success(tmp_path: Path) -> None:
    legacy_wake_store_path = tmp_path / "wake-requests-store.json"
    legacy_wake_store_path.write_text("   \n", encoding="utf-8")

    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-real",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    result = call_tool(
        server,
        "schedule_resume",
        {
            "session_id": "session-1",
            "resume_at": "2026-04-01T10:30:00Z",
            "prompt": "Continue working through unread feedback.",
            "request_id": "wake-123",
            "source": "wake-worker",
            "metadata": {"trace_id": "trace-1"},
        },
    )

    assert result == {
        "session_id": "session-1",
        "resume_at": "2026-04-01T10:30:00Z",
        "cron": None,
        "scheduled": True,
        "request_id": "wake-123",
    }
    saved_request = context.wake_store.get_request("wake-123")
    assert saved_request is not None
    assert context.wake_store.path == tmp_path / "wake-requests-store.db"
    assert legacy_wake_store_path.read_text(encoding="utf-8") == "   \n"
    assert saved_request.session_id == "session-1"
    assert saved_request.schedule.resume_at == datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    assert saved_request.resume_prompt.prompt == "Continue working through unread feedback."
    assert saved_request.source == "wake-worker"
    assert saved_request.resume_prompt.metadata == {"trace_id": "trace-1"}

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.SCHEDULE_RESUME.value
    assert event["decision"] == "allowed"
    assert event["directory_path"] == "root"
    assert event["request_id"] == "wake-123"
    assert event["approval_request_id"] is None
    assert event["duration_ms"] is not None
    assert event["args"]["prompt"] == "<redacted>"
    assert event["result"] == {
        "session_id": "session-1",
        "resume_at": "2026-04-01T10:30:00Z",
        "request_id": "wake-123",
        "scheduled": True,
    }


def test_schedule_resume_persists_request_for_container_worker_vm_without_vm_binding(
    tmp_path: Path,
) -> None:
    legacy_wake_store_path = tmp_path / "wake-requests-store.json"
    legacy_wake_store_path.write_text("   \n", encoding="utf-8")

    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    result = call_tool(
        server,
        "schedule_resume",
        {
            "session_id": "session-1",
            "resume_at": "2026-04-01T10:30:00Z",
            "prompt": "Continue working through unread feedback.",
            "request_id": "wake-123",
            "source": "wake-worker",
            "metadata": {"trace_id": "trace-1"},
        },
    )

    assert result == {
        "session_id": "session-1",
        "resume_at": "2026-04-01T10:30:00Z",
        "cron": None,
        "scheduled": True,
        "request_id": "wake-123",
    }
    saved_request = context.wake_store.get_request("wake-123")
    assert saved_request is not None
    assert context.wake_store.path == tmp_path / "wake-requests-store.db"
    assert legacy_wake_store_path.read_text(encoding="utf-8") == "   \n"
    assert saved_request.session_id == "session-1"
    assert saved_request.schedule.resume_at == datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    assert saved_request.resume_prompt.prompt == "Continue working through unread feedback."
    assert saved_request.source == "wake-worker"
    assert saved_request.resume_prompt.metadata == {"trace_id": "trace-1"}

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.SCHEDULE_RESUME.value
    assert event["decision"] == "allowed"
    assert event["directory_path"] == "root"
    assert event["request_id"] == "wake-123"
    assert event["approval_request_id"] is None
    assert event["duration_ms"] is not None
    assert event["args"]["prompt"] == "<redacted>"
    assert event["result"] == {
        "session_id": "session-1",
        "resume_at": "2026-04-01T10:30:00Z",
        "request_id": "wake-123",
        "scheduled": True,
    }


def test_schedule_resume_rejects_non_utc_timestamps(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-real",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="timezone information"):
        call_tool(
            server,
            "schedule_resume",
            {
                "session_id": "session-1",
                "resume_at": "2026-04-01T10:30:00",
                "prompt": "Continue",
            },
        )


def test_cancel_scheduled_wake_persists_request_and_is_idempotent(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Continue working through unread feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-123",
        source="wake-worker",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )
    server = create_server(context)

    result = call_tool(
        server,
        "cancel_scheduled_wake",
        {
            "session_id": "session-1",
            "request_id": "wake-123",
            "reason": "No longer needed.",
            "metadata": {"trace_id": "trace-1", "cancelled_by": "agent"},
        },
    )

    assert result["session_id"] == "session-1"
    assert result["request_id"] == "wake-123"
    assert result["status"] == "cancelled"
    assert result["request"]["status"] == "cancelled"
    assert result["request"]["cancelled_by_session_id"] == "session-1"
    assert result["request"]["cancel_reason"] == "No longer needed."
    assert result["request"]["cancel_metadata"] == {
        "trace_id": "trace-1",
        "cancelled_by": "agent",
    }
    saved_request = context.wake_store.get_request("wake-123")
    assert saved_request is not None
    assert saved_request.status == "cancelled"
    assert saved_request.enabled is False
    assert saved_request.next_run_at is None
    assert saved_request.cancelled_by_session_id == "session-1"
    assert saved_request.cancel_reason == "No longer needed."
    assert saved_request.cancel_metadata == {
        "trace_id": "trace-1",
        "cancelled_by": "agent",
    }
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.CANCEL_SCHEDULED_WAKE.value
    assert event["decision"] == "allowed"
    assert event["result"]["status"] == "cancelled"
    assert event["result"]["request"]["status"] == "cancelled"

    repeated = call_tool(
        server,
        "cancel_scheduled_wake",
        {"session_id": "session-1", "request_id": "wake-123"},
    )

    assert repeated["status"] == "already_cancelled"
    assert repeated["request"]["status"] == "cancelled"


def test_cancel_scheduled_wake_requires_tool_permission_for_owned_wake(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Continue working through unread feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-123",
        source="wake-worker",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )
    before = context.wake_store.read_raw()["requests"]["wake-123"]
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool cancel_scheduled_wake is not allowed"):
        call_tool(
            server,
            "cancel_scheduled_wake",
            {
                "session_id": "session-1",
                "request_id": "wake-123",
                "reason": "No longer needed.",
            },
        )

    assert context.wake_store.read_raw()["requests"]["wake-123"] == before
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.CANCEL_SCHEDULED_WAKE.value
    assert event["decision"] == "denied"
    assert event["args"]["reason"] == "<redacted>"
    assert event["result"]["error_class"] == "policy_error"


def test_cancel_scheduled_wake_denies_cross_session_and_redacts_reason(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-2",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Continue working through unread feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-123",
        source="wake-worker",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )
    before = context.wake_store.read_raw()["requests"]["wake-123"]
    server = create_server(context)

    result = call_tool(
        server,
        "cancel_scheduled_wake",
        {
            "session_id": "session-2",
            "request_id": "wake-123",
            "reason": "keep this private",
            "metadata": {"trace_id": "trace-2"},
        },
    )

    assert result["status"] == "permission_denied"
    assert result["request_id"] == "wake-123"
    assert context.wake_store.read_raw()["requests"]["wake-123"] == before
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.CANCEL_SCHEDULED_WAKE.value
    assert event["decision"] == "denied"
    assert event["args"]["reason"] == "<redacted>"
    assert event["result"]["error_class"] == "policy_error"


@pytest.mark.parametrize(
    "dispatch_status",
    [
        "dispatched",
        "queued",
    ],
)
def test_cancel_scheduled_wake_allows_nonterminal_one_time_requests(
    tmp_path: Path,
    dispatch_status: str,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Continue working through unread feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id=f"wake-{dispatch_status}",
        source="wake-worker",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )
    context.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id=f"dispatch-{dispatch_status}",
            request_id=f"wake-{dispatch_status}",
            session_id="session-1",
            status=dispatch_status,
            resume_prompt=WakeResumePromptPayload(
                prompt="Continue working through unread feedback.",
                metadata={"trace_id": "trace-1"},
            ),
            scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
            source="wake-worker",
        )
    )
    before = context.wake_store.read_raw()["requests"][f"wake-{dispatch_status}"]
    server = create_server(context)

    result = call_tool(
        server,
        "cancel_scheduled_wake",
        {
            "session_id": "session-1",
            "request_id": f"wake-{dispatch_status}",
            "reason": "obsolete",
        },
    )

    assert result["status"] == "cancelled"
    assert result["request"]["status"] == "cancelled"
    assert result["request"]["enabled"] is False
    assert result["request"]["next_run_at"] is None
    assert context.wake_store.read_raw()["requests"][f"wake-{dispatch_status}"] != before


def test_cancel_scheduled_wake_preserves_completed_one_time_requests(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Continue working through unread feedback.",
        schedule=WakeScheduleDefinition(resume_at=datetime(2026, 4, 1, 10, 30, tzinfo=UTC)),
        request_id="wake-completed",
        source="wake-worker",
        resume_mode="same_context",
        enabled=True,
        metadata={"trace_id": "trace-1"},
    )
    context.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-completed",
            request_id="wake-completed",
            session_id="session-1",
            status="completed",
            resume_prompt=WakeResumePromptPayload(
                prompt="Continue working through unread feedback.",
                metadata={"trace_id": "trace-1"},
            ),
            scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
            source="wake-worker",
        )
    )
    before = context.wake_store.read_raw()["requests"]["wake-completed"]
    server = create_server(context)

    result = call_tool(
        server,
        "cancel_scheduled_wake",
        {
            "session_id": "session-1",
            "request_id": "wake-completed",
            "reason": "obsolete",
        },
    )

    assert result["status"] == "already_completed"
    assert context.wake_store.read_raw()["requests"]["wake-completed"] == before


def test_cancel_scheduled_wake_reports_missing_and_unknown_request_ids(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    missing = call_tool(
        server,
        "cancel_scheduled_wake",
        {"session_id": "session-1", "request_id": ""},
    )
    unknown = call_tool(
        server,
        "cancel_scheduled_wake",
        {"session_id": "session-1", "request_id": "missing"},
    )

    assert missing["status"] == "missing_request_id"
    assert missing["request_id"] is None
    assert missing["request"] is None
    assert unknown["status"] == "not_found"
    assert unknown["request_id"] == "missing"
    assert unknown["request"] is None


def test_run_script_persists_request_and_audits_success(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            allowed_script_names=["deploy-review"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    result = call_tool(
        server,
        "run_script",
        {
            "session_id": "session-1",
            "script_name": "deploy-review",
            "parameters": ["--pr", "42"],
        },
    )

    assert result["script_name"] == "deploy-review"
    assert result["parameters"] == ["--pr", "42"]
    assert result["queued"] is True

    stored = json.loads((tmp_path / "script-run-requests-store.json").read_text(encoding="utf-8"))
    saved_request = next(iter(stored["requests"].values()))
    assert saved_request["session_id"] == "session-1"
    assert saved_request["script_name"] == "deploy-review"
    assert saved_request["parameters"] == ["--pr", "42"]
    assert saved_request["directory_path"] == "root"

    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.RUN_SCRIPT.value
    assert event["decision"] == "allowed"
    assert event["args"]["parameters"] == "<redacted>"
    assert event["result"]["script_name"] == "deploy-review"
    assert event["result"]["queued"] is True
    assert event["result"]["parameter_count"] == 2


def test_list_all_feedback_returns_history_including_already_read_items(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_ALL_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )
    context.pr_feedback_service = _FakePrFeedbackService()
    server = create_server(context)

    result = call_tool(
        server,
        "list_all_feedback",
        {"session_id": "session-1", "limit": 2},
    )

    assert [item["feedback_id"] for item in result["feedback"]] == [
        "github:review_comment:1",
        "github:review_comment:7",
    ]
    assert result["feedback"][1]["inline_media"][0]["media_id"] == "markdown-image-0"
    assert context.pr_feedback_service.last_cursor is None
    assert context.pr_feedback_service.last_limit == 2
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.LIST_ALL_FEEDBACK.value
    assert event["result"] == {"count": 2, "next_cursor": "2026-01-01T00:00:00Z"}


def test_post_pr_feedback_reply_calls_provider_and_audits_reply_identifiers(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )
    fake_service = _FakePrReplyService()
    set_pr_reply_service(context, fake_service)
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
    assert fake_service.calls[0]["body_text"] == "Applied in the latest patch."
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.POST_PR_FEEDBACK_REPLY.value
    assert event["result"] == {
        "status": "posted",
        "provider": "github",
        "reply_id": "reply-1",
        "reply_url": "https://example.test/replies/1",
    }


def test_post_pr_inline_comment_calls_publish_service_and_audits_inline_target(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_INLINE_COMMENT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class FakePrPublishService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post_inline_comment(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            return {
                "status": "posted",
                "provider": "azure_devops",
                "repo": kwargs["repo"],
                "pr_id": kwargs["pr_id"],
                "review_id": None,
                "review_url": "https://ado.test/threads/45",
                "comment_ids": ["11"],
                "comment_urls": ["https://ado.test/threads/45"],
                "published_feedback_ids": ["azure_devops:thread_comment:45:11"],
            }

    fake_service = FakePrPublishService()
    context.pr_publish_service = fake_service
    server = create_server(context)

    result = call_tool(
        server,
        "post_pr_inline_comment",
        {
            "session_id": "session-1",
            "provider": "azure-devops",
            "repo": "repo-1",
            "pr_id": "17",
            "path": "src/app.py",
            "line": 17,
            "body_text": "Fix this line.",
            "idempotency_key": "inline-1",
        },
    )

    assert result["comment_ids"] == ["11"]
    assert fake_service.calls == [
        {
            "provider": "azure_devops",
            "repo": "repo-1",
            "pr_id": "17",
            "path": "src/app.py",
            "line": 17,
            "body_text": "Fix this line.",
            "start_line": None,
            "side": "RIGHT",
            "attachments": [],
            "idempotency_key": "inline-1",
        }
    ]
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.POST_PR_INLINE_COMMENT.value
    assert event["args"]["path"] == "src/app.py"
    assert event["args"]["line"] == 17
    assert event["result"] == {
        "status": "posted",
        "provider": "azure_devops",
        "review_id": None,
        "review_url": "https://ado.test/threads/45",
        "comment_count": 1,
    }


def test_post_pr_feedback_reply_normalizes_azure_devops_provider_alias(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [
                    {
                        "provider": "azure_devops",
                        "repo": "exampleindustry/Example Industry APP/ExampleIndustryAPP",
                        "pr_id": "5857",
                    }
                ]
            },
        ),
    )
    fake_service = _FakePrReplyService()
    set_pr_reply_service(context, fake_service)
    server = create_server(context)

    result = call_tool(
        server,
        "post_pr_feedback_reply",
        {
            "session_id": "session-1",
            "provider": "azure-devops",
            "repo": "exampleindustry/Example Industry APP/ExampleIndustryAPP",
            "pr_id": "5857",
            "thread_id": "29797",
            "comment_id": "1",
            "body_text": "Applied in the latest patch.",
        },
    )

    assert result["provider"] == "azure_devops"
    assert fake_service.calls[0]["provider"] == "azure_devops"
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["args"]["provider"] == "azure_devops"
    assert event["result"]["provider"] == "azure_devops"


def test_post_pr_feedback_reply_rejects_missing_target_identifiers(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )
    set_pr_reply_service(context, _FakePrReplyService())
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


def test_post_pr_feedback_reply_uses_directory_policy_for_approval_requirement(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    write_root_policy_overlay(context, {"pr_reply_require_approval": True})
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )
    set_pr_reply_service(context, _FakePrReplyService())
    server = create_server(context)

    with pytest.raises(ToolError, match="request_id is required"):
        call_tool(
            server,
            "post_pr_feedback_reply",
            {
                "session_id": "session-1",
                "provider": "github",
                "repo": "org/repo",
                "pr_id": "17",
                "comment_id": "91",
                "body_text": "Applied in the latest patch.",
            },
        )
