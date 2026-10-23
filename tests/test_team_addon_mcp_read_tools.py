from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.addons import AddonLoaderConfig
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.artifact_spaces import ArtifactSpaceStore
from evander.config import GatewayConfig
from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from evander.gateway import create_server
from evander.models import SessionManifest, tool_name_value
from evander.policy import PolicyError
from evander.published_artifacts import PublishedArtifactStore
from evander.runtime import build_runtime
from evander_team_addon import mcp as team_mcp
from evander_team_addon.models import (
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    GitPromotionRecord,
    GitWorkContext,
    PersistentAgent,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    RolePermission,
    TeamDispatch,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import ADDON_ID, TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.read_service import TeamReadService


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
TEAM_IMPORT_ARTIFACT_TOOL = getattr(
    team_mcp,
    "TEAM_IMPORT_ARTIFACT_TOOL",
    "team_import_artifact",
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
TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL = getattr(
    team_mcp,
    "TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL",
    "team_get_current_dispatch_context",
)
TEAM_LIST_PROJECTS_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_PROJECTS_TOOL",
    "team_list_projects",
)
TEAM_GET_PROJECT_TOOL = getattr(team_mcp, "TEAM_GET_PROJECT_TOOL", "team_get_project")
TEAM_LIST_OUTCOME_KINDS_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_OUTCOME_KINDS_TOOL",
    "team_list_outcome_kinds",
)
TEAM_GET_OUTCOME_KIND_TOOL = getattr(
    team_mcp,
    "TEAM_GET_OUTCOME_KIND_TOOL",
    "team_get_outcome_kind",
)
TEAM_LIST_RUNTIME_ATTENTION_TOOL = getattr(
    team_mcp,
    "TEAM_LIST_RUNTIME_ATTENTION_TOOL",
    "team_list_runtime_attention",
)
TEAM_LIST_BLOCKERS_TOOL = getattr(team_mcp, "TEAM_LIST_BLOCKERS_TOOL", "team_list_blockers")
TEAM_GET_BLOCKER_TOOL = getattr(team_mcp, "TEAM_GET_BLOCKER_TOOL", "team_get_blocker")
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
    TEAM_IMPORT_ARTIFACT_TOOL,
    TEAM_LIST_REVIEW_REQUESTS_TOOL,
    TEAM_GET_REVIEW_REQUEST_TOOL,
    TEAM_LIST_RELEASE_PACKAGES_TOOL,
    TEAM_GET_RELEASE_PACKAGE_TOOL,
    TEAM_LIST_AGENTS_TOOL,
    TEAM_GET_AGENT_TOOL,
    TEAM_GET_MY_IDENTITY_TOOL,
    TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
    TEAM_LIST_PROJECTS_TOOL,
    TEAM_GET_PROJECT_TOOL,
    TEAM_LIST_OUTCOME_KINDS_TOOL,
    TEAM_GET_OUTCOME_KIND_TOOL,
    TEAM_LIST_RUNTIME_ATTENTION_TOOL,
    TEAM_LIST_BLOCKERS_TOOL,
    TEAM_GET_BLOCKER_TOOL,
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
    result = asyncio.run(server._tool_manager.call_tool(name, arguments, context=None))
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
    admin.create_outcome_kind_definition(
        definition_id="alpha-outcome-prepared-action",
        team_id="alpha",
        project_id="alpha-project",
        kind="prepared_action",
        name="Prepared action",
        description="A durable action ready for approval.",
        reference_requirements="Reference the prepared action ID.",
        evidence_requirements="Cite validation evidence.",
        expected_revision=repo.read().revision,
    )
    admin.create_outcome_kind_definition(
        definition_id="beta-outcome-private",
        team_id="beta",
        project_id="beta-project",
        kind="private_beta_outcome",
        name="Private beta outcome",
        expected_revision=repo.read().revision,
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
            "coordination boundaries, and exact acceptance criteria. " + ("role-detail " * 80)
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
            state.deliverables[f"{team_id}-deliverable"].review_request_ids = [f"{team_id}-review"]
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
                checkout_id=f"gc_{team_id}_context",
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


@pytest.mark.parametrize("detail", ["full", "summary"])
def test_current_dispatch_context_hydrates_only_exact_dispatch_git_rows(
    tmp_path: Path, detail: str,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(context.config.runtime_dir)

    def _activate_dispatch(state):
        dispatch_id = "alpha-current-dispatch"
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id="alpha-dev",
            session_id="session-alpha-dev",
            cause_kind="scheduled",
            task_ids=["alpha-task-a"],
            project_ids=["alpha-project"],
        )
        state.agent_runtimes["alpha-dev"].active_dispatch_id = dispatch_id
        state.git_work_contexts["alpha-current-context"] = GitWorkContext(
            id="alpha-current-context",
            checkout_id="gc_alpha_current_context",
            agent_id="alpha-dev",
            task_id="alpha-task-a",
            dispatch_id=dispatch_id,
            agent_branch="agents/session-alpha-dev/current",
            base_branch="main",
            worktree_path="/worktrees/alpha-current",
        )
        return state

    repo.update(_activate_dispatch)
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
        {"session_id": "session-alpha-dev", "detail": detail},
    )

    _assert_read_success(result)
    record = _item(result)
    assert record["dispatch"]["id"] == "alpha-current-dispatch"
    assert [row["id"] for row in record["git_work_contexts"]] == ["alpha-current-context"]
    assert record["git_work_contexts"][0]["checkout_id"] == "gc_alpha_current_context"
    if detail == "full":
        assert [row["id"] for row in record["tasks"][0]["git_work_contexts"]] == [
            "alpha-current-context"
        ]
        assert record["tasks"][0]["git_work_contexts"][0]["checkout_id"] == "gc_alpha_current_context"
    else:
        assert record["tasks"][0]["git_work_context_ids"] == ["alpha-current-context"]
    assert "alpha-context" not in json.dumps(record, sort_keys=True)
    assert "gc_alpha_context" not in json.dumps(record, sort_keys=True)
    assert "gc_beta_context" not in json.dumps(record, sort_keys=True)


def test_team_addon_read_tools_do_not_require_active_dispatch_lease(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)
    before_revision = repo.read().revision
    alpha_blocker_id = next(
        blocker.id
        for blocker in repo.read().blockers.values()
        if blocker.source_id == "alpha-task-c"
    )

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
        (
            TEAM_LIST_OUTCOME_KINDS_TOOL,
            {"project_id": "alpha-project"},
            "alpha-outcome-prepared-action",
        ),
        (TEAM_LIST_BLOCKERS_TOOL, {}, alpha_blocker_id),
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
        (
            TEAM_GET_OUTCOME_KIND_TOOL,
            {"definition_id": "alpha-outcome-prepared-action"},
            "alpha-outcome-prepared-action",
        ),
        (TEAM_GET_BLOCKER_TOOL, {"blocker_id": alpha_blocker_id}, alpha_blocker_id),
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
    _call_denied_or_not_found(
        server,
        TEAM_GET_OUTCOME_KIND_TOOL,
        {
            "session_id": "session-alpha-dev",
            "definition_id": "beta-outcome-private",
        },
    )


def test_runtime_attention_read_tool_exposes_derived_operator_context(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_LIST_RUNTIME_ATTENTION_TOOL])
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )

    _assert_read_success(result)
    row = next(record for record in _items(result) if record["id"] == "alpha-task-c")
    assert row["attention_group"] == "active_obligation"
    assert row["attention_reason"] == "task_blocked"
    assert row["occurred_at"]
    assert isinstance(row["age_seconds"], int)
    assert row["age_bucket"] in {"lt_1h", "lt_1d", "lt_7d", "gte_7d"}
    assert row["reconciliation_required"] is False
    assert row["terminal_context"] == []
    assert row["team_id"] == "alpha"
    assert row["project_id"] == "alpha-project"
    assert row["project_ids"] == ["alpha-project"]
    assert row["links"]["task_ids"] == ["alpha-task-c"]
    assert row["links"]["project_ids"] == ["alpha-project"]
    assert "raw_context" not in json.dumps(row)


def test_runtime_attention_read_tool_exposes_visible_failed_promotion_commit(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_LIST_RUNTIME_ATTENTION_TOOL])
    repo = _seed_read_tool_state(context.config.runtime_dir)

    def _add_failed_promotion(state):
        state.git_promotions["alpha-promotion-failed"] = GitPromotionRecord(
            id="alpha-promotion-failed",
            project_id="alpha-project",
            actor_agent_id="alpha-dev",
            source_branch="agents/session-alpha-dev/failed-promotion",
            target_branch="team/alpha/features/failed-promotion",
            promotion_kind="feature",
            state="failed",
            commit_sha="failed-commit-sha",
            error="promotion failed",
        )
        return state

    repo.update(_add_failed_promotion)
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )

    _assert_read_success(result)
    row = next(record for record in _items(result) if record["id"] == "alpha-promotion-failed")
    assert row["promotion_id"] == "alpha-promotion-failed"
    assert row["commit_sha"] == "failed-commit-sha"


def test_runtime_attention_read_tool_redacts_hidden_session_ids_from_narrative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_LIST_RUNTIME_ATTENTION_TOOL])
    _seed_read_tool_state(context.config.runtime_dir)
    expires_at = "2026-07-01T12:00:00+00:00"

    def _runtime_payload(_state):
        return {
            "attention": [
                {
                    "id": "alpha-lead:expired-session",
                    "kind": "expired_agent_session",
                    "severity": "critical",
                    "title": "Expired session for alpha-lead",
                    "detail": (
                        f"agent alpha-lead; session session-alpha-lead expired at {expires_at}"
                    ),
                    "state": "expired",
                    "agent_id": "alpha-lead",
                    "session_id": "session-alpha-lead",
                    "occurred_at": expires_at,
                    "links": {
                        "agent_ids": ["alpha-lead"],
                        "session_ids": ["session-alpha-lead"],
                    },
                },
                {
                    "id": "alpha-dev:expired-session",
                    "kind": "expired_agent_session",
                    "severity": "critical",
                    "title": "Expired session for alpha-dev",
                    "detail": (
                        f"agent alpha-dev; session session-alpha-dev expired at {expires_at}"
                    ),
                    "state": "expired",
                    "agent_id": "alpha-dev",
                    "session_id": "session-alpha-dev",
                    "occurred_at": expires_at,
                    "links": {
                        "agent_ids": ["alpha-dev"],
                        "session_ids": ["session-alpha-dev"],
                    },
                },
            ]
        }

    monkeypatch.setattr(
        "evander_team_addon.services.read_service.runtime_operations_payload",
        _runtime_payload,
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_LIST_RUNTIME_ATTENTION_TOOL,
        {"session_id": "session-alpha-dev", "limit": 20},
    )

    _assert_read_success(result)
    rows = {record["id"]: record for record in _items(result)}
    hidden = rows["alpha-lead:expired-session"]
    assert "session_id" not in hidden
    assert "session_ids" not in hidden["links"]
    assert "session-alpha-lead" not in json.dumps(hidden, sort_keys=True)
    assert hidden["detail"] == (f"agent alpha-lead; session [not visible] expired at {expires_at}")

    own = rows["alpha-dev:expired-session"]
    assert own["session_id"] == "session-alpha-dev"
    assert own["links"]["session_ids"] == ["session-alpha-dev"]
    assert "session session-alpha-dev expired" in own["detail"]


def test_runtime_attention_read_service_filters_payload_links_to_actor_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_read_tool_state(tmp_path / "runtime")

    def _add_teamless_inbox(state):
        state.agents["unassigned"] = PersistentAgent(
            id="unassigned",
            session_id="session-unassigned",
        )
        state.tasks["beta-task-a"].state = TeamTaskState.ACCEPTED
        state.deliverables["beta-deliverable"].state = TeamRecordState.ACCEPTED
        state.release_packages["beta-release"].state = TeamRecordState.ACCEPTED
        state.inbox["unassigned-inbox"] = AgentInboxItem(
            id="unassigned-inbox",
            recipient_agent_id="unassigned",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            payload={
                "message": "Review the referenced records.",
                "nested_context": {
                    "task_id": "beta-task-a",
                    "deliverable_id": "beta-deliverable",
                    "release_package_id": "beta-release",
                    "session_id": "session-beta-lead",
                },
            },
        )
        state.inbox["unassigned-passive-inbox"] = AgentInboxItem(
            id="unassigned-passive-inbox",
            recipient_agent_id="unassigned",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
            payload={"message": "Durable informational update."},
        )
        return state

    repo.update(_add_teamless_inbox)
    state = repo.read()
    state.tasks["unassigned-task"] = AgentTask(
        id="unassigned-task",
        owner_agent_id="unassigned",
        project_id="beta-project",
        priority=1,
        state=TeamTaskState.BLOCKED,
        title="Visible task with a hidden dependency",
        description="The dependency remains outside the actor's readable scope.",
        created_by_actor="operator",
        dependency_task_ids=["beta-task-a"],
    )
    result = TeamReadService(
        state=state,
        actor=state.agents["unassigned"],
    ).list_runtime_attention(
        limit=20,
    )

    row = next(record for record in result["records"] if record["id"] == "unassigned-inbox")
    assert row["links"] == {
        "agent_ids": ["unassigned"],
        "inbox_item_ids": ["unassigned-inbox"],
    }
    assert row["terminal_context"] == []
    assert row["reconciliation_required"] is False
    assert row["attention_group"] == "active_obligation"
    assert row["attention_reason"] == "awaiting_response"
    assert row["project_ids"] == []
    assert "team_id" not in row
    assert "project_id" not in row
    assert "beta-" not in json.dumps(row, sort_keys=True)

    task_row = next(record for record in result["records"] if record["id"] == "unassigned-task")
    assert task_row["blocked_by_task_ids"] == []
    assert task_row["links"]["task_ids"] == ["unassigned-task"]
    assert "session-beta-lead" not in json.dumps(result, sort_keys=True)
    row_ids = [record["id"] for record in result["records"]]
    assert row_ids.index("unassigned-inbox") < row_ids.index("unassigned-passive-inbox")
    group_ranks = {
        "active_obligation": 0,
        "failed_attempt": 1,
        "retained_history": 2,
    }
    assert [group_ranks[record["attention_group"]] for record in result["records"]] == sorted(
        group_ranks[record["attention_group"]] for record in result["records"]
    )


def test_team_import_artifact_allows_same_team_artifact_without_deliverable_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_IMPORT_ARTIFACT_TOOL],
    )
    recipient_session = context.session_repository.get_session("session-alpha-dev")
    assert recipient_session is not None
    context.session_repository.upsert_session(
        recipient_session.model_copy(update={"vm_id": "vm-alpha-dev", "vm_provider": "libvirt_vm"})
    )
    repo = _seed_read_tool_state(context.config.runtime_dir)
    artifact_space_path = "/teams/alpha/projects/alpha-project/deliverables/1930-export"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_key = "team/alpha/alpha-project/1930-export/dispatch-manager/bundle"
    published_store = PublishedArtifactStore(context.config.runtime_dir)
    artifact_record = published_store.publish_single_file(
        key=artifact_key,
        session_id="session-alpha-manager",
        filename="1930.csv",
        artifact_path="operator-1930-export/1930.csv",
        content=b"Date,Description,Amount\n2026-07-01,Example,-100.00\n",
    )
    artifact_store = ArtifactSpaceStore(context.config.runtime_dir)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={
            "team_id": "alpha",
            "project_id": "alpha-project",
            "deliverable_id": "alpha-deliverable",
            "source": "team_addon",
        },
        subject="team-agent:alpha-manager",
    )
    artifact_version = artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject="team-agent:alpha-manager",
    ).latest_versions_for_space(artifact_space_path, artifact_id="bundle")[0]

    assert repo.read().agent_runtimes["alpha-dev"].active_dispatch_id is None
    ref_hash = hashlib.sha256(artifact_space_path.encode("utf-8")).hexdigest()[:16]
    expected_destination = f"team-artifact-imports/bundle/ref-{ref_hash}-{artifact_version.id}"
    copy_requests: list[dict[str, Any]] = []
    exec_requests: list[dict[str, Any]] = []

    def fake_vm_copy_files(
        self: AdminRuntimeProviderAdapter,
        **kwargs: Any,
    ) -> dict[str, Any]:
        copy_requests.append(dict(kwargs))
        return {
            "success": True,
            "message": "copied",
            "vm_name": str(kwargs["target"]),
            "copied_count": 1,
            "copied_paths": [str(kwargs["files"][0]["destination_path"])],
        }

    def fake_vm_exec(
        self: AdminRuntimeProviderAdapter,
        **kwargs: Any,
    ) -> dict[str, Any]:
        exec_requests.append(dict(kwargs))
        payload = {
            "success": True,
            "artifacts_root": "/artifacts",
            "destination_path": expected_destination,
            "imported_file_count": 1,
            "imported_paths": [f"{expected_destination}/operator-1930-export/1930.csv"],
            "source_byte_count": 53,
            "overwrite": True,
        }
        return {
            "success": True,
            "message": "imported",
            "vm_name": str(kwargs["target"]),
            "exit_code": 0,
            "stdout": (
                "__EVANDER_ARTIFACT_EFFECT_JSON_BEGIN__\n"
                f"{json.dumps(payload, sort_keys=True)}\n"
                "__EVANDER_ARTIFACT_EFFECT_JSON_END__\n"
            ),
        }

    monkeypatch.setattr(AdminRuntimeProviderAdapter, "vm_copy_files", fake_vm_copy_files)
    monkeypatch.setattr(AdminRuntimeProviderAdapter, "vm_exec", fake_vm_exec)
    server = create_server(context)
    before_revision = repo.read().revision

    result = _call_tool(
        server,
        TEAM_IMPORT_ARTIFACT_TOOL,
        {"session_id": "session-alpha-dev", "artifact_ref": artifact_ref},
    )

    _assert_read_success(result)
    assert result["artifact_ref"] == artifact_ref
    assert result["artifact_key"] == artifact_key
    assert result["artifact_space_version_id"] == artifact_version.id
    assert result["artifact_space_version"] == artifact_version.version
    assert result["artifact_path"] == f"/artifacts/{expected_destination}"
    assert result["imported_paths"] == [f"{result['artifact_path']}/operator-1930-export/1930.csv"]
    assert result["imported_file_count"] == 1
    assert result["archive_sha256"] == artifact_record.sha256
    assert copy_requests[0]["target"] == "vm-alpha-dev"
    assert copy_requests[0]["files"][0]["source_path"] == str(
        published_store.get(artifact_key).blob_path
    )
    assert exec_requests[0]["target"] == "vm-alpha-dev"
    assert expected_destination in exec_requests[0]["command"]
    assert repo.read().revision == before_revision


def test_team_import_artifact_enforces_artifact_space_read_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_IMPORT_ARTIFACT_TOOL])
    repo = _seed_read_tool_state(context.config.runtime_dir)
    artifact_space_path = "/teams/alpha/restricted"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_store = ArtifactSpaceStore(context.config.runtime_dir)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={"team_id": "alpha", "source": "team_addon"},
        grants={
            "read_subjects": ["team-agent:alpha-lead"],
            "write_subjects": ["team-agent:alpha-lead"],
            "manage_subjects": ["team-agent:alpha-lead"],
        },
        subject="team-agent:alpha-lead",
    )

    def _publish_ref_on_visible_deliverable(state):
        state.deliverables["alpha-deliverable"].artifact_refs = [artifact_ref]
        return state

    repo.update(_publish_ref_on_visible_deliverable)

    def fail_import(self: AgentCopyFilesEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("permission-denied artifact must not be imported")

    monkeypatch.setattr(AgentCopyFilesEffectProvider, "execute", fail_import)
    server = create_server(context)

    with pytest.raises(ToolError, match="team-agent:alpha-dev cannot read artifact space"):
        _call_tool(
            server,
            TEAM_IMPORT_ARTIFACT_TOOL,
            {"session_id": "session-alpha-dev", "artifact_ref": artifact_ref},
        )


def test_team_import_artifact_rejects_cross_team_ref_on_visible_deliverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_IMPORT_ARTIFACT_TOOL])
    repo = _seed_read_tool_state(context.config.runtime_dir)
    artifact_space_path = "/teams/beta/private-deliverable"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_store = ArtifactSpaceStore(context.config.runtime_dir)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={
            "team_id": "beta",
            "project_id": "beta-project",
            "source": "team_addon",
        },
        subject="team-agent:beta-lead",
    )
    artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key="team/beta/beta-project/private-deliverable/dispatch-beta/bundle",
        created_by_subject="team-agent:beta-lead",
    )

    def _launder_ref_on_visible_deliverable(state):
        state.deliverables["alpha-deliverable"].artifact_refs = [artifact_ref]
        return state

    repo.update(_launder_ref_on_visible_deliverable)

    def fail_import(self: AgentCopyFilesEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("out-of-scope artifact must not be imported")

    monkeypatch.setattr(AgentCopyFilesEffectProvider, "execute", fail_import)
    server = create_server(context)

    with pytest.raises(ToolError, match="artifact space is not owned by Team alpha"):
        _call_tool(
            server,
            TEAM_IMPORT_ARTIFACT_TOOL,
            {"session_id": "session-alpha-dev", "artifact_ref": artifact_ref},
        )


def test_team_import_artifact_rejects_cross_team_key_in_same_team_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_IMPORT_ARTIFACT_TOOL])
    _seed_read_tool_state(context.config.runtime_dir)
    artifact_space_path = "/teams/alpha/aliased-private-deliverable"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_store = ArtifactSpaceStore(context.config.runtime_dir)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={"team_id": "alpha", "source": "team_addon"},
        subject="team-agent:alpha-dev",
    )
    artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key="team/beta/beta-project/private-deliverable/dispatch-beta/bundle",
        created_by_subject="team-agent:alpha-dev",
    )

    def fail_import(self: AgentCopyFilesEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("out-of-scope artifact key must not be imported")

    monkeypatch.setattr(AgentCopyFilesEffectProvider, "execute", fail_import)
    server = create_server(context)

    with pytest.raises(ToolError, match="artifact version is not owned by Team alpha"):
        _call_tool(
            server,
            TEAM_IMPORT_ARTIFACT_TOOL,
            {"session_id": "session-alpha-dev", "artifact_ref": artifact_ref},
        )


def test_team_search_memory_status_filter_reaches_archived_records(tmp_path: Path) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_SEARCH_MEMORY_TOOL])
    repo = _seed_read_tool_state(context.config.runtime_dir)
    _team_admin_service(repo).archive_memory(
        memory_id="alpha-memory",
        expected_revision=repo.read().revision,
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_SEARCH_MEMORY_TOOL,
        {
            "session_id": "session-alpha-dev",
            "project_id": "alpha-project",
            "statuses": ["archived"],
            "limit": 10,
        },
    )

    _assert_read_success(result)
    assert [row["id"] for row in result["records"]] == ["alpha-memory"]
    assert result["records"][0]["status"] == "archived"


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
            "checkout_id": "gc_alpha_context",
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
    assert "gc_beta_context" not in json.dumps(list_result, sort_keys=True)
    assert "gc_beta_context" not in json.dumps(get_result, sort_keys=True)


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
    assert len(_items(inbox_result)) == 2
    inbox_row = next(row for row in _items(inbox_result) if row["id"] == "alpha-inbox")
    assert {"id", "item_type", "state", "priority", "recipient_agent_id"}.issubset(inbox_row)
    assert inbox_row["id"] == "alpha-inbox"
    assert "payload" not in inbox_row
    blocker_row = next(row for row in _items(inbox_result) if row["item_type"] == "status_request")
    assert blocker_row["message"] == "Task blocked: Alpha task c"
