from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncio
import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import (
    _assistant_mutation_audit_result,
    _filter_discord_commands_for_session,
    _resolve_session_id_from_context,
    create_app,
    create_server,
)
from agent_sandbox.admin.web.chat import ChatStore
from agent_sandbox.models import (
    DiscordCommandEnvelope,
    ListPullRequestsResponse,
    PullRequestItem,
    SessionManifest,
    ToolName,
)
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import build_runtime
from agent_sandbox.worker.models import (
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseState,
)


def _call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def _command(command_id: str, *, source: str) -> DiscordCommandEnvelope:
    return DiscordCommandEnvelope(
        command_id=command_id,
        author_id="user-1",
        channel_id="chan-1",
        content="!status",
        created_at=datetime.now(UTC),
        metadata={"source": source, "channel_id": "chan-1", "author_id": "user-1"},
    )


def test_filter_discord_commands_returns_raw_stream_cursor_with_denials() -> None:
    allowed = _command("cmd_allowed", source="discord_bot_api")
    denied = _command("cmd_denied", source="discord_bot_api_status")
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class AllowOnlyTrustedPolicy:
        def require_discord_command_source(
            self, _session: SessionManifest, command: DiscordCommandEnvelope
        ) -> None:
            if command.metadata.get("source") != "discord_bot_api":
                raise PolicyError(f"Discord command {command.command_id} has untrusted source")

    denied_audits: list[dict[str, str]] = []

    def audit_failure(
        _session_id: str,
        _tool_name: ToolName,
        denied_args: dict[str, str],
        _error: str,
    ) -> None:
        denied_audits.append(denied_args)

    commands, denied_count, next_cursor = _filter_discord_commands_for_session(
        SimpleNamespace(policy=AllowOnlyTrustedPolicy()),
        "s1",
        session,
        [allowed, denied],
        {"session_id": "s1", "since_cursor": "cmd_prev"},
        audit_failure,
    )

    assert [command["command_id"] for command in commands] == ["cmd_allowed"]
    assert denied_count == 1
    assert next_cursor == "cmd_denied"
    assert denied_audits[0]["command_id"] == "cmd_denied"


def test_resolve_session_id_from_context_prefers_explicit_value() -> None:
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={"session_id": "query-session"}, headers={})
        )
    )

    assert _resolve_session_id_from_context("explicit-session", ctx) == "explicit-session"


def test_resolve_session_id_from_context_reads_query_param_then_header() -> None:
    transport_map: dict[str, str] = {}
    query_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                query_params={"session_id": "query-session"},
                headers={"mcp-session-id": "transport-1"},
            )
        )
    )
    header_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                query_params={}, headers={"x-agent-sandbox-session-id": "header-session"}
            )
        )
    )
    mapped_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={}, headers={"mcp-session-id": "transport-1"})
        )
    )

    assert (
        _resolve_session_id_from_context(None, query_ctx, transport_session_map=transport_map)
        == "query-session"
    )
    assert transport_map == {"transport-1": "query-session"}
    assert _resolve_session_id_from_context(None, header_ctx) == "header-session"
    assert (
        _resolve_session_id_from_context(None, mapped_ctx, transport_session_map=transport_map)
        == "query-session"
    )


def test_resolve_session_id_from_context_requires_value() -> None:
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(request=SimpleNamespace(query_params={}, headers={}))
    )

    with pytest.raises(ToolError, match="session_id is required"):
        _resolve_session_id_from_context(None, ctx)


def test_create_server_registers_schedule_resume_tool(tmp_path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    server = create_server(context)

    assert any(tool.name == "schedule_resume" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "run_script" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "list_pull_requests" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "get_pull_request" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "assistant_read_config" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "make_phone_call" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "get_phone_call_result" for tool in server._tool_manager.list_tools())
    assert any(
        tool.name == "mssql_provision_database" for tool in server._tool_manager.list_tools()
    )
    assert any(tool.name == "mssql_list_databases" for tool in server._tool_manager.list_tools())
    assert any(tool.name == "mssql_drop_database" for tool in server._tool_manager.list_tools())
    make_phone_call_tool = next(
        tool for tool in server._tool_manager.list_tools() if tool.name == "make_phone_call"
    )
    make_phone_call_fields = set(make_phone_call_tool.parameters["properties"])
    assert {"contact", "prompt", "session_id", "request_id", "metadata"}.issubset(
        make_phone_call_fields
    )
    assert "phone_number" not in make_phone_call_fields
    assert "to_number" not in make_phone_call_fields


def test_request_pr_review_schema_advertises_allowed_provider_values(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    server = create_server(context)

    request_pr_review_tool = next(
        tool for tool in server._tool_manager.list_tools() if tool.name == "request_pr_review"
    )
    provider_schema = request_pr_review_tool.parameters["properties"]["provider"]

    assert provider_schema["type"] == "string"
    assert provider_schema["enum"] == ["github", "azure_devops"]


def test_mssql_tools_call_container_worker_backend_and_redact_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
            execution_backend="container_worker_vm",
            container_worker_base_url="http://worker.test",
            container_worker_mssql_enabled=True,
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.MSSQL_PROVISION_DATABASE,
                ToolName.MSSQL_LIST_DATABASES,
                ToolName.MSSQL_DROP_DATABASE,
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    calls: list[tuple[str, str]] = []

    class FakeContainerWorkerBackend:
        def __init__(self, *, config: GatewayConfig) -> None:
            assert config.container_worker_mssql_enabled is True

        def provision_mssql_database(
            self,
            *,
            session_id: str,
            logical_name: str,
        ) -> WorkerMssqlDatabaseRecord:
            calls.append(("provision", logical_name))
            return WorkerMssqlDatabaseRecord(
                session_id=session_id,
                logical_name=logical_name,
                database_name="asb_hash_app_hash",
                server="172.17.0.1",
                port=1433,
                username="asb_login_hash",
                password="session-secret",
                connection_string="Server=tcp:172.17.0.1,1433;Password=session-secret",
            )

        def list_mssql_databases(self, *, session_id: str) -> WorkerMssqlDatabaseListRecord:
            calls.append(("list", session_id))
            return WorkerMssqlDatabaseListRecord(
                session_id=session_id,
                databases=[
                    WorkerMssqlDatabaseState(
                        logical_name="app",
                        database_name="asb_hash_app_hash",
                    )
                ],
            )

        def drop_mssql_database(
            self,
            *,
            session_id: str,
            logical_name: str,
        ) -> WorkerMssqlDatabaseDeleteRecord:
            calls.append(("drop", logical_name))
            return WorkerMssqlDatabaseDeleteRecord(
                session_id=session_id,
                logical_name=logical_name,
                database_name="asb_hash_app_hash",
                deleted=True,
            )

    monkeypatch.setattr(
        "agent_sandbox.gateway.ContainerWorkerVmExecutionBackend",
        FakeContainerWorkerBackend,
    )
    server = create_server(context)

    provisioned = _call_tool(
        server,
        "mssql_provision_database",
        {"session_id": "session-1", "logical_name": "app"},
    )
    listed = _call_tool(server, "mssql_list_databases", {"session_id": "session-1"})
    dropped = _call_tool(
        server,
        "mssql_drop_database",
        {"session_id": "session-1", "logical_name": "app"},
    )

    assert provisioned["password"] == "session-secret"
    assert listed["databases"][0]["logical_name"] == "app"
    assert dropped["deleted"] is True
    assert calls == [("provision", "app"), ("list", "session-1"), ("drop", "app")]
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "session-secret" not in audit_text
    assert "<redacted>" in audit_text


def test_mssql_provision_database_requires_allowed_tool(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
            execution_backend="container_worker_vm",
            container_worker_base_url="http://worker.test",
            container_worker_mssql_enabled=True,
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError):
        _call_tool(
            server,
            "mssql_provision_database",
            {"session_id": "session-1", "logical_name": "app"},
        )


def test_assistant_mutation_audit_result_redacts_before_after_summary() -> None:
    args = {
        "before": {"api_key": "before-secret", "safe": "visible"},
        "after": {"nested": {"token": "after-token", "safe": "still-visible"}},
    }

    result = _assistant_mutation_audit_result(
        ToolName.ASSISTANT_UPSERT_CONFIG,
        args,
        {"before_after_summary": {"before": args["before"], "after": args["after"]}},
        {"api_key", "token"},
        default_status="success",
    )

    assert result["status"] == "success"
    assert result["before_after_summary"]["before"]["api_key"] == "<redacted>"
    assert result["before_after_summary"]["before"]["safe"] == "visible"
    assert result["before_after_summary"]["after"]["nested"]["token"] == "<redacted>"
    assert result["before_after_summary"]["after"]["nested"]["safe"] == "still-visible"
    assert "before-secret" not in json.dumps(result)
    assert "after-token" not in json.dumps(result)


def test_assistant_mutation_audit_result_redacts_full_result() -> None:
    result = _assistant_mutation_audit_result(
        ToolName.ASSISTANT_UPSERT_CONFIG,
        {},
        {
            "config": {
                "token": "result-token",
                "nested": {"api_key": "result-api-key", "safe": "visible"},
            },
            "safe": "still-visible",
        },
        {"api_key", "token"},
        default_status="success",
    )

    assert result["status"] == "success"
    assert result["config"]["token"] == "<redacted>"
    assert result["config"]["nested"]["api_key"] == "<redacted>"
    assert result["config"]["nested"]["safe"] == "visible"
    assert result["safe"] == "still-visible"
    assert "result-token" not in json.dumps(result)
    assert "result-api-key" not in json.dumps(result)


def test_assistant_list_agents_defaults_to_all_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            allowed_tools=[ToolName.ASSISTANT_LIST_AGENTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
            metadata={"capability_profile": "read-only"},
        )
    )
    list_agents_kwargs: dict[str, Any] = {}

    def fake_list_agents(self: AdminService, **kwargs: Any) -> list[object]:
        del self
        list_agents_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(AdminService, "list_agents", fake_list_agents)
    server = create_server(context)

    result = _call_tool(
        server,
        "assistant_list_agents",
        {"session_id": "assistant-session"},
    )

    assert list_agents_kwargs["directory_path"] == "root"
    assert list_agents_kwargs["all_directories"] is True
    assert result["all_directories"] is True
    assert result["directory_path"] is None


def test_assistant_read_config_returns_directory_global_and_worker_config(
    tmp_path: Path,
) -> None:
    service = AdminService(tmp_path)
    created = service.create_directory_child("root", "team")
    assert created.ok
    root_write = service.write_directory_domain_config(
        "root", "policy", {"default_session_ttl_seconds": 111}
    )
    assert root_write.ok
    team_write = service.write_directory_domain_config(
        "root/team", "policy", {"approval_ttl_seconds": 222}
    )
    assert team_write.ok
    service.write_assistant_settings(
        {"enabled": True, "default_profile": "read-only", "read_only": True}
    )
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            allowed_tools=[ToolName.ASSISTANT_READ_CONFIG],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root/team",
            metadata={"capability_profile": "read-only"},
        )
    )
    server = create_server(context)

    directory_result = _call_tool(
        server,
        "assistant_read_config",
        {"session_id": "assistant-session", "scope": "directory", "domain": "policy"},
    )

    assert directory_result["scope"] == "directory"
    assert directory_result["directory_path"] == "root/team"
    assert directory_result["raw"]["exists"] is True
    assert directory_result["raw"]["config"] == {"approval_ttl_seconds": 222}
    assert (
        directory_result["effective"]["config"]["default_session_ttl_seconds"] == 111
    )
    assert directory_result["effective"]["config"]["approval_ttl_seconds"] == 222

    global_result = _call_tool(
        server,
        "assistant_read_config",
        {
            "session_id": "assistant-session",
            "scope": "global",
            "domain": "assistant_settings",
        },
    )

    assert global_result["scope"] == "global"
    assert global_result["domain"] == "assistant_settings"
    assert global_result["exists"] is True
    assert global_result["config"]["enabled"] is True

    host_result = _call_tool(
        server,
        "assistant_read_config",
        {
            "session_id": "assistant-session",
            "scope": "global",
            "domain": "host",
        },
    )

    assert host_result["scope"] == "global"
    assert host_result["domain"] == "host"
    assert host_result["config"]["runtime_dir"] == str(tmp_path)

    worker_result = _call_tool(
        server,
        "assistant_read_config",
        {
            "session_id": "assistant-session",
            "scope": "worker_global",
            "worker_id": "teams-ingest",
        },
    )

    assert worker_result["scope"] == "worker_global"
    assert worker_result["worker_id"] == "teams-ingest"
    assert isinstance(worker_result["config"], dict)


def test_assistant_post_chat_reply_enforces_thread_mapping(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    store = ChatStore(tmp_path / "admin-web-chat-store.json")
    thread_id = store.get_or_create_thread("alice")
    user_message = store.append_message(
        user_id="alice",
        thread_id=thread_id,
        role="user",
        content="hello",
        status="queued",
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            allowed_tools=[ToolName.ASSISTANT_POST_CHAT_REPLY],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "capability_profile": "read-only",
                "admin_chat_threads": {
                    "chat-thread-alice": {
                        "thread_id": thread_id,
                        "user_id": "alice",
                    }
                },
            },
        )
    )
    server = create_server(context)

    result = _call_tool(
        server,
        "assistant_post_chat_reply",
        {
            "session_id": "assistant-session",
            "thread_id": thread_id,
            "user_message_id": user_message["id"],
            "content": "answer",
        },
    )

    assert result["message"]["role"] == "assistant"
    transcript = store.get_transcript(user_id="alice", thread_id=thread_id)
    assert [message["role"] for message in transcript["messages"]] == ["user", "assistant"]
    assert transcript["messages"][0]["status"] == "answered"

    with pytest.raises(ToolError):
        _call_tool(
            server,
            "assistant_post_chat_reply",
            {
                "session_id": "assistant-session",
                "thread_id": "thread-other",
                "content": "nope",
            },
        )


def test_run_script_enforces_tool_policy_and_script_allowlist(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            allowed_script_names=["deploy-review"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool run_script is not allowed"):
        asyncio.run(
            server.call_tool(
                "run_script",
                {
                    "session_id": "session-1",
                    "script_name": "deploy-review",
                    "parameters": ["--pr", "42"],
                },
            )
        )

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            allowed_script_names=["deploy-review"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    with pytest.raises(ToolError, match="not allowlisted"):
        asyncio.run(
            server.call_tool(
                "run_script",
                {"session_id": "session-1", "script_name": "collect-logs", "parameters": []},
            )
        )


def test_make_phone_call_enforces_policy_contact_alias_and_audit_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RETELL_API_KEY", "retell-secret")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "call_id": "call-1",
                    "call_status": "registered",
                    "from_number": "+15550000001",
                    "to_number": "+15550000002",
                },
            )
        return httpx.Response(
            200,
            json={
                "call_id": "call-1",
                "call_status": "ended",
                "to_number": "+15550000002",
                "transcript": "Agent: done",
                "metadata": {"customer_phone": "+15550000003", "safe": "ok"},
                "disconnection_reason": "agent_hangup",
            },
        )

    real_client = httpx.Client

    def fake_client(*_args: object, **_kwargs: object) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("agent_sandbox.retell_phone.httpx.Client", fake_client)
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
            retell_api_base_url="https://retell.test",
            retell_from_number="+15550000001",
            retell_default_agent_id="agent-default",
            retell_phonebook={
                "ops": {
                    "display_name": "Ops Duty",
                    "phone_number": "+15550000002",
                }
            },
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool make_phone_call is not allowed"):
        _call_tool(
            server,
            "make_phone_call",
            {
                "session_id": "session-1",
                "contact": "ops",
                "prompt": "Ask whether the shipment arrived.",
            },
        )

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.MAKE_PHONE_CALL],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    with pytest.raises(ToolError, match="Unknown Retell phonebook contact alias"):
        _call_tool(
            server,
            "make_phone_call",
            {
                "session_id": "session-1",
                "contact": "raw-number",
                "prompt": "Ask whether the shipment arrived.",
            },
        )

    registered = _call_tool(
        server,
        "make_phone_call",
        {
            "session_id": "session-1",
            "contact": "ops",
            "prompt": "Ask whether the shipment arrived.",
            "request_id": "phone-1",
            "metadata": {"safe": "visible", "phone": "+15550000004"},
        },
    )
    with pytest.raises(ToolError, match="Tool get_phone_call_result is not allowed"):
        _call_tool(
            server,
            "get_phone_call_result",
            {"session_id": "session-1", "request_id": "phone-1"},
        )

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.MAKE_PHONE_CALL, ToolName.GET_PHONE_CALL_RESULT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    result = _call_tool(
        server,
        "get_phone_call_result",
        {"session_id": "session-1", "request_id": "phone-1"},
    )

    assert registered == {
        "request_id": "phone-1",
        "call_id": "call-1",
        "contact": "ops",
        "status": "registered",
    }
    assert result["status"] == "ended"
    assert result["transcript"] == "Agent: done"
    assert result["metadata"]["metadata"]["customer_phone"] == "<redacted>"
    create_payload = json.loads(requests[0].content.decode("utf-8"))
    assert create_payload["to_number"] == "+15550000002"
    assert create_payload["retell_llm_dynamic_variables"] == {
        "agent_question": "Ask whether the shipment arrived."
    }
    assert "+15550000001" not in json.dumps(result)
    assert "+15550000002" not in json.dumps(result)
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "Ask whether the shipment arrived." not in audit_text
    assert "+15550000001" not in audit_text
    assert "+15550000002" not in audit_text
    assert "+15550000004" not in audit_text


def test_get_phone_call_result_denies_cross_session_reads(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
            retell_api_base_url="https://retell.test",
            retell_from_number="+15550000001",
            retell_default_agent_id="agent-default",
            retell_phonebook={
                "ops": {
                    "display_name": "Ops Duty",
                    "phone_number": "+15550000002",
                }
            },
        )
    )
    context.retell_phone_call_store.prepare_call(
        session_id="session-1",
        directory_path="root",
        contact_alias="ops",
        contact_display_name="Ops Duty",
        prompt_metadata={"sha256": "prompt-sha", "length": 8},
        request_id="phone-1",
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-2",
            allowed_tools=[ToolName.GET_PHONE_CALL_RESULT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="outside this session scope"):
        _call_tool(
            server,
            "get_phone_call_result",
            {"session_id": "session-2", "request_id": "phone-1"},
        )


def test_list_pull_requests_enforces_tool_policy_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "ado-token")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool list_pull_requests is not allowed"):
        asyncio.run(
            server.call_tool(
                "list_pull_requests",
                {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1"},
            )
        )

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    with pytest.raises(ToolError, match="greater than or equal to 1"):
        asyncio.run(
            server.call_tool(
                "list_pull_requests",
                {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1", "top": 0},
            )
        )


def test_list_pull_requests_does_not_require_feedback_stream_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "ado-token")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class FakePrQueryService:
        def list_pull_requests(self, **kwargs: object) -> ListPullRequestsResponse:
            assert kwargs["provider"] == "azure_devops"
            assert kwargs["repo"] == "repo-1"
            return ListPullRequestsResponse(
                pull_requests=[
                    PullRequestItem(
                        provider="azure_devops",
                        repo="repo-1",
                        pr_id="17",
                        title="Fix race",
                        status="active",
                    )
                ]
            )

    context.pr_query_service = FakePrQueryService()
    server = create_server(context)

    result = _call_tool(
        server,
        "list_pull_requests",
        {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1"},
    )

    assert result["pull_requests"][0]["pr_id"] == "17"


def test_get_pull_request_enforces_tool_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "ado-token")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [
                    {"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}
                ]
            },
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool get_pull_request is not allowed"):
        asyncio.run(
            server.call_tool(
                "get_pull_request",
                {
                    "session_id": "session-1",
                    "provider": "azure_devops",
                    "repo": "repo-1",
                    "pr_id": "17",
                },
            )
        )


def test_get_pull_request_allows_repo_scoped_pr_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "ado-token")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.GET_PULL_REQUEST],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/_apis/git/repositories/repo-1/pullRequests/99")
        return httpx.Response(
            200,
            json={
                "pullRequestId": 99,
                "title": "Follow-up",
                "status": "active",
                "_links": {
                    "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/99"}
                },
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )

    result = _call_tool(
        server,
        "get_pull_request",
        {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1", "pr_id": "99"},
    )

    assert result["pr_id"] == "99"


def test_schedule_resume_enforces_tool_policy_and_validates_args(tmp_path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool schedule_resume is not allowed"):
        asyncio.run(
            server.call_tool(
                "schedule_resume",
                {
                    "session_id": "session-1",
                    "resume_at": "2026-04-01T10:30:00Z",
                    "prompt": "continue",
                },
            )
        )

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-real",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    with pytest.raises(ToolError, match="String should have at least 1 character"):
        asyncio.run(
            server.call_tool(
                "schedule_resume",
                {
                    "session_id": "session-1",
                    "resume_at": "2026-04-01T10:30:00Z",
                    "prompt": "",
                },
            )
        )


def test_gateway_app_exposes_host_mediated_copilot_auth(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_test")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COPILOT_HOST_AUTH],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    client = TestClient(create_app(context))
    response = client.get(
        "/copilot-auth/token", headers={"x-agent-sandbox-session-id": "session-1"}
    )

    assert response.status_code == 200
    assert response.text == "github_pat_test"
    assert response.headers["cache-control"] == "no-store"


def test_gateway_app_rejects_missing_unauthorized_or_expired_copilot_auth_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_test")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="active-session",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="expired-session",
            allowed_tools=[],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )

    client = TestClient(create_app(context))

    missing_response = client.get("/copilot-auth/token")
    unauthorized_response = client.get(
        "/copilot-auth/token",
        headers={"x-agent-sandbox-session-id": "active-session"},
    )
    expired_response = client.get(
        "/copilot-auth/token",
        headers={"x-agent-sandbox-session-id": "expired-session"},
    )

    assert missing_response.status_code == 403
    assert missing_response.json()["error"] == "session_id is required"
    assert unauthorized_response.status_code == 403
    assert (
        unauthorized_response.json()["error"]
        == "Tool copilot_host_auth is not allowed for session active-session"
    )
    assert expired_response.status_code == 403
    assert expired_response.json()["error"] == "Expired session: expired-session"
    audit_events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        event["event_type"] == "copilot_auth_request"
        and event["session_id"] == "active-session"
        and event["decision"] == "denied"
        and "copilot_host_auth" in event["reason"]
        for event in audit_events
    )


def test_gateway_app_reports_missing_host_token_for_copilot_auth(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COPILOT_HOST_AUTH],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    client = TestClient(create_app(context))
    response = client.get(
        "/copilot-auth/token", headers={"x-agent-sandbox-session-id": "session-1"}
    )

    assert response.status_code == 503
    assert response.json()["error"] == "Host environment variable GITHUB_TOKEN is not set."


def test_gateway_app_runs_mounted_mcp_lifespan_on_startup(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )

    app = create_app(context)
    mounted_app = next(route.app for route in app.routes if getattr(route, "path", None) == "")
    mcp_session_manager = mounted_app.router.routes[0].endpoint.session_manager

    assert mcp_session_manager._task_group is None

    with TestClient(app):
        assert mcp_session_manager._task_group is not None

    assert mcp_session_manager._task_group is None
