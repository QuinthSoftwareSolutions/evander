from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.admin.composition import build_admin_runtime
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.gateway import create_server
from evander.directories import DirectoryService, DirectoryStore
from evander.models import (
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from evander.runtime import build_runtime


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
    ensure_directories(context, "root/team-a")
    return context


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


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def read_audit_events(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write_root_policy_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


def test_create_server_registers_extracted_issue_tools(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    server = create_server(context)

    registered_tools = set(server._tool_manager._tools)

    assert {
        "list_issues",
        "get_issue",
        "create_local_issue",
        "set_issue_plan",
        "approve_issue",
        "claim_next_issue",
        "complete_issue",
        "reject_issue",
        "commit_issue",
    }.issubset(registered_tools)


def test_create_server_registers_extracted_agent_tools(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    server = create_server(context)

    registered_tools = set(server._tool_manager._tools)

    assert {
        "get_agent_status",
        "get_agent_logs",
    }.issubset(registered_tools)


def test_get_issue_reads_closed_issue_without_list_filters(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[ToolName.GET_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    admin = build_admin_runtime(context.config.runtime_dir)
    created = admin.issue_git_admin.create_local_issue(title="Rejected issue", body="x")
    admin.issue_git_admin.update_issue(
        created.issue_number, state="Closed", fields={"Reason": "Out of scope"}
    )

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


def test_list_issues_returns_rejected_closed_local_issue(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ISSUES],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    admin = build_admin_runtime(context.config.runtime_dir)
    created = admin.issue_git_admin.create_local_issue(
        title="Rejected issue",
        body="x",
        directory_path="root/team-a",
    )
    admin.issue_git_admin.update_issue(
        created.issue_number, state="Closed", fields={"Reason": "Out of scope"}
    )

    result = call_tool(
        server,
        "list_issues",
        {
            "session_id": "session-1",
            "status": ["Rejected"],
        },
    )

    assert [entry["issue_number"] for entry in result["issues"]] == [created.issue_number]
    assert result["issues"][0]["state"] == "Closed"
    assert result["issues"][0]["reason"] == "Out of scope"
    assert result["issues"][0]["rejection_reason"] == "Out of scope"


def test_get_issue_requires_permission_in_gateway_issue_tools(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            allowed_tools=[ToolName.SET_ISSUE_PLAN, ToolName.GET_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)
    admin = build_admin_runtime(context.config.runtime_dir)
    created = admin.issue_git_admin.create_local_issue(title="Planned issue", body="x")

    result = call_tool(
        server,
        "set_issue_plan",
        {
            "session_id": "session-1",
            "issue_id": created.issue_number,
            "steps": ["Inspect code", "Write patch"],
            "summary": "Inspect the repo, make the patch, then continue with follow-up workflow steps.",
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
    assert (
        result["summary"]
        == "Inspect the repo, make the patch, then continue with follow-up workflow steps."
    )
    assert reread["metadata"]["steps"] == ["Inspect code", "Write patch"]
    assert reread["metadata"]["plan_summary"] == (
        "Inspect the repo, make the patch, then continue with follow-up workflow steps."
    )


def test_set_issue_plan_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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


def test_create_ado_backlog_item_success_with_allowed_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    write_root_policy_overlay(context.config, {"redacted_keys": ["description"]})
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "project-lock",
                        "name": "Lock ADO project",
                        "constraints": [
                            {
                                "id": "project",
                                "path": "project",
                                "operator": "fixed",
                                "value": "example-project",
                            }
                        ],
                    },
                    {
                        "id": "type-allowlist",
                        "name": "Allow work item types",
                        "constraints": [
                            {
                                "id": "type",
                                "path": "type",
                                "operator": "enum",
                                "values": ["task", "product backlog item"],
                                "case_sensitive": False,
                            }
                        ],
                    },
                    {
                        "id": "required-fields",
                        "name": "Require backlog item fields",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            },
                            {
                                "id": "iteration",
                                "path": "iteration",
                                "operator": "required",
                            },
                        ],
                    },
                    {
                        "id": "tag-denylist",
                        "name": "Reject forbidden tags",
                        "constraints": [
                            {
                                "id": "tags-deny",
                                "path": "tags[]",
                                "operator": "denylist",
                                "values": ["blocked-tag"],
                            }
                        ],
                    },
                ]
            },
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

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
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
        "project": "example-project",
        "issue_type": "Task",
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
    event = read_audit_events(context.config.audit_log_path)[-1]
    assert event["decision"] == "allowed"
    assert event["result"]["created_item_id"] == "9001"
    assert event["args"]["description"] == "<redacted>"


def test_create_ado_backlog_item_denies_argument_policy_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    write_root_policy_overlay(context.config, {"redacted_keys": ["description"]})
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "project-lock",
                        "name": "Lock ADO project",
                        "constraints": [
                            {
                                "id": "project",
                                "path": "project",
                                "operator": "fixed",
                                "value": "example-project",
                            }
                        ],
                    },
                    {
                        "id": "type-allowlist",
                        "name": "Allow work item types",
                        "constraints": [
                            {
                                "id": "type",
                                "path": "type",
                                "operator": "enum",
                                "values": ["task", "product backlog item"],
                                "case_sensitive": False,
                            }
                        ],
                    },
                    {
                        "id": "required-fields",
                        "name": "Require backlog item fields",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            },
                            {
                                "id": "iteration",
                                "path": "iteration",
                                "operator": "required",
                            },
                        ],
                    },
                    {
                        "id": "tag-denylist",
                        "name": "Reject forbidden tags",
                        "constraints": [
                            {
                                "id": "tags-deny",
                                "path": "tags[]",
                                "operator": "denylist",
                                "values": ["blocked-tag"],
                            }
                        ],
                    },
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["instantiated"] = True
            raise AssertionError("IssueService should not be instantiated for denied arguments")

        def create_remote_issue(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {}

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    with pytest.raises(ToolError, match="tool_argument_policy_denied"):
        asyncio.run(
            server.call_tool(
                "create_ado_backlog_item",
                {
                    "session_id": "session-1",
                    "type": "task",
                    "project": "example-project",
                    "iteration": "Project\\Sprint 1",
                    "title": "Denied",
                    "description": "secret description",
                    "tags": ["blocked-tag", "backend"],
                },
            )
        )

    assert "instantiated" not in captured
    events = read_audit_events(context.config.audit_log_path)
    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "denied"
    assert event["reason"].startswith("tool argument policy violation for create_ado_backlog_item")
    assert event["result"]["status"] == "denied"
    assert event["result"]["error_class"] == "tool_argument_policy_violation"
    assert event["result"]["policy_rule_id"] == "tag-denylist"
    assert event["result"]["policy_rule_name"] == "Reject forbidden tags"
    assert event["result"]["constraint_id"] == "tags-deny"
    assert event["result"]["field_path"] == "tags[]"
    assert event["result"]["payload_summary"]["description"] == "<redacted>"
    assert event["args"]["description"] == "<redacted>"
    assert event["args"]["tags"] == ["blocked-tag", "backend"]


def test_create_ado_backlog_item_normalizes_assignee_identity_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            pass

        def create_remote_issue(self, **kwargs: object) -> dict[str, object]:
            return {
                "provider": "azure_devops",
                "remote_issue_id": "9002",
                "url": "https://dev.azure.com/example/example/_workitems/edit/9002",
                "project": "example-project",
                "fields": {
                    "System.Title": "Gateway-created item",
                    "System.AssignedTo": {
                        "displayName": "Ada Lovelace",
                        "uniqueName": "ada@example.com",
                    },
                },
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
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
        },
    )

    assert result["assigned_user"] == "Ada Lovelace"


def test_create_ado_backlog_item_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
    "receive,granted,denied",
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, False),
        (True, True, True),
    ],
)
def test_ado_image_access_reports_effective_gates_without_changing_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receive: bool,
    granted: bool,
    denied: bool,
) -> None:
    context = make_context(tmp_path)
    media_tools = [
        ToolName.LIST_INLINE_MEDIA,
        ToolName.DOWNLOAD_INLINE_MEDIA,
        ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
    ]
    session = SessionManifest(
        session_id="session-1",
        directory_path="root/team-a",
        allowed_tools=[ToolName.GET_ADO_BACKLOG_ITEM] + (media_tools if granted else []),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    context.session_repository.upsert_session(session)
    write_root_policy_overlay(
        context.config,
        {
            "communication_file_receive_enabled": receive,
            "mcp_tool_policies": {"download_inline_media": {"denied": True}} if denied else {},
        },
    )

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            pass

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            return {
                "provider": "azure_devops",
                "id": "101",
                "project": "Project",
                "inline_media": [
                    {
                        "source_kind": "ado_backlog",
                        "provider": "azure_devops",
                        "resource_id": "101",
                        "media_id": "field-image-0",
                        "filename": "image.png",
                        "url": "https://dev.azure.com/example/image.png",
                    }
                ],
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    result = call_tool(
        create_server(context),
        "get_ado_backlog_item",
        {
            "session_id": "session-1",
            "id": 101,
        },
    )
    access = result["metadata"]["inline_media_access"]
    assert access["receive_enabled"] is receive
    expected_unavailable = {tool.value for tool in media_tools} if not granted else set()
    if denied:
        expected_unavailable.add("download_inline_media")
    assert set(access["unavailable_tools"]) == expected_unavailable
    assert "destination_path" in access["instructions"]
    assert "unique" in access["instructions"]
    assert context.session_repository.get_session("session-1") == session


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
    context.session_repository.upsert_session(
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


def test_query_ado_backlog_items_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(server.call_tool("query_ado_backlog_items", {"session_id": "session-1"}))


def test_update_ado_backlog_tags_merges_existing_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.UPDATE_ADO_BACKLOG_TAGS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[dict[str, object]] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append({"provider_config": provider_config})

        def read_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"read": kwargs})
            return {
                "remote_issue_id": "15446",
                "fields": {"System.Tags": "auto-dev; evander"},
            }

        def update_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"update": kwargs})
            return {
                "provider": "azure_devops",
                "remote_issue_id": "15446",
                "updated": True,
                "fields": kwargs.get("fields"),
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "update_ado_backlog_tags",
        {
            "session_id": "session-1",
            "id": 15446,
            "project": "Example Industry App",
            "add_tags": ["auto-dev-approved", "evander"],
        },
    )

    assert calls[1]["read"] == {
        "provider": "azure_devops",
        "remote_issue_id": "15446",
        "directory_path": "root/team-a",
        "project": "Example Industry App",
        "include_comments": False,
    }
    assert calls[2]["update"] == {
        "provider": "azure_devops",
        "remote_issue_id": "15446",
        "directory_path": "root/team-a",
        "project": "Example Industry App",
        "state": None,
        "fields": {"System.Tags": "auto-dev; evander; auto-dev-approved"},
    }
    assert result["tags"] == ["auto-dev", "evander", "auto-dev-approved"]
    assert result["added_tags"] == ["auto-dev-approved"]
    assert result["removed_tags"] == []


def test_update_ado_backlog_tags_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                "update_ado_backlog_tags",
                {"session_id": "session-1", "id": 15446, "add_tags": ["auto-dev-approved"]},
            )
        )


def test_update_ado_backlog_title_updates_system_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.UPDATE_ADO_BACKLOG_TITLE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[dict[str, object]] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append({"provider_config": provider_config})

        def update_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"update": kwargs})
            return {
                "provider": "azure_devops",
                "remote_issue_id": "15446",
                "updated": True,
                "fields": {"System.Title": "Renamed backlog item"},
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "update_ado_backlog_title",
        {
            "session_id": "session-1",
            "id": 15446,
            "project": "Example Industry App",
            "title": "  Renamed backlog item  ",
        },
    )

    assert calls[0]["provider_config"] == context.provider_config
    assert calls[1]["update"] == {
        "provider": "azure_devops",
        "remote_issue_id": "15446",
        "directory_path": "root/team-a",
        "project": "Example Industry App",
        "state": None,
        "fields": {"System.Title": "Renamed backlog item"},
    }
    assert result["work_item_id"] == "15446"
    assert result["title"] == "Renamed backlog item"
    assert result["fields"]["System.Title"] == "Renamed backlog item"


def test_update_ado_backlog_title_rejects_blank_title_before_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.UPDATE_ADO_BACKLOG_TITLE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[object] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append(provider_config)

        def update_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {}

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    with pytest.raises(ToolError, match="title cannot be empty"):
        asyncio.run(
            server.call_tool(
                "update_ado_backlog_title",
                {"session_id": "session-1", "id": 15446, "title": "   "},
            )
        )

    assert calls == []


def test_update_ado_backlog_title_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                "update_ado_backlog_title",
                {"session_id": "session-1", "id": 15446, "title": "Renamed backlog item"},
            )
        )


def test_query_ado_backlog_items_maps_request_into_issue_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.QUERY_ADO_BACKLOG_ITEMS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["provider_config"] = provider_config

        def query_remote_backlog_items(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "project": "example-project",
                "items": [
                    {
                        "id": "77",
                        "title": "Backlog item",
                        "state": "New",
                        "type": "Task",
                        "iteration": "Project\\Sprint 1",
                        "area": "Project",
                        "assigned_to": "Ada Lovelace",
                        "url": "https://dev.azure.com/example/example/_workitems/edit/77",
                        "fields": {"System.Title": "Backlog item"},
                    }
                ],
                "count": 1,
                "paging": {
                    "skip": 2,
                    "top": 10,
                    "returned_count": 1,
                    "total_matches": 3,
                    "has_more": False,
                    "next_skip": None,
                },
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "query_ado_backlog_items",
        {
            "session_id": "session-1",
            "project": "example-project",
            "iteration": "Project\\Sprint 1",
            "area_path": "Project",
            "states": ["new", "active"],
            "types": ["task", "bug"],
            "tags_any": ["api", "backend"],
            "text_search": "important",
            "top": 10,
            "skip": 2,
        },
    )

    assert captured["provider_config"] == context.provider_config
    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root/team-a",
        "project": "example-project",
        "iteration": "Project\\Sprint 1",
        "area_path": "Project",
        "states": ["new", "active"],
        "types": ["Task", "Bug"],
        "tags_any": ["api", "backend"],
        "text_search": "important",
        "top": 10,
        "skip": 2,
    }
    assert result["count"] == 1
    assert result["items"][0]["id"] == "77"
    assert result["paging"]["skip"] == 2


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        ({"session_id": "session-1", "top": 0}, "top"),
        ({"session_id": "session-1", "skip": -1}, "skip"),
        ({"session_id": "session-1", "types": ["not-a-real-type"]}, "type"),
    ],
)
def test_query_ado_backlog_items_validation_failures(
    tmp_path: Path,
    arguments: dict[str, object],
    error_match: str,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.QUERY_ADO_BACKLOG_ITEMS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match=error_match):
        asyncio.run(server.call_tool("query_ado_backlog_items", arguments))


def test_list_ado_current_iteration_backlog_items_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                "list_ado_current_iteration_backlog_items", {"session_id": "session-1"}
            )
        )


def test_list_ado_current_iteration_backlog_items_maps_request_and_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["provider_config"] = provider_config

        def list_remote_current_iteration_backlog_items(
            self, **kwargs: object
        ) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "project": "example-project",
                "team": "Platform Team",
                "iteration_path": "Project\\Sprint 12",
                "items": [
                    {
                        "id": "101",
                        "title": "Current sprint task",
                        "state": "Active",
                        "type": "Task",
                        "iteration": "Project\\Sprint 12",
                        "area": "Project",
                        "assigned_to": "Ada Lovelace",
                        "url": "https://dev.azure.com/example/example/_workitems/edit/101",
                        "fields": {"System.Title": "Current sprint task"},
                    }
                ],
                "count": 1,
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "list_ado_current_iteration_backlog_items",
        {
            "session_id": "session-1",
            "project": "example-project",
            "team": "Platform Team",
            "area_path": "Project",
            "states": ["active"],
            "types": ["task"],
            "tags_any": ["backend"],
            "text_search": "retry",
            "top": 25,
            "skip": 5,
        },
    )

    assert captured["provider_config"] == context.provider_config
    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root/team-a",
        "project": "example-project",
        "team": "Platform Team",
        "area_path": "Project",
        "states": ["active"],
        "types": ["Task"],
        "tags_any": ["backend"],
        "text_search": "retry",
        "top": 25,
        "skip": 5,
    }
    assert result == {
        "project": "example-project",
        "team": "Platform Team",
        "iteration_path": "Project\\Sprint 12",
        "items": [
            {
                "id": "101",
                "title": "Current sprint task",
                "state": "Active",
                "type": "Task",
                "iteration": "Project\\Sprint 12",
                "area": "Project",
                "assigned_to": "Ada Lovelace",
                "url": "https://dev.azure.com/example/example/_workitems/edit/101",
                "fields": {"System.Title": "Current sprint task"},
            }
        ],
        "count": 1,
    }


def test_list_ado_current_iteration_backlog_items_errors_when_no_iteration_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            pass

        def list_remote_current_iteration_backlog_items(
            self, **kwargs: object
        ) -> dict[str, object]:
            raise RuntimeError(
                "Azure DevOps team 'Platform Team' has no current iteration configured."
            )

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    with pytest.raises(ToolError, match="no current iteration configured"):
        asyncio.run(
            server.call_tool(
                "list_ado_current_iteration_backlog_items",
                {
                    "session_id": "session-1",
                    "project": "example-project",
                    "team": "Platform Team",
                },
            )
        )


def test_get_ado_daily_status_report_data_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
            server.call_tool("get_ado_daily_status_report_data", {"session_id": "session-1"})
        )


def test_get_ado_daily_status_report_data_fetches_pages_and_calculates_template_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {"requests": []}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["provider_config"] = provider_config

        def list_remote_current_iteration_backlog_items(
            self, **kwargs: object
        ) -> dict[str, object]:
            requests = captured["requests"]
            assert isinstance(requests, list)
            requests.append(kwargs)
            if kwargs["skip"] == 0:
                return {
                    "provider": "azure_devops",
                    "project": "example-project",
                    "team": "Platform Team",
                    "iteration_path": "Project\\Sprint 12",
                    "iteration_start_date": "2026-05-04T00:00:00Z",
                    "iteration_finish_date": "2026-05-18T00:00:00Z",
                    "items": [
                        {
                            "id": "101",
                            "title": "Active backlog item",
                            "state": "Active",
                            "type": "Product Backlog Item",
                            "assigned_to": "Ada",
                            "fields": {"Microsoft.VSTS.Scheduling.Effort": 3},
                            "relations": [
                                {
                                    "rel": "ArtifactLink",
                                    "url": "vstfs:///Git/PullRequestId/project%2Frepo%2F42",
                                    "attributes": {"name": "Pull Request"},
                                }
                            ],
                        }
                    ],
                    "count": 1,
                    "paging": {
                        "skip": 0,
                        "top": 2,
                        "returned_count": 1,
                        "total_matches": 2,
                        "has_more": True,
                        "next_skip": 1,
                    },
                }
            return {
                "provider": "azure_devops",
                "project": "example-project",
                "team": "Platform Team",
                "iteration_path": "Project\\Sprint 12",
                "iteration_start_date": "2026-05-04T00:00:00Z",
                "iteration_finish_date": "2026-05-18T00:00:00Z",
                "items": [
                    {
                        "id": "102",
                        "title": "Done bug",
                        "state": "Done",
                        "type": "Bug",
                        "assigned_to": None,
                        "fields": {"Microsoft.VSTS.Scheduling.Effort": 2},
                        "relations": [],
                    }
                ],
                "count": 1,
                "paging": {
                    "skip": 1,
                    "top": 1,
                    "returned_count": 1,
                    "total_matches": 2,
                    "has_more": False,
                    "next_skip": None,
                },
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    result = call_tool(
        server,
        "get_ado_daily_status_report_data",
        {
            "session_id": "session-1",
            "project": "example-project",
            "team": "Platform Team",
            "report_date": "2026-05-12",
            "elapsed_sprint_percent": 50,
            "elapsed_time_mode": "manual_override",
            "max_items": 20,
        },
    )

    assert captured["provider_config"] == context.provider_config
    assert captured["requests"] == [
        {
            "provider": "azure_devops",
            "directory_path": "root/team-a",
            "project": "example-project",
            "team": "Platform Team",
            "area_path": None,
            "states": [],
            "types": ["Product Backlog Item", "Bug"],
            "exclude_types": ["Task"],
            "tags_any": [],
            "text_search": None,
            "top": 200,
            "skip": 0,
            "include_relations": True,
        },
        {
            "provider": "azure_devops",
            "directory_path": "root/team-a",
            "project": "example-project",
            "team": "Platform Team",
            "area_path": None,
            "states": [],
            "types": ["Product Backlog Item", "Bug"],
            "exclude_types": ["Task"],
            "tags_any": [],
            "text_search": None,
            "top": 200,
            "skip": 1,
            "include_relations": True,
        },
    ]
    assert result["template_data"]["items"] == {
        "unassigned": 1,
        "notStarted": 0,
        "inProgress": 1,
        "withPrs": 1,
        "done": 1,
        "total": 2,
    }
    assert result["template_data"]["effort"] == {"done": 2, "total": 5}
    assert result["template_data"]["elapsedSprintPercent"] == 50
    assert result["source"]["truncated"] is False
    assert result["source"]["max_items"] == 1000
    assert result["source"]["iteration_path"] == "Project\\Sprint 12"
    assert result["source"]["excluded_types"] == ["Task"]


def test_get_ado_backlog_item_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
            server.call_tool("get_ado_backlog_item", {"session_id": "session-1", "id": 101})
        )


def test_get_ado_backlog_item_maps_request_into_issue_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            captured["provider_config"] = provider_config

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "id": "101",
                "project": "example-project",
                "rev": 7,
                "url": "https://dev.azure.com/example/example/_apis/wit/workItems/101",
                "web_url": "https://dev.azure.com/example/example/_workitems/edit/101",
                "title": "Backlog item",
                "state": "Active",
                "type": "Task",
                "created_at": "2026-04-10T10:00:00Z",
                "changed_at": "2026-04-11T10:00:00Z",
                "closed_at": None,
                "fields": {"System.Title": "Backlog item"},
                "relations": [
                    {"rel": "System.LinkTypes.Hierarchy-Forward", "url": "https://example/rel"}
                ],
                "comments": [{"id": "1", "text": "Looks good"}],
                "updates": [{"id": "3", "rev": 7}],
            }

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)
    result = call_tool(
        server,
        "get_ado_backlog_item",
        {
            "session_id": "session-1",
            "id": "101",
            "project": "example-project",
            "include_comments": True,
            "include_relations": True,
            "include_updates": True,
        },
    )

    assert captured["provider_config"] == context.provider_config
    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root/team-a",
        "backlog_item_id": "101",
        "project": "example-project",
        "include_comments": True,
        "include_relations": True,
        "include_updates": True,
    }
    assert result["id"] == "101"
    assert result["project"] == "example-project"
    assert result["title"] == "Backlog item"


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        ({"session_id": "session-1"}, "id"),
        ({"session_id": "session-1", "id": "abc"}, "id"),
        ({"session_id": "session-1", "id": "   "}, "id"),
    ],
)
def test_get_ado_backlog_item_validation_failures(
    tmp_path: Path,
    arguments: dict[str, object],
    error_match: str,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match=error_match):
        asyncio.run(server.call_tool("get_ado_backlog_item", arguments))


def test_delete_ado_backlog_item_creates_pending_approval_without_instantiating_issue_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            raise AssertionError("IssueService must not be instantiated before approval")

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)

    with pytest.raises(ToolError, match="requires approval"):
        asyncio.run(
            server.call_tool(
                "delete_ado_backlog_item",
                {
                    "session_id": "session-1",
                    "id": 101,
                    "project": "example-project",
                },
            )
        )

    approvals = context.approvals.read_all()
    assert len(approvals) == 1
    approval = next(iter(approvals.values()))
    assert approval.status == ApprovalStatus.PENDING
    assert approval.action_type == "mcp_tool_invocation"
    assert approval.tool_name == ToolName.DELETE_ADO_BACKLOG_ITEM.value

    tool_events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event.get("tool_name") == ToolName.DELETE_ADO_BACKLOG_ITEM.value
    ]
    assert len(tool_events) == 1
    assert tool_events[0]["decision"] == "pending_approval"
    assert tool_events[0]["approval_request_id"] == approval.request_id
    assert tool_events[0]["result"]["status"] == "pending_approval"


@pytest.mark.parametrize(
    ("destroy", "delete_kind"),
    [
        (False, "soft_delete"),
        (True, "permanent_destroy"),
    ],
)
def test_delete_ado_backlog_item_approved_exact_retry_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destroy: bool,
    delete_kind: str,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[dict[str, object]] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append({"provider_config": provider_config})

        def delete_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"delete": kwargs})
            return {"provider": "azure_devops", "deleted": True}

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)
    tool_arguments = {
        "session_id": "session-1",
        "id": 101,
        "project": "example-project",
        "destroy": destroy,
    }

    with pytest.raises(ToolError, match="requires approval"):
        asyncio.run(server.call_tool("delete_ado_backlog_item", tool_arguments))

    approval = next(iter(context.approvals.read_all().values()))
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    approval.decision_reason = "verified"
    context.approvals.put(approval.request_id, approval)

    result = call_tool(
        server,
        "delete_ado_backlog_item",
        {**tool_arguments, "approval_request_id": approval.request_id},
    )

    assert calls == [
        {"provider_config": context.provider_config},
        {
            "delete": {
                "provider": "azure_devops",
                "remote_issue_id": "101",
                "directory_path": "root/team-a",
                "project": "example-project",
                "destroy": destroy,
            }
        },
    ]
    assert result == {
        "provider": "azure_devops",
        "work_item_id": "101",
        "project": "example-project",
        "deleted": True,
        "destroy": destroy,
        "delete_kind": delete_kind,
    }


def test_delete_ado_backlog_item_rejects_payload_replay_even_with_loose_session_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            approval_tool_settings={
                ToolName.DELETE_ADO_BACKLOG_ITEM.value: ApprovalGrantSettings(
                    max_uses=None,
                    payload_mode=ApprovalPayloadMode.ANY,
                )
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[dict[str, object]] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append({"provider_config": provider_config})

        def delete_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"delete": kwargs})
            return {"provider": "azure_devops", "deleted": True}

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)
    approved_arguments = {
        "session_id": "session-1",
        "id": 101,
        "project": "example-project",
        "destroy": False,
    }

    with pytest.raises(ToolError, match="requires approval"):
        asyncio.run(server.call_tool("delete_ado_backlog_item", approved_arguments))

    approval = next(iter(context.approvals.read_all().values()))
    assert approval.approval_settings is not None
    assert approval.approval_settings.max_uses == 1
    assert approval.approval_settings.payload_mode is ApprovalPayloadMode.EXACT
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    approval.decision_reason = "verified"
    context.approvals.put(approval.request_id, approval)

    with pytest.raises(ToolError, match="payload mismatch"):
        asyncio.run(
            server.call_tool(
                "delete_ado_backlog_item",
                {
                    **approved_arguments,
                    "id": 102,
                    "destroy": True,
                    "approval_request_id": approval.request_id,
                },
            )
        )
    assert calls == []

    result = call_tool(
        server,
        "delete_ado_backlog_item",
        {**approved_arguments, "approval_request_id": approval.request_id},
    )

    assert result["work_item_id"] == "101"
    assert len(calls) == 2
    stored_approval = context.approvals.get(approval.request_id)
    assert stored_approval is not None
    assert stored_approval.status == ApprovalStatus.EXHAUSTED


def test_delete_ado_backlog_item_requires_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
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
                "delete_ado_backlog_item",
                {"session_id": "session-1", "id": 101},
            )
        )


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        ({"session_id": "session-1"}, "id"),
        ({"session_id": "session-1", "id": "abc"}, "id"),
        ({"session_id": "session-1", "id": "   "}, "id"),
    ],
)
def test_delete_ado_backlog_item_validation_failures(
    tmp_path: Path,
    arguments: dict[str, object],
    error_match: str,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match=error_match):
        asyncio.run(server.call_tool("delete_ado_backlog_item", arguments))


def test_delete_ado_backlog_item_propagates_missing_work_item_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[dict[str, object]] = []

    class StubIssueService:
        def __init__(self, provider_config: object) -> None:
            calls.append({"provider_config": provider_config})

        def delete_remote_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append({"delete": kwargs})
            raise RuntimeError(
                "Azure DevOps delete work item failed (status 404): Work item does not exist"
            )

    monkeypatch.setattr("evander.gateway.IssueService", StubIssueService)
    server = create_server(context)
    tool_arguments = {
        "session_id": "session-1",
        "id": 404,
        "project": "example-project",
    }

    with pytest.raises(ToolError, match="requires approval"):
        asyncio.run(server.call_tool("delete_ado_backlog_item", tool_arguments))

    approval = next(iter(context.approvals.read_all().values()))
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    approval.decision_reason = "verified"
    context.approvals.put(approval.request_id, approval)

    with pytest.raises(
        ToolError,
        match=r"Azure DevOps delete work item failed \(status 404\): Work item does not exist",
    ):
        asyncio.run(
            server.call_tool(
                "delete_ado_backlog_item",
                {**tool_arguments, "approval_request_id": approval.request_id},
            )
        )

    tool_events = [
        event
        for event in read_audit_events(context.config.audit_log_path)
        if event.get("tool_name") == ToolName.DELETE_ADO_BACKLOG_ITEM.value
    ]
    assert [event["decision"] for event in tool_events] == ["pending_approval", "error"]
    assert tool_events[-1]["result"]["error_class"] == "unexpected_error"
