from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.addons import AddonLoaderConfig
from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, tool_name_value
from agent_sandbox.runtime import build_runtime
from truman_team_addon.mcp import TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL
from truman_team_addon.repository import ADDON_ID, TeamStateRepository


def _runtime(tmp_path: Path, *, allowed_tools: list[str]):
    runtime_dir = tmp_path / "runtime"
    context = build_runtime(
        GatewayConfig(
            runtime_dir=runtime_dir,
            audit_log_path=runtime_dir / "audit.jsonl",
            session_store_path=runtime_dir / "sessions.json",
            approval_store_path=runtime_dir / "approvals.json",
            draft_store_path=runtime_dir / "drafts.json",
            email_store_path=runtime_dir / "emails.json",
            discord_command_store_path=runtime_dir / "discord.json",
            addon_loader=AddonLoaderConfig(
                modules=["truman_team_addon"],
                enabled_addon_ids=[ADDON_ID],
            ),
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-governance",
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    return context


def _call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server._tool_manager.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def _seed_governance_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_team(
        team_id="other-team",
        name="Other Team",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    return repo


def test_team_addon_mcp_registers_operator_directive_tool(tmp_path: Path) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL])
    repo = _seed_governance_state(context.config.runtime_dir)
    server = create_server(context)

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    session = context.policy.get_session("session-governance")
    visible = {
        tool_name_value(item.tool)
        for item in context.policy.visible_tools_for_session(session)
    }

    assert TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL in registered
    assert TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL in visible

    result = _call_tool(
        server,
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        {
            "session_id": "session-governance",
            "team_id": "team",
            "title": "Confirm blocker",
            "message": "Confirm the exact blocker and keep the interim demo available.",
            "source_message_id": "governance-dispatch-1",
        },
    )

    assert result["success"] is True
    assert result["recipient_agent_id"] == "manager"
    state = repo.read()
    item = state.inbox[result["inbox_item_id"]]
    assert item.recipient_agent_id == "manager"
    assert item.payload["source"] == "team_governance_mcp"
    assert item.payload["source_message_id"] == "governance-dispatch-1"
    assert item.payload["created_by"] == "agent-session:session-governance"
    assert item.payload["requesting_session_id"] == "session-governance"
    assert state.agent_runtimes["manager"].next_wake_at is None


def test_team_addon_mcp_rejects_directives_outside_governance_scope(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL])
    _seed_governance_state(context.config.runtime_dir)
    server = create_server(context)

    with pytest.raises(ToolError, match="governance manager session"):
        _call_tool(
            server,
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
            {
                "session_id": "session-governance",
                "team_id": "other-team",
                "message": "This team is outside the governance group.",
            },
        )
