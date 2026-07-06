from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonLoaderConfig
from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.models import SessionManifest, tool_name_value
from evander.policy import PolicyError
from evander.runtime import build_runtime
from evander_team_addon import mcp as team_mcp
from evander_team_addon.models import (
    AgentInboxItem,
    AgentRuntime,
    GitWorkContext,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    RolePermission,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import ADDON_ID, TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService


# Keep this file importable on branches before the production constants land. The
# registration tests below still fail until the add-on exports and registers these tools.
TEAM_LIST_TASKS_TOOL = getattr(team_mcp, "TEAM_LIST_TASKS_TOOL", "team_list_tasks")
TEAM_GET_TASK_TOOL = getattr(team_mcp, "TEAM_GET_TASK_TOOL", "team_get_task")
TEAM_LIST_INBOX_TOOL = getattr(team_mcp, "TEAM_LIST_INBOX_TOOL", "team_list_inbox")
TEAM_GET_INBOX_ITEM_TOOL = getattr(
    team_mcp,
    "TEAM_GET_INBOX_ITEM_TOOL",
    "team_get_inbox_item",
)
TEAM_LIST_CASES_TOOL = getattr(team_mcp, "TEAM_LIST_CASES_TOOL", "team_list_cases")
TEAM_GET_CASE_TOOL = getattr(team_mcp, "TEAM_GET_CASE_TOOL", "team_get_case")
TEAM_LIST_DELIVERABLES_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_DELIVERABLES_TOOL",
    "team_list_deliverables",
)
TEAM_GET_DELIVERABLE_TOOL = getattr(
    team_mcp,
    "TEAM_GET_DELIVERABLE_TOOL",
    "team_get_deliverable",
)
TEAM_LIST_REVIEW_REQUESTS_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_REVIEW_REQUESTS_TOOL",
    "team_list_review_requests",
)
TEAM_GET_REVIEW_REQUEST_TOOL = getattr(
    team_mcp,
    "TEAM_GET_REVIEW_REQUEST_TOOL",
    "team_get_review_request",
)
TEAM_LIST_RELEASE_PACKAGES_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_RELEASE_PACKAGES_TOOL",
    "team_list_release_packages",
)
TEAM_GET_RELEASE_PACKAGE_TOOL = getattr(
    team_mcp,
    "TEAM_GET_RELEASE_PACKAGE_TOOL",
    "team_get_release_package",
)
TEAM_LIST_AGENTS_TOOL = getattr(team_mcp, "TEAM_LIST_AGENTS_TOOL", "team_list_agents")
TEAM_GET_AGENT_TOOL = getattr(team_mcp, "TEAM_GET_AGENT_TOOL", "team_get_agent")
TEAM_GET_MY_IDENTITY_TOOL = getattr(
    team_mcp,
    "TEAM_GET_MY_IDENTITY_TOOL",
    "team_get_my_identity",
)
TEAM_LIST_PROJECTS_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_PROJECTS_TOOL",
    "team_list_projects",
)
TEAM_GET_PROJECT_TOOL = getattr(team_mcp, "TEAM_GET_PROJECT_TOOL", "team_get_project")
TEAM_LIST_RUNTIME_ATTENTION_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_RUNTIME_ATTENTION_TOOL",
    "team_list_runtime_attention",
)
TEAM_SEARCH_MEMORY_TOOL = getattr(team_mcp, "TEAM_SEARCH_MEMORY_TOOL", "team_search_memory")

EXPECTED_READ_TOOL_NAMES = (
    TEAM_LIST_TASKS_TOOL,
    TEAM_GET_TASK_TOOL,
    TEAM_LIST_INBOX_TOOL,
    TEAM_GET_INBOX_ITEM_TOOL,
    TEAM_LIST_CASES_TOOL,
    TEAM_GET_CASE_TOOL,
    TEAM_LIST_DELIVERABLES_TOOL,
    TEAM_GET_DELIVERABLE_TOOL,
    TEAM_LIST_REVIEW_REQUESTS_TOOL,
    TEAM_GET_REVIEW_REQUEST_TOOL,
    TEAM_LIST_RELEASE_PACKAGES_TOOL,
    TEAM_GET_RELEASE_PACKAGE_TOOL,
    TEAM_LIST_AGENTS_TOOL,
    TEAM_GET_AGENT_TOOL,
    TEAM_GET_MY_IDENTITY_TOOL,
    TEAM_LIST_PROJECTS_TOOL,
    TEAM_GET_PROJECT_TOOL,
    TEAM_LIST_RUNTIME_ATTENTION_TOOL,
    TEAM_SEARCH_MEMORY_TOOL,
)


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


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
                modules=["evander_team_addon"],
                enabled_addon_ids=[ADDON_ID],
            ),
        )
    )
    _upsert_session(context, session_id="session-alpha-dev", allowed_tools=allowed_tools)
    return context


def _upsert_session(context, *, session_id: str, allowed_tools: list[str]) -> None:
    context.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )


def _call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server._tool_manager.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def _seed_read_tool_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    admin = _team_admin_service(repo)
    _seed_team(
        admin,
        repo,
        team_id="alpha",
        manager_agent_id="alpha-manager",
        manager_session_id="session-alpha-manager",
        lead_agent_id="alpha-lead",
        lead_session_id="session-alpha-lead",
        worker_agent_id="alpha-dev",
        worker_session_id="session-alpha-dev",
    )
    _seed_team(
        admin,
        repo,
        team_id="beta",
        manager_agent_id="beta-manager",
        manager_session_id="session-beta-manager",
        lead_agent_id="beta-lead",
        lead_session_id="session-beta-lead",
        worker_agent_id="beta-dev",
        worker_session_id="session-beta-dev",
    )
    _seed_read_records(repo)
    return repo


def _seed_team(
    admin: TeamAdminService,
    repo: TeamStateRepository,
    *,
    team_id: str,
    manager_agent_id: str,
    manager_session_id: str,
    lead_agent_id: str,
    lead_session_id: str,
    worker_agent_id: str,
    worker_session_id: str,
) -> None:
    project_id = f"{team_id}-project"
    role_id = f"{team_id}__developer"
    admin.create_team(
        team_id=team_id,
        name=f"{team_id.title()} Team",
        manager_agent_id=manager_agent_id,
        manager_session_id=manager_session_id,
        manager_display_name=f"{team_id.title()} Manager",
        expected_revision=repo.read().revision,
    )
    admin.create_project(
        project_id=project_id,
        team_id=team_id,
        name=f"{team_id.title()} Project",
        lead_agent_id=lead_agent_id,
        lead_session_id=lead_session_id,
        lead_display_name=f"{team_id.title()} Lead",
        goals=[ProjectGoal(id=f"{team_id}-goal", description=f"Ship {team_id}")],
        expected_revision=repo.read().revision,
    )
    admin.create_role(
        role_id=role_id,
        team_id=team_id,
        project_id=project_id,
        name="Developer",
        description=(
            f"{team_id.title()} developer role owns implementation details, "
            "coordination boundaries, and exact acceptance criteria. "
            + ("role-detail " * 80)
        ),
        permissions=[RolePermission.REQUEST_PAYMENT_PREPARATION],
        expected_revision=repo.read().revision,
    )
    admin.register_agent(
        agent_id=worker_agent_id,
        team_id=team_id,
        project_id=project_id,
        role_id=role_id,
        session_id=worker_session_id,
        reports_to_agent_id=lead_agent_id,
        display_name=f"{team_id.title()} Developer",
        expected_revision=repo.read().revision,
    )

    for suffix, state in (
        ("a", TeamTaskState.IN_PROGRESS),
        ("b", TeamTaskState.PENDING),
        ("c", TeamTaskState.BLOCKED),
    ):
        admin.create_or_reprioritize_task(
            task_id=f"{team_id}-task-{suffix}",
            owner_agent_id=worker_agent_id,
            project_id=project_id,
            priority=1,
            title=f"{team_id.title()} task {suffix}",
            description=f"{team_id} task {suffix} details " + ("x" * 4000),
            created_by_agent_id=lead_agent_id,
            state=state,
            expected_revision=repo.read().revision,
        )

    admin.create_case(
        case_id=f"{team_id}-case",
        team_id=team_id,
        project_id=project_id,
        title=f"{team_id.title()} customer case",
        description=f"{team_id} case details " + ("y" * 4000),
        owner_agent_id=worker_agent_id,
        metadata={"raw": {"nested": ["large"] * 300}},
        task_ids=[f"{team_id}-task-a"],
        expected_revision=repo.read().revision,
    )
    admin.record_deliverable(
        deliverable_id=f"{team_id}-deliverable",
        task_id=f"{team_id}-task-a",
        producer_agent_id=worker_agent_id,
        summary=f"{team_id.title()} implementation ready",
        artifact_refs=[f"artifact-space:/teams/{team_id}/large-artifact"],
        state=TeamRecordState.PENDING,
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id=f"{team_id}-memory",
        team_id=team_id,
        project_id=project_id,
        codebase_id=f"{team_id}-repo",
        scope="project",
        kind="decision",
        title=f"{team_id.title()} memory",
        summary=f"{team_id} Qdrant memory should be explicit and compact",
        body=f"{team_id} memory body should not appear in search results",
        tags=["memory"],
        expected_revision=repo.read().revision,
    )


def _seed_read_records(repo: TeamStateRepository) -> None:
    def _mutate(state):
        for team_id in ("alpha", "beta"):
            state.review_requests[f"{team_id}-review"] = ReviewRequest(
                id=f"{team_id}-review",
                deliverable_id=f"{team_id}-deliverable",
                reviewer_agent_id=f"{team_id}-lead",
                state=TeamRecordState.PENDING,
                instructions=f"{team_id} review instructions " + ("z" * 4000),
            )
            state.deliverables[f"{team_id}-deliverable"].review_request_ids = [
                f"{team_id}-review"
            ]
            state.inbox[f"{team_id}-inbox"] = AgentInboxItem(
                id=f"{team_id}-inbox",
                recipient_agent_id=f"{team_id}-dev",
                sender_agent_id=f"{team_id}-lead",
                task_id=f"{team_id}-task-a",
                item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
                priority=TeamInboxPriority.HIGH,
                payload={
                    "title": f"{team_id.title()} directive",
                    "message": f"{team_id} directive details " + ("p" * 4000),
                    "raw_context": {"entries": list(range(500))},
                },
            )
            state.release_packages[f"{team_id}-release"] = ReleasePackage(
                id=f"{team_id}-release",
                project_id=f"{team_id}-project",
                deliverable_ids=[f"{team_id}-deliverable"],
                feature_branches=[f"team/{team_id}/features/task-a"],
                state=TeamRecordState.PENDING,
            )
            state.agent_runtimes[f"{team_id}-dev"] = AgentRuntime(
                agent_id=f"{team_id}-dev",
                active_dispatch_id=None,
                wake_reason=None,
                next_wake_at=datetime(2099, 1, 1, tzinfo=UTC),
            )
            state.git_work_contexts[f"{team_id}-context"] = GitWorkContext(
                id=f"{team_id}-context",
                agent_id=f"{team_id}-dev",
                task_id=f"{team_id}-task-a",
                dispatch_id=f"{team_id}-dispatch",
                agent_branch=f"agents/session-{team_id}-dev/work-session-{team_id}-dev",
                base_branch="main",
                worktree_path=f"/worktrees/{team_id}",
            )
        return state

    repo.update(_mutate)


def _assert_read_success(result: dict[str, Any]) -> None:
    assert result["success"] is True
    assert result["progress_made"] is False
    assert result["error"] is None


def _items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("records")
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def _item(result: dict[str, Any]) -> dict[str, Any]:
    item = result.get("record")
    assert isinstance(item, dict)
    return item


def _call_denied_or_not_found(server, tool_name: str, arguments: dict[str, object]) -> None:
    try:
        result = _call_tool(server, tool_name, arguments)
    except ToolError as exc:
        message = str(exc).casefold()
        assert any(term in message for term in ("not found", "not visible", "scope"))
        assert "beta customer case" not in message
        assert "beta task a details" not in message
        return

    assert result["success"] is False
    message = json.dumps(result, sort_keys=True).casefold()
    assert any(term in message for term in ("not found", "not visible", "scope"))
    assert "beta customer case" not in message
    assert "beta task a details" not in message


def test_team_addon_mcp_registers_read_tool_names_as_addon_tools(tmp_path: Path) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    server = create_server(context)

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    session = context.policy.get_session("session-alpha-dev")
    visible = {
        tool_name_value(item.tool) for item in context.policy.visible_tools_for_session(session)
    }

    assert set(EXPECTED_READ_TOOL_NAMES).issubset(registered)
    assert set(EXPECTED_READ_TOOL_NAMES).issubset(context.policy.extra_mcp_tool_names)
    assert set(EXPECTED_READ_TOOL_NAMES).issubset(visible)


def test_team_addon_read_tools_do_not_require_active_dispatch_lease(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)
    before_revision = repo.read().revision

    assert repo.read().agent_runtimes["alpha-dev"].active_dispatch_id is None

    list_calls = [
        (TEAM_LIST_TASKS_TOOL, {}, "alpha-task-a"),
        (TEAM_LIST_INBOX_TOOL, {}, "alpha-inbox"),
        (TEAM_LIST_CASES_TOOL, {}, "alpha-case"),
        (TEAM_LIST_DELIVERABLES_TOOL, {}, "alpha-deliverable"),
        (TEAM_LIST_REVIEW_REQUESTS_TOOL, {}, "alpha-review"),
        (TEAM_LIST_RELEASE_PACKAGES_TOOL, {}, "alpha-release"),
        (TEAM_LIST_AGENTS_TOOL, {}, "alpha-dev"),
        (TEAM_LIST_PROJECTS_TOOL, {}, "alpha-project"),
    ]
    for tool_name, arguments, expected_id in list_calls:
        result = _call_tool(
            server,
            tool_name,
            {"session_id": "session-alpha-dev", "limit": 10, **arguments},
        )
        _assert_read_success(result)
        assert any(row.get("id") == expected_id for row in _items(result))

    attention_result = _call_tool(
        server,
        TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        {"session_id": "session-alpha-dev", "limit": 10},
    )
    _assert_read_success(attention_result)
    assert _items(attention_result)

    memory_result = _call_tool(
        server,
        TEAM_SEARCH_MEMORY_TOOL,
        {
            "session_id": "session-alpha-dev",
            "project_id": "alpha-project",
            "query_text": "Qdrant",
            "limit": 10,
        },
    )
    _assert_read_success(memory_result)
    assert [row["id"] for row in memory_result["records"]] == ["alpha-memory"]
    assert "body" not in memory_result["records"][0]

    get_calls = [
        (TEAM_GET_TASK_TOOL, {"task_id": "alpha-task-a"}, "alpha-task-a"),
        (TEAM_GET_INBOX_ITEM_TOOL, {"inbox_item_id": "alpha-inbox"}, "alpha-inbox"),
        (TEAM_GET_CASE_TOOL, {"case_id": "alpha-case"}, "alpha-case"),
        (
            TEAM_GET_DELIVERABLE_TOOL,
            {"deliverable_id": "alpha-deliverable"},
            "alpha-deliverable",
        ),
        (
            TEAM_GET_REVIEW_REQUEST_TOOL,
            {"review_request_id": "alpha-review"},
            "alpha-review",
        ),
        (
            TEAM_GET_RELEASE_PACKAGE_TOOL,
            {"release_package_id": "alpha-release"},
            "alpha-release",
        ),
        (TEAM_GET_AGENT_TOOL, {"agent_id": "alpha-dev"}, "alpha-dev"),
        (TEAM_GET_MY_IDENTITY_TOOL, {}, "alpha-dev"),
        (TEAM_GET_PROJECT_TOOL, {"project_id": "alpha-project"}, "alpha-project"),
    ]
    for tool_name, arguments, expected_id in get_calls:
        result = _call_tool(
            server,
            tool_name,
            {"session_id": "session-alpha-dev", **arguments},
        )
        _assert_read_success(result)
        assert _item(result)["id"] == expected_id

    assert repo.read().revision == before_revision


def test_team_addon_task_read_tools_surface_callers_git_work_context(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    list_result = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 10},
    )
    _assert_read_success(list_result)
    alpha_task = next(row for row in _items(list_result) if row["id"] == "alpha-task-a")
    assert alpha_task["git_work_contexts"] == [
        {
            "id": "alpha-context",
            "branch_context_id": "alpha-context",
            "agent_id": "alpha-dev",
            "task_id": "alpha-task-a",
            "agent_branch": "agents/session-alpha-dev/work-session-alpha-dev",
            "base_branch": "main",
            "dispatch_id": "alpha-dispatch",
            "worktree_path": "/worktrees/alpha",
            "state": "active",
        }
    ]

    get_result = _call_tool(
        server,
        TEAM_GET_TASK_TOOL,
        {"session_id": "session-alpha-dev", "task_id": "alpha-task-a"},
    )
    _assert_read_success(get_result)
    assert _item(get_result)["git_work_contexts"] == alpha_task["git_work_contexts"]
    assert "beta-context" not in json.dumps(list_result, sort_keys=True)
    assert "beta-context" not in json.dumps(get_result, sort_keys=True)


def test_team_addon_get_my_identity_returns_complete_bounded_identity(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    _upsert_session(
        context,
        session_id="session-alpha-lead",
        allowed_tools=list(EXPECTED_READ_TOOL_NAMES),
    )
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    worker_result = _call_tool(
        server,
        TEAM_GET_MY_IDENTITY_TOOL,
        {"session_id": "session-alpha-dev"},
    )
    _assert_read_success(worker_result)
    worker_identity = _item(worker_result)

    assert worker_identity["id"] == "alpha-dev"
    assert worker_identity["session_id"] == "session-alpha-dev"
    assert worker_identity["display_name"] == "Alpha Developer"
    assert worker_identity["role_name"] == "Developer"
    assert worker_identity["role_description"].startswith(
        "Alpha developer role owns implementation details"
    )
    assert "..." not in worker_identity["role_description"]
    assert "request_payment_preparation" in worker_identity["role_permissions"]
    assert "request_payment_preparation" in worker_identity["effective_role_permissions"]
    assert worker_identity["superior"] == {
        "id": "alpha-lead",
        "display_name": "Alpha Lead",
        "team_id": "alpha",
        "role_id": "alpha-project__project-lead",
        "role_name": "Project Lead",
        "status": "active",
    }
    assert worker_identity["subordinates"] == []
    assert "tasks" not in worker_identity
    assert "inbox" not in worker_identity
    assert "deliverables" not in worker_identity

    lead_result = _call_tool(
        server,
        TEAM_GET_MY_IDENTITY_TOOL,
        {"session_id": "session-alpha-lead"},
    )
    _assert_read_success(lead_result)
    lead_identity = _item(lead_result)

    assert lead_identity["id"] == "alpha-lead"
    assert lead_identity["display_name"] == "Alpha Lead"
    assert lead_identity["superior"] == {
        "id": "alpha-manager",
        "display_name": "Alpha Manager",
        "team_id": "alpha",
        "role_id": "alpha__manager",
        "role_name": "Manager",
        "status": "active",
    }
    assert {
        "id": "alpha-dev",
        "display_name": "Alpha Developer",
        "team_id": "alpha",
        "role_id": "alpha__developer",
        "role_name": "Developer",
        "status": "active",
    } in lead_identity["subordinates"]


def test_team_addon_get_agent_visibility_is_team_scoped_not_project_scoped(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_LIST_AGENTS_TOOL, TEAM_GET_AGENT_TOOL],
    )
    repo = _seed_read_tool_state(context.config.runtime_dir)
    admin = _team_admin_service(repo)
    admin.create_role(
        role_id="alpha__observer",
        team_id="alpha",
        name="Observer",
        permissions=[RolePermission.REQUEST_RELEASE],
        expected_revision=repo.read().revision,
    )
    admin.register_agent(
        agent_id="alpha-observer",
        team_id="alpha",
        role_id="alpha__observer",
        session_id="session-alpha-observer",
        display_name="Alpha Observer",
        expected_revision=repo.read().revision,
    )
    server = create_server(context)

    list_result = _call_tool(
        server,
        TEAM_LIST_AGENTS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )
    _assert_read_success(list_result)
    listed_ids = {row["id"] for row in _items(list_result)}

    assert "alpha-observer" in listed_ids
    assert "beta-dev" not in listed_ids

    observer_result = _call_tool(
        server,
        TEAM_GET_AGENT_TOOL,
        {"session_id": "session-alpha-dev", "agent_id": "alpha-observer"},
    )
    _assert_read_success(observer_result)
    observer = _item(observer_result)
    assert observer["id"] == "alpha-observer"
    assert "session_id" not in observer
    assert observer["display_name"] == "Alpha Observer"
    assert observer["role_permissions"] == ["request_release"]
    assert observer["effective_role_permissions"] == ["request_release"]

    _call_denied_or_not_found(
        server,
        TEAM_GET_AGENT_TOOL,
        {"session_id": "session-alpha-dev", "agent_id": "beta-dev"},
    )


def test_team_addon_read_tools_scope_task_and_project_by_callers_team(
    tmp_path: Path,
) -> None:
    scoped_tools = [
        TEAM_LIST_TASKS_TOOL,
        TEAM_GET_TASK_TOOL,
        TEAM_LIST_PROJECTS_TOOL,
        TEAM_GET_PROJECT_TOOL,
    ]
    context = _runtime(tmp_path, allowed_tools=scoped_tools)
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    task_result = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )
    project_result = _call_tool(
        server,
        TEAM_LIST_PROJECTS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )

    _assert_read_success(task_result)
    _assert_read_success(project_result)
    assert {row["id"] for row in _items(task_result)} == {
        "alpha-task-a",
        "alpha-task-b",
        "alpha-task-c",
    }
    assert {row["id"] for row in _items(project_result)} == {"alpha-project"}

    _call_denied_or_not_found(
        server,
        TEAM_GET_TASK_TOOL,
        {"session_id": "session-alpha-dev", "task_id": "beta-task-a"},
    )
    _call_denied_or_not_found(
        server,
        TEAM_GET_PROJECT_TOOL,
        {"session_id": "session-alpha-dev", "project_id": "beta-project"},
    )


def test_team_addon_read_tools_are_still_gated_by_session_policy(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_GET_TASK_TOOL])
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    with pytest.raises(
        (PolicyError, ToolError),
        match="not allowed|denied|not available",
    ):
        _call_tool(
            server,
            TEAM_LIST_TASKS_TOOL,
            {"session_id": "session-alpha-dev", "limit": 10},
        )


def test_team_addon_read_tool_lists_are_compact_and_paginated(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_LIST_TASKS_TOOL, TEAM_LIST_INBOX_TOOL],
    )
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    first_page = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 2, "offset": 0},
    )
    second_page = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-alpha-dev", "limit": 2, "cursor": "2"},
    )

    _assert_read_success(first_page)
    _assert_read_success(second_page)
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0
    assert first_page["total"] == 3
    assert first_page["next_cursor"] == "2"
    assert first_page["has_more"] is True
    assert second_page["limit"] == 2
    assert second_page["offset"] == 2
    assert second_page["total"] == 3
    assert second_page["next_cursor"] is None
    assert second_page["has_more"] is False

    first_ids = {row["id"] for row in _items(first_page)}
    second_ids = {row["id"] for row in _items(second_page)}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {"alpha-task-a", "alpha-task-b", "alpha-task-c"}

    for row in _items(first_page) + _items(second_page):
        assert {"id", "title", "state", "project_id", "owner_agent_id"}.issubset(row)
        assert "description" not in row
        assert "description_preview" in row
        assert "deliverable_ids" not in row

    inbox_result = _call_tool(
        server,
        TEAM_LIST_INBOX_TOOL,
        {"session_id": "session-alpha-dev", "limit": 10},
    )
    _assert_read_success(inbox_result)
    assert len(_items(inbox_result)) == 1
    inbox_row = _items(inbox_result)[0]
    assert {"id", "item_type", "state", "priority", "recipient_agent_id"}.issubset(inbox_row)
    assert inbox_row["id"] == "alpha-inbox"
    assert "payload" not in inbox_row
