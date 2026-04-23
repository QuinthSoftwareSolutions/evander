from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import asyncio
import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import (
    _filter_discord_commands_for_session,
    _resolve_session_id_from_context,
    create_app,
    create_server,
)
from agent_sandbox.models import DiscordCommandEnvelope, SessionManifest, ToolName
from agent_sandbox.policy import PolicyError
from agent_sandbox.runtime import build_runtime


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
        def require_discord_command_source(self, _session: SessionManifest, command: DiscordCommandEnvelope) -> None:
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
            request=SimpleNamespace(query_params={}, headers={"x-agent-sandbox-session-id": "header-session"})
        )
    )
    mapped_ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(query_params={}, headers={"mcp-session-id": "transport-1"})
        )
    )

    assert (
        _resolve_session_id_from_context(None, query_ctx, transport_session_map=transport_map) == "query-session"
    )
    assert transport_map == {"transport-1": "query-session"}
    assert _resolve_session_id_from_context(None, header_ctx) == "header-session"
    assert (
        _resolve_session_id_from_context(None, mapped_ctx, transport_session_map=transport_map) == "query-session"
    )


def test_resolve_session_id_from_context_requires_value() -> None:
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(query_params={}, headers={})))

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

    request_pr_review_tool = next(tool for tool in server._tool_manager.list_tools() if tool.name == "request_pr_review")
    provider_schema = request_pr_review_tool.parameters["properties"]["provider"]

    assert provider_schema["type"] == "string"
    assert provider_schema["enum"] == ["github", "azure_devops"]


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
    context.sessions.put(
        "session-1",
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
                {"session_id": "session-1", "script_name": "deploy-review", "parameters": ["--pr", "42"]},
            )
        )

    context.sessions.put(
        "session-1",
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
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]},
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

    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]},
        ),
    )
    with pytest.raises(ToolError, match="greater than or equal to 1"):
        asyncio.run(
            server.call_tool(
                "list_pull_requests",
                {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1", "top": 0},
            )
        )


def test_get_pull_request_enforces_tool_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]},
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool get_pull_request is not allowed"):
        asyncio.run(
            server.call_tool(
                "get_pull_request",
                {"session_id": "session-1", "provider": "azure_devops", "repo": "repo-1", "pr_id": "17"},
            )
        )


def test_get_pull_request_allows_repo_scoped_pr_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.GET_PULL_REQUEST],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]},
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
                "_links": {"web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/99"}},
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
    context.sessions.put(
        "session-1",
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

    context.sessions.put(
        "session-1",
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


def test_gateway_app_exposes_host_mediated_copilot_auth(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COPILOT_HOST_AUTH],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    client = TestClient(create_app(context))
    response = client.get("/copilot-auth/token", headers={"x-agent-sandbox-session-id": "session-1"})

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
    context.sessions.put(
        "active-session",
        SessionManifest(
            session_id="active-session",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.sessions.put(
        "expired-session",
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
    assert unauthorized_response.json()["error"] == "Tool copilot_host_auth is not allowed for session active-session"
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


def test_gateway_app_reports_missing_host_token_for_copilot_auth(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COPILOT_HOST_AUTH],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    client = TestClient(create_app(context))
    response = client.get("/copilot-auth/token", headers={"x-agent-sandbox-session-id": "session-1"})

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
