from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.runtime import build_runtime


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


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def test_get_issue_reads_closed_issue_without_list_filters(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[ToolName.GET_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    admin = AdminService(context.config.runtime_dir)
    created = admin.create_local_issue(title="Rejected issue", body="x")
    admin.update_issue(created.issue_number, state="Closed", fields={"Reason": "Out of scope"})

    result = call_tool(
        server,
        "get_issue",
        {
            "session_id": "session-1",
            "issue_id": created.issue_number,
        },
    )

    assert result["issue_number"] == created.issue_number
    assert result["state"] == "Closed"
    assert result["rejection_reason"] == "Out of scope"


def test_get_issue_requires_permission_in_gateway_issue_tools(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(server.call_tool("get_issue", {"session_id": "session-1", "issue_id": "42"}))


def test_set_issue_plan_updates_issue_metadata(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[ToolName.SET_ISSUE_PLAN, ToolName.GET_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    admin = AdminService(context.config.runtime_dir)
    created = admin.create_local_issue(title="Planned issue", body="x")

    result = call_tool(
        server,
        "set_issue_plan",
        {
            "session_id": "session-1",
            "issue_id": created.issue_number,
            "steps": ["Inspect code", "Write patch"],
        },
    )
    reread = call_tool(
        server,
        "get_issue",
        {
            "session_id": "session-1",
            "issue_id": created.issue_number,
        },
    )

    assert result["updated"] is True
    assert result["step_count"] == 2
    assert reread["metadata"]["steps"] == ["Inspect code", "Write patch"]


def test_set_issue_plan_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "set_issue_plan",
                {"session_id": "session-1", "issue_id": "42", "steps": ["Inspect code"]},
            )
        )


def test_create_ado_backlog_item_success_with_allowed_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["provider_config"] = provider_config

        def create_remote_issue(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "remote_issue_id": "9001",
                "url": "https://dev.azure.com/example/example/_workitems/edit/9001",
                "project": "example-project",
                "fields": {
                    "System.Title": "Gateway-created item",
                    "System.Description": "<p>desc</p>",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "AC text",
                    "System.AssignedTo": "ada@example.com",
                },
            }

    monkeypatch.setattr("agent_sandbox.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "create_ado_backlog_item",
        {
            "session_id": "session-1",
            "type": "task",
            "project": "example-project",
            "iteration": "Project\\Sprint 1",
            "title": "Gateway-created item",
            "description": "<p>desc</p>",
            "acceptance_criteria": "AC text",
            "tags": ["api", "backend"],
            "assigned_user": "ada@example.com",
        },
    )

    assert captured["provider_config"] == context.provider_config
    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root/team-a",
        "issue_type": "task",
        "title": "Gateway-created item",
        "description": "<p>desc</p>",
        "iteration_path": "Project\\Sprint 1",
        "acceptance_criteria": "AC text",
        "tags": ["api", "backend"],
        "assigned_to": "ada@example.com",
        "parent_backlog_item_id": None,
    }
    assert result["created_item_id"] == "9001"
    assert result["provider"] == "azure_devops"
    assert result["project"] == "example-project"


def test_create_ado_backlog_item_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(
            server.call_tool(
                "create_ado_backlog_item",
                {
                    "session_id": "session-1",
                    "type": "task",
                    "project": "example-project",
                    "iteration": "Project\\Sprint 1",
                    "title": "Denied",
                },
            )
        )


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        (
            {
                "session_id": "session-1",
                "type": "not-a-real-type",
                "project": "example-project",
                "iteration": "Project\\Sprint 1",
                "title": "Gateway-created item",
            },
            "type",
        ),
        (
            {
                "session_id": "session-1",
                "type": "task",
                "project": "example-project",
                "iteration": "Project\\Sprint 1",
            },
            "title",
        ),
        (
            {
                "session_id": "session-1",
                "type": "task",
                "project": "   ",
                "iteration": "Project\\Sprint 1",
                "title": "Gateway-created item",
            },
            "project",
        ),
        (
            {
                "session_id": "session-1",
                "type": "task",
                "project": "example-project",
                "iteration": "  ",
                "title": "Gateway-created item",
            },
            "iteration",
        ),
    ],
)
def test_create_ado_backlog_item_validation_failures(
    tmp_path: Path,
    arguments: dict[str, object],
    error_match: str,
) -> None:
    context = make_context(tmp_path)
    context.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match=error_match):
        asyncio.run(server.call_tool("create_ado_backlog_item", arguments))
