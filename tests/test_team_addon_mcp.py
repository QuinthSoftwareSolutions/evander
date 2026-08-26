from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonLoaderConfig
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.events.scheduled import ScheduledInternalEventStore
from evander.gateway import create_server
from evander.models import ApprovalStatus, SessionManifest, ToolName, tool_name_value
from evander.model_usage.models import ModelUsageCorrelationContext
from evander.model_usage.runtime import model_usage_store
from evander.operator_steering import operator_steering_note_store
from evander.runtime import build_runtime
import evander_team_addon.mcp as team_addon_mcp
from internal_event_test_support import default_orchestration_database_path
from evander_team_addon.mcp import (
    MCP_TOOL_NAMES,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_CLOSE_POST_RELEASE_CASE_TOOL,
    TEAM_CREATE_CASE_TOOL,
    TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL,
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_DECIDE_INITIAL_RELEASE_TOOL,
    TEAM_DECIDE_DELIVERABLE_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_ESCALATE_OPERATOR_TOOL,
    TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL,
    TEAM_LIST_TASKS_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
    TEAM_RETIRE_REVIEW_TOOL,
    TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_RETRY_REVIEW_TOOL,
    TEAM_RETRY_RELEASE_GATE_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_WITHDRAW_REVIEW_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_UPDATE_TASK_TOOL,
    _team_mcp_git_candidate_commit_resolver,
    _team_mcp_git_candidate_snapshot_pinner,
)
from evander.review_snapshots import (
    team_deliverable_snapshot_binding_id,
    team_deliverable_snapshot_ref,
)
from evander_team_addon.chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from evander_team_addon.models import (
    AgentRuntime,
    DeliverableInitialReleaseDisposition,
    DeliverableReviewManifest,
    DeliverableVerification,
    EphemeralAgentPolicy,
    GitWorkContext,
    GovernanceAgendaItemState,
    InitialReleaseDispositionState,
    ProjectGitConfig,
    ProjectGoal,
    ProjectReleasePolicy,
    ReviewRequest,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamChatMutationAuthorization,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import (
    ADDON_ID,
    TeamStateRepository,
)
from evander_team_addon.operator_chat_steering import (
    queue_operator_chat_steering,
    reconcile_operator_chat_steering_deliveries,
)
from evander_team_addon.services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
)
from evander_team_addon.services.commands.chat_commands import TeamChatCommandService
from evander_team_addon.services.git_release_service import TeamGitReleaseService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_git_release_service(repo: TeamStateRepository) -> TeamGitReleaseService:
    return TeamGitReleaseService(repo)


def _authorized_chat_mutation(repo: TeamStateRepository, agent_id: str):
    state = repo.read()
    return project_scoped_team_chat_authorization(
        state,
        agent=state.agents[agent_id],
        source="internal",
        principal_id="operator@example.com",
        recipient_directory_path="root",
        project_is_authorized=lambda _directory_path: True,
    )


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


def _call_tool_with_context(
    server,
    name: str,
    arguments: dict[str, object],
    context: object,
):
    result = asyncio.run(server._tool_manager.call_tool(name, arguments, context=context))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def _make_request_context(*, session_id: str) -> object:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                query_params={"session_id": session_id},
                headers={},
            )
        )
    )


def _upsert_session(
    context,
    *,
    session_id: str,
    allowed_tools: list[str],
    directory_path: str = "root",
) -> None:
    context.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path=directory_path,
        )
    )


def _seed_governance_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _team_admin_service(repo).create_team(
        team_id="other-team",
        name="Other Team",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    return repo


def _seed_planning_state(runtime_dir: Path) -> TeamStateRepository:
    repo = _seed_governance_state(runtime_dir)
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    return repo


def _lease_planning_agents(repo: TeamStateRepository) -> None:
    def _lease(state):
        for agent_id in ("manager", "lead", "backend"):
            _install_leased_dispatch(
                state,
                agent_id=agent_id,
                dispatch_id=f"dispatch-{agent_id}",
            )
        return state

    repo.update(_lease)


def _seed_slice8_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Task",
        description="Implement the task.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).record_deliverable(
        deliverable_id="deliverable",
        task_id="task",
        producer_agent_id="backend",
        summary="Ready for release.",
        state=TeamRecordState.ACCEPTED,
    )

    def _set_git_and_refs(state):
        project = state.projects["project"]
        project.git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        project.release_policy = ProjectReleasePolicy(allow_ungated=True)
        state.tasks["task"].git_base_branch = "main"
        state.git_work_contexts["ctx-deliverable"] = GitWorkContext(
            id="ctx-deliverable",
            agent_id="backend",
            task_id="task",
            base_branch="main",
            base_commit="base-commit",
            agent_branch="agents/session-backend/work",
            commit_sha="reviewed-commit",
        )
        deliverable = state.deliverables["deliverable"]
        deliverable.verification = DeliverableVerification(status="passed")
        refs = deliverable.git_refs
        refs.branch_context_id = "ctx-deliverable"
        refs.agent_branch = "agents/session-backend/work"
        refs.review_branch = "team/team/reviews/deliverable"
        refs.commit_sha = "reviewed-commit"
        refs.feature_branch = "team/team/features/task-backend"
        refs.feature_commit_sha = "reviewed-commit"
        deliverable.review_manifest = DeliverableReviewManifest(
            base_commit_sha="base-commit",
            reviewed_commit_sha="reviewed-commit",
            tree_hash="reviewed-tree",
            commit_shas=["reviewed-commit"],
            changed_paths=["src/reviewed.py"],
        )
        state.review_requests["review-deliverable"] = ReviewRequest(
            id="review-deliverable",
            deliverable_id="deliverable",
            reviewer_agent_id="lead",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha="reviewed-commit",
        )
        deliverable.review_request_ids = ["review-deliverable"]
        return state

    repo.update(_set_git_and_refs)
    return repo


def _lease_agent(
    repo: TeamStateRepository,
    *,
    agent_id: str,
    dispatch_id: str,
) -> None:
    def _lease(state):
        _install_leased_dispatch(
            state,
            agent_id=agent_id,
            dispatch_id=dispatch_id,
        )
        return state

    repo.update(_lease)


def _install_leased_dispatch(
    state,
    *,
    agent_id: str,
    dispatch_id: str,
) -> None:
    agent = state.agents[agent_id]
    existing_runtime = state.agent_runtimes.get(agent_id)
    previous_dispatch = (
        state.dispatches.get(existing_runtime.active_dispatch_id)
        if existing_runtime is not None and existing_runtime.active_dispatch_id is not None
        else None
    )
    if (
        previous_dispatch is not None
        and previous_dispatch.id != dispatch_id
        and previous_dispatch.status in {TeamDispatchStatus.PREPARING, TeamDispatchStatus.LEASED}
    ):
        previous_dispatch.status = TeamDispatchStatus.COMPLETED
        previous_dispatch.completed_at = datetime(
            2098,
            12,
            31,
            23,
            59,
            tzinfo=UTC,
        )
    task_ids = sorted(
        task.id
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id
        and task.state
        not in {
            TeamTaskState.ACCEPTED,
            TeamTaskState.CANCELLED,
            TeamTaskState.FINISHED,
        }
    )
    project_ids = {
        assignment.project_id
        for assignment in state.project_assignments.values()
        if assignment.agent_id == agent_id
    }
    project_ids.update(
        project.id
        for project in state.projects.values()
        if project.lead_agent_id == agent_id
        or state.teams[project.team_id].manager_agent_id == agent_id
    )
    project_ids.update(state.tasks[task_id].project_id for task_id in task_ids)
    review_request_ids = sorted(
        review.id
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent_id
        and review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
        and review.retired_at is None
        and review.superseded_by_review_request_id is None
    )
    blocker_ids = sorted(
        blocker.id
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and (
            (blocker.route == TeamBlockerRoute.DELEGATED and blocker.delegate_agent_id == agent_id)
            or (
                blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                and blocker.owner_agent_id == agent_id
            )
        )
    )
    inbox_item_ids = sorted(
        item.id
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
    )
    managed_repo_ids = sorted(
        {
            project.git.managed_repo_id
            for project_id in project_ids
            if (project := state.projects.get(project_id)) is not None
            and project.git is not None
            and project.git.managed_repo_id is not None
        }
    )
    prepared_at = datetime(2098, 12, 31, 23, 59, tzinfo=UTC)
    lease_expires_at = datetime(2099, 1, 1, 0, 15, tzinfo=UTC)
    state.dispatches[dispatch_id] = TeamDispatch(
        id=dispatch_id,
        agent_id=agent_id,
        session_id=agent.session_id,
        cause_kind="scheduled",
        task_ids=task_ids,
        blocker_ids=blocker_ids,
        review_request_ids=review_request_ids,
        inbox_item_ids=inbox_item_ids,
        project_ids=sorted(project_ids),
        managed_repo_ids=managed_repo_ids,
        status=TeamDispatchStatus.LEASED,
        created_at=prepared_at,
        prepared_at=prepared_at,
        leased_at=prepared_at,
        lease_expires_at=lease_expires_at,
    )
    state.agent_runtimes[agent_id] = AgentRuntime(
        agent_id=agent_id,
        active_dispatch_id=dispatch_id,
        wake_leased_until=lease_expires_at,
        wake_reason="scheduled",
    )


def test_team_git_candidate_resolver_builds_admin_authority_for_gateway_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "d9b05522b48d4515c40537f6c6e6701f688f1575"
    build_calls: list[Path] = []
    resolve_calls: list[tuple[str, str, str]] = []
    ancestry_calls: list[dict[str, object]] = []

    class _ManagedGit:
        def resolve_branch_commit(
            self,
            managed_repo_id: str,
            *,
            directory_path: str,
            branch: str,
        ) -> str:
            resolve_calls.append((managed_repo_id, directory_path, branch))
            return expected_commit

        def verify_branch_commit_ancestry(
            self,
            managed_repo_id: str,
            **kwargs: object,
        ) -> None:
            ancestry_calls.append({"managed_repo_id": managed_repo_id, **kwargs})

    def _build_admin_runtime(runtime_dir: Path):
        build_calls.append(runtime_dir)
        return SimpleNamespace(managed_git_admin=_ManagedGit())

    monkeypatch.setattr(
        "evander.admin.composition.build_admin_runtime",
        _build_admin_runtime,
    )
    context = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        service=SimpleNamespace(),
    )
    resolver = _team_mcp_git_candidate_commit_resolver(context)
    assert build_calls == [tmp_path / "runtime"]

    assert (
        resolver(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root",
            agent_branch="agents/local-coder-2/tasks/task",
            required_ancestor_shas=["a" * 40, "b" * 40],
        )
        == expected_commit
    )
    assert (
        resolver(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root",
            agent_branch="agents/local-coder-2/tasks/task",
        )
        == expected_commit
    )
    assert resolve_calls == [
        (
            "mr_0123456789abcdefabcd",
            "root",
            "agents/local-coder-2/tasks/task",
        ),
        (
            "mr_0123456789abcdefabcd",
            "root",
            "agents/local-coder-2/tasks/task",
        ),
    ]
    assert ancestry_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root",
            "branch": "agents/local-coder-2/tasks/task",
            "commit_sha": expected_commit,
            "required_ancestor_shas": ["a" * 40, "b" * 40],
        }
    ]


def test_team_git_candidate_pinner_uses_deliverable_scoped_immutable_ref(
    tmp_path: Path,
) -> None:
    deliverable_id = "deliverable-direct-pinned"
    commit_sha = "a" * 40
    expected_ref = team_deliverable_snapshot_ref(deliverable_id)
    calls: list[dict[str, object]] = []

    class _ManagedGit:
        def pin_team_review_snapshot(
            self,
            managed_repo_id: str,
            directory_path: str,
            **kwargs: object,
        ) -> dict[str, str]:
            calls.append(
                {
                    "managed_repo_id": managed_repo_id,
                    "directory_path": directory_path,
                    **kwargs,
                }
            )
            return {"pin_ref": expected_ref, "commit_sha": commit_sha}

    pinner = _team_mcp_git_candidate_snapshot_pinner(
        SimpleNamespace(runtime_dir=tmp_path, service=SimpleNamespace()),
        managed_git=_ManagedGit(),
    )
    result = pinner(
        managed_repo_id="mr_0123456789abcdefabcd",
        directory_path="root",
        deliverable_id=deliverable_id,
        commit_sha=commit_sha,
        source_ref="agents/session-backend/tasks/task",
        expected_pinned_ref=expected_ref,
    )

    assert result == {"pin_ref": expected_ref, "commit_sha": commit_sha}
    assert calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root",
            "review_id": team_deliverable_snapshot_binding_id(deliverable_id),
            "commit_sha": commit_sha,
            "source_ref": "agents/session-backend/tasks/task",
            "pinned_ref": expected_ref,
        }
    ]


def test_team_addon_mcp_registers_all_plan_tool_names_as_addon_tools(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=list(MCP_TOOL_NAMES))
    server = create_server(context)

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    core_tool_names = {tool.value for tool in ToolName}
    session = context.policy.get_session("session-governance")
    visible = {
        tool_name_value(item.tool) for item in context.policy.visible_tools_for_session(session)
    }

    assert set(MCP_TOOL_NAMES).issubset(registered)
    assert set(MCP_TOOL_NAMES).issubset(context.policy.extra_mcp_tool_names)
    assert not set(MCP_TOOL_NAMES) & core_tool_names
    assert set(MCP_TOOL_NAMES).issubset(visible)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    submit_description = tools[TEAM_SUBMIT_DELIVERABLE_TOOL].description or ""
    decide_deliverable_description = tools[TEAM_DECIDE_DELIVERABLE_TOOL].description or ""
    publish_description = tools[TEAM_PUBLISH_REVIEW_BRANCH_TOOL].description or ""
    escalation_description = tools[TEAM_ESCALATE_OPERATOR_TOOL].description or ""
    retry_release_description = tools[TEAM_RETRY_RELEASE_GATE_TOOL].description or ""
    decide_review_description = tools[TEAM_DECIDE_REVIEW_TOOL].description or ""
    request_release = tools[TEAM_REQUEST_RELEASE_TOOL]
    retry_review_description = tools[TEAM_RETRY_REVIEW_TOOL].description or ""
    withdraw_review_description = tools[TEAM_WITHDRAW_REVIEW_TOOL].description or ""
    retire_review_description = tools[TEAM_RETIRE_REVIEW_TOOL].description or ""
    replacement_description = tools[TEAM_LINK_DELIVERABLE_REPLACEMENT_TOOL].description or ""
    reopen_rework_description = tools[TEAM_REOPEN_TASK_FOR_REWORK_TOOL].description or ""
    finding_resolution_description = (
        tools[TEAM_PROPOSE_HUMAN_FINDING_RESOLUTION_TOOL].description or ""
    )
    correction_route_description = tools[TEAM_ROUTE_HUMAN_FINDING_CORRECTION_TOOL].description or ""
    post_release_close_description = tools[TEAM_CLOSE_POST_RELEASE_CASE_TOOL].description or ""
    assert "verification records are optional diagnostic reports" in submit_description
    assert "git_refs are provenance only" in submit_description
    assert "use commit_sha, not commit" in submit_description
    assert "does not make the human decision" in finding_resolution_description
    assert "existing correction child" in correction_route_description
    assert "does not roll back" in post_release_close_description
    assert "Only the superior recorded on that handoff" in decide_deliverable_description
    assert "rejection requires a concrete reason" in decide_deliverable_description
    assert "team_decide_review" in decide_deliverable_description
    assert "not required merely because team_submit_deliverable included git_refs" in (
        publish_description
    )
    assert "receipt, not delivery confirmation" in escalation_description
    assert "report the escalation as queued or pending, never delivered" in (escalation_description)
    assert "infrastructure-failed or timed-out verification" in retry_release_description
    assert "process-blocked reviews" in retry_release_description
    assert "concrete evidence that the failure recovered" in retry_release_description
    assert "cannot retry a failed agent-requested" in retry_release_description
    assert "all current reviews are accepted" in decide_review_description
    assert "authoritative build/test result" in (request_release.description or "")
    assert "verification_waivers" not in request_release.parameters["properties"]
    assert "supersedes a current blocked" in retry_review_description
    assert "pending non-release review" in withdraw_review_description
    assert "project lead or team manager" in retire_review_description
    assert "open findings are not resolved or waived" in retire_review_description
    assert "old acceptance and reviews remain immutable" in reopen_rework_description
    assert "fresh formally reviewed deliverable" in reopen_rework_description
    assert "accepted replacement in the same project" in replacement_description


def test_teamless_governance_agent_can_reply_to_active_team_chat(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_REPLY_TO_CHAT_TOOL])
    repo = _seed_governance_state(context.config.runtime_dir)
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="governance-manager",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please acknowledge this policy.",
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=source_message.id,
        dispatch_id=f"team-chat:{source_message.id}",
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_REPLY_TO_CHAT_TOOL,
        {
            "session_id": "session-governance",
            "conversation_id": conversation.id,
            "message_id": source_message.id,
            "body": "Understood.",
            "request_id": "reply-to-policy",
        },
    )

    assert result["success"] is True
    stored = repo.read()
    assert stored.conversation_messages[source_message.id].status == "replied"
    reply = stored.conversation_messages[result["chat_message_id"]]
    assert reply.sender == "agent"
    assert reply.body == "Understood."


def test_active_team_mcp_result_repeats_plain_operator_attention_without_consuming_it(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_LIST_TASKS_TOOL, TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL],
    )
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend")
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please keep the interface stable while you work.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repo, "backend"),
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-backend"},
    )

    assert result["attention"]["message_ids"] == [message.id]
    assert "team_send_operator_chat_message" in result["attention"]["required_action"]
    attention_message = result["attention"]["messages"]
    assert len(attention_message) == 1
    assert attention_message[0]["message_id"] == message.id
    assert attention_message[0]["conversation_id"] == conversation.id
    assert attention_message[0]["body"] == "Please keep the interface stable while you work."
    assert attention_message[0]["operator_user_id"] == "operator@example.com"
    assert attention_message[0]["operator_display_name"] == "Operator"
    assert attention_message[0]["status"] == "delivered_in_dispatch"
    assert attention_message[0]["created_at"]
    stored = repo.read()
    assert stored.conversation_messages[message.id].status == "delivered_in_dispatch"
    assert stored.conversation_messages[message.id].seen_dispatch_id == "dispatch-backend"
    delivered_revision = stored.revision

    replay = _call_tool(
        server,
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-backend"},
    )
    assert replay["attention"]["message_ids"] == [message.id]
    assert repo.read().revision == delivered_revision

    reply = _call_tool(
        server,
        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        {
            "session_id": "session-backend",
            "recipient_operator_user_id": "operator@example.com",
            "body": "Understood; I will keep it stable.",
            "source_message_ids": [message.id],
            "request_id": "reply-to-tool-attention-1",
        },
    )
    assert reply["success"] is True
    assert repo.read().conversation_messages[message.id].status == "replied"


def test_active_team_mcp_result_leaves_operator_attachment_for_chat_dispatch(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_LIST_TASKS_TOOL],
    )
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend")
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    attachment = TeamChatAttachmentStorage(context.config.runtime_dir).prepare_upload(
        content=b"operator notes",
        filename="notes.txt",
        mime_type="text/plain",
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Read the attached notes when you can.",
        attachments=[attachment],
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    result = _call_tool(
        create_server(context),
        TEAM_LIST_TASKS_TOOL,
        {"session_id": "session-backend"},
    )

    assert "attention" not in result
    assert repo.read().conversation_messages[message.id].status == "queued"


@pytest.mark.parametrize("authorization_kind", ["reply_only", "different_project"])
def test_insufficiently_authorized_operator_message_is_not_delivered_into_mutation_dispatch(
    tmp_path: Path,
    authorization_kind: str,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend")
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Try to mutate through the active project dispatch.",
        created_by="operator:operator@example.com",
        mutation_authorization=(
            TeamChatMutationAuthorization(
                mode="project_scoped",
                source="internal",
                principal_id="operator@example.com",
                recipient_directory_path="root",
                project_ids=["different-project"],
            )
            if authorization_kind == "different_project"
            else None
        ),
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    model_usage_store(context.config.runtime_dir).upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="session-backend",
            dispatch_id="dispatch-backend",
            agent_harness="codex",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )

    _state, pending = chat.list_pending_operator_attention_messages(
        agent_id="backend",
        dispatch_id="dispatch-backend",
    )
    _state, queued_message, steering = queue_operator_chat_steering(
        repo,
        message_id=message.id,
    )

    assert pending == []
    assert steering is None
    assert queued_message.status == "queued"
    assert "operator_steering" not in queued_message.metadata


def test_reply_only_chat_dispatch_completes_only_source_bound_attention(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    TeamChatAttachmentPolicyService(repo).set_override(
        directory_path="root",
        values={"agent_send_requires_approval": False},
        updated_by="test",
        expected_revision=repo.read().revision,
    )
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL],
    )
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        expected_revision=repo.read().revision,
    )
    state, active_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="This is the current reply-only batch.",
        expected_revision=state.revision,
    )
    active_dispatch_id = f"team-chat:{active_message.id}"
    state, _active_message = chat.begin_chat_message_dispatch(
        message_id=active_message.id,
        dispatch_id=active_dispatch_id,
    )
    state, attention_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="This arrived during the reply-only batch.",
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=attention_message.id,
        dispatch_id=f"team-chat:{attention_message.id}",
    )
    _state, delivered = chat.mark_operator_attention_messages_delivered(
        agent_id="backend",
        dispatch_id=active_dispatch_id,
        message_ids=[attention_message.id],
    )
    assert [message.id for message in delivered] == [attention_message.id]

    server = create_server(context)
    staged = TeamChatAttachmentStorage(context.config.runtime_dir).stage_agent_file(
        session_id="session-backend",
        source_path="/home/agent/attention.txt",
        content=b"must remain unavailable",
        filename="attention.txt",
        mime_type="text/plain",
    )
    attachment_reply = _call_tool(
        server,
        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        {
            "session_id": "session-backend",
            "recipient_operator_user_id": "operator@example.com",
            "body": "This source-bound reply must remain text-only.",
            "attachment_ids": [staged.id],
            "source_message_ids": [attention_message.id],
            "request_id": "reply-only-source-bound-attachment",
        },
    )
    reply = _call_tool(
        server,
        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        {
            "session_id": "session-backend",
            "recipient_operator_user_id": "operator@example.com",
            "body": "I handled the newly delivered attention.",
            "source_message_ids": [attention_message.id],
            "request_id": "reply-only-source-bound-attention",
        },
    )
    proactive = _call_tool(
        server,
        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        {
            "session_id": "session-backend",
            "recipient_operator_user_id": "operator@example.com",
            "body": "This proactive send must remain denied.",
            "request_id": "reply-only-proactive-send",
        },
    )

    assert attachment_reply["success"] is False
    assert attachment_reply["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert repo.read().chat_attachments == {}
    assert reply["success"] is True
    assert repo.read().conversation_messages[attention_message.id].status == "replied"
    assert proactive["success"] is False
    assert proactive["errors"][0]["code"] == "dispatch_scope_forbidden"


@pytest.mark.parametrize(
    ("tool_name", "expected_status"),
    [
        (TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL, "replied"),
        (TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL, "acknowledged"),
    ],
)
def test_operator_message_next_call_delivery_requires_explicit_completion(
    tmp_path: Path,
    tool_name: str,
    expected_status: str,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[tool_name],
    )
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend")
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please preserve the public interface.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repo, "backend"),
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    if tool_name == TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL:
        def _mark_external_origin(state):
            state.conversation_messages[message.id].metadata["external_origin"] = {
                "provider": "teams",
                "communication_target_id": "operator-chat",
                "source_thread_id": "thread-1",
            }
            return state

        repo.update(_mark_external_origin)
    model_usage_store(context.config.runtime_dir).upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="session-backend",
            dispatch_id="dispatch-backend",
            agent_harness="codex",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )

    queued_state, queued_message, link = queue_operator_chat_steering(
        repo,
        message_id=message.id,
    )

    assert link is not None
    assert queued_message.status == "queued"
    assert queued_state.conversation_messages[message.id].metadata["operator_steering"][
        "dispatch_id"
    ] == "dispatch-backend"
    _replay_state, _replay_message, replay_link = queue_operator_chat_steering(
        repo,
        message_id=message.id,
    )
    assert replay_link is not None
    assert replay_link["note_id"] == link["note_id"]
    steering_store = operator_steering_note_store(context.config.runtime_dir)
    note = steering_store.claim_next_pending(
        session_id="session-backend",
        dispatch_id="dispatch-backend",
    )
    assert note is not None
    steering_store.mark_delivered(
        note.note_id,
        provider="openai",
        provider_endpoint="responses",
    )
    delivered = reconcile_operator_chat_steering_deliveries(repo, state=queued_state)
    assert delivered.conversation_messages[message.id].status == "delivered_in_dispatch"
    assert delivered.conversation_messages[message.id].seen_dispatch_id == "dispatch-backend"

    server = create_server(context)
    if tool_name == TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL:
        result = _call_tool(
            server,
            tool_name,
            {
                "session_id": "session-backend",
                "recipient_operator_user_id": "operator@example.com",
                "body": "Understood; I kept it stable.",
                "source_message_ids": [message.id],
                "request_id": "reply-to-steering-1",
            },
        )
        reply = repo.read().conversation_messages[result["chat_message_id"]]
        assert reply.source_message_id == message.id
        assert reply.source_message_ids == [message.id]
        assert len(repo.read().external_chat_deliveries) == 1
        assert result["events"][0]["event_type"] == "team.chat.external_reply_requested"
    else:
        result = _call_tool(
            server,
            tool_name,
            {
                "session_id": "session-backend",
                "message_ids": [message.id],
                "disposition": "acted_no_reply",
                "summary": "Kept the public interface stable.",
                "request_id": "ack-steering-1",
            },
        )
        assert result["acknowledged_message_ids"] == [message.id]

    assert repo.read().conversation_messages[message.id].status == expected_status


def test_reply_only_team_chat_cannot_stage_attachment(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_STAGE_CHAT_ATTACHMENT_TOOL, TEAM_REPLY_TO_CHAT_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    TeamChatAttachmentPolicyService(repo).set_override(
        directory_path="root",
        values={"agent_send_requires_approval": False},
        updated_by="test",
        expected_revision=repo.read().revision,
    )
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="governance-manager",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please attach the policy summary.",
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=source_message.id,
        dispatch_id=f"team-chat:{source_message.id}",
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="not authorized for file attachments"):
        _call_tool(
            server,
            TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
            {
                "session_id": "session-governance",
                "source_path": "/home/agent/policy.txt",
            },
        )

    staged = TeamChatAttachmentStorage(context.config.runtime_dir).stage_agent_file(
        session_id="session-governance",
        source_path="/home/agent/policy.txt",
        content=b"policy",
        filename="policy.txt",
        mime_type="text/plain",
    )
    with pytest.raises(ToolError, match="not authorized for file attachments"):
        _call_tool(
            server,
            TEAM_REPLY_TO_CHAT_TOOL,
            {
                "session_id": "session-governance",
                "conversation_id": conversation.id,
                "message_id": source_message.id,
                "body": "Attached.",
                "attachment_ids": [staged.id],
                "request_id": "reply-with-previously-staged-policy",
            },
        )

    stored = repo.read()
    assert stored.conversation_messages[source_message.id].status == "running"
    assert stored.chat_attachments == {}


def test_teamless_governance_agent_cannot_reply_outside_active_team_chat(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_STAGE_CHAT_ATTACHMENT_TOOL, TEAM_REPLY_TO_CHAT_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="governance-manager",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please acknowledge this policy.",
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool(
            server,
            TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
            {
                "session_id": "session-governance",
                "source_path": "/home/agent/policy.txt",
            },
        )
    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool(
            server,
            TEAM_REPLY_TO_CHAT_TOOL,
            {
                "session_id": "session-governance",
                "conversation_id": conversation.id,
                "message_id": source_message.id,
                "body": "Understood.",
                "request_id": "reply-without-chat-dispatch",
            },
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.conversation_messages == before.conversation_messages


def test_teamless_governance_chat_dispatch_cannot_use_ordinary_team_tools(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_REPLY_TO_CHAT_TOOL, TEAM_RECORD_NOTE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="governance-manager",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please acknowledge this policy.",
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=source_message.id,
        dispatch_id=f"team-chat:{source_message.id}",
    )
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="Team-assigned persistent agent"):
        _call_tool(
            server,
            TEAM_RECORD_NOTE_TOOL,
            {
                "session_id": "session-governance",
                "body": "This ordinary Team mutation must remain unavailable.",
                "request_id": "teamless-chat-note",
            },
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.action_records == before.action_records


def test_team_chat_agent_attachment_staging_and_exact_approved_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_STAGE_CHAT_ATTACHMENT_TOOL, TEAM_REPLY_TO_CHAT_TOOL],
    )
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please return the report.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repo, "backend"),
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=source_message.id,
        dispatch_id=f"team-chat:{source_message.id}",
    )
    monkeypatch.setattr(
        AgentExecEffectProvider,
        "execute",
        lambda _self: {
            "success": True,
            "session_id": "session-backend",
            "stdout": (
                "__EVANDER_TEAM_CHAT_FILE_BEGIN__\n"
                '{"source_path":"/home/agent/report.txt","filename":"report.txt",'
                '"mime_type":"text/plain","byte_count":6,"content_base64":"cmVwb3J0"}\n'
                "__EVANDER_TEAM_CHAT_FILE_END__\n"
            ),
        },
    )
    server = create_server(context)

    staged_result = _call_tool(
        server,
        TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
        {
            "session_id": "session-backend",
            "source_path": "/home/agent/report.txt",
        },
    )
    staged = staged_result["attachment"]
    reply_arguments = {
        "session_id": "session-backend",
        "conversation_id": conversation.id,
        "message_id": source_message.id,
        "body": "Attached is the report.",
        "attachment_ids": [staged["id"]],
        "request_id": "reply-with-report",
    }
    with pytest.raises(ToolError, match="require exact approval"):
        _call_tool(server, TEAM_REPLY_TO_CHAT_TOOL, reply_arguments)

    approval_payload = {
        "tool_name": TEAM_REPLY_TO_CHAT_TOOL,
        "conversation_id": conversation.id,
        "message_id": source_message.id,
        "body": "Attached is the report.",
        "attachment_ids": [staged["id"]],
        "attachments": [
            {key: staged[key] for key in ("id", "filename", "mime_type", "byte_count", "sha256")}
        ],
    }
    approval = context.policy.create_approval(
        "session-backend",
        "mcp_tool_invocation",
        approval_payload,
        "Send report.txt to the Team Chat operator",
        tool_name=TEAM_REPLY_TO_CHAT_TOOL,
    )
    approval.status = ApprovalStatus.APPROVED
    context.policy.approval_store.put(approval.request_id, approval)

    replied = _call_tool(
        server,
        TEAM_REPLY_TO_CHAT_TOOL,
        {**reply_arguments, "approval_request_id": approval.request_id},
    )

    assert replied["success"] is True
    stored = repo.read()
    reply = stored.conversation_messages[replied["chat_message_id"]]
    assert reply.body == "Attached is the report."
    assert len(reply.attachment_ids) == 1
    attachment = stored.chat_attachments[reply.attachment_ids[0]]
    assert attachment.filename == "report.txt"
    assert attachment.created_by_session_id == "session-backend"


def test_concurrent_chat_reply_failure_does_not_delete_committed_attachment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_REPLY_TO_CHAT_TOOL],
    )
    chat = TeamChatService(repo)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    state, source_message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please return the report.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repo, "backend"),
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=source_message.id,
        dispatch_id=f"team-chat:{source_message.id}",
    )
    TeamChatAttachmentPolicyService(repo).set_override(
        directory_path="root",
        values={"agent_send_requires_approval": False},
        updated_by="test",
        expected_revision=repo.read().revision,
    )
    storage = TeamChatAttachmentStorage(context.config.runtime_dir)
    staged = storage.stage_agent_file(
        session_id="session-backend",
        source_path="/home/agent/report.txt",
        content=b"report",
        filename="report.txt",
        mime_type="text/plain",
    )
    attachment_id = f"chatatt_{staged.id.removeprefix('chatstage_')}"
    barrier = threading.Barrier(2)
    original_reply = TeamChatCommandService.reply_to_chat_command

    def synchronized_reply(self, **kwargs):
        barrier.wait(timeout=5)
        return original_reply(self, **kwargs)

    monkeypatch.setattr(TeamChatCommandService, "reply_to_chat_command", synchronized_reply)
    server = create_server(context)
    common_arguments = {
        "session_id": "session-backend",
        "conversation_id": conversation.id,
        "message_id": source_message.id,
        "body": "Attached is the report.",
        "attachment_ids": [staged.id],
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _call_tool,
                server,
                TEAM_REPLY_TO_CHAT_TOOL,
                {**common_arguments, "request_id": request_id},
            )
            for request_id in ("concurrent-reply-1", "concurrent-reply-2")
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sum(bool(result["success"]) for result in results) == 1
    committed = repo.read().chat_attachments[attachment_id]
    assert committed.message_id in repo.read().conversation_messages
    assert storage.asset_path(attachment_id).read_bytes() == b"report"


def test_staged_chat_attachment_cannot_be_claimed_by_a_second_message(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL],
    )
    _lease_planning_agents(repo)
    TeamChatService(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="operator@example.com",
        operator_display_name="Operator",
        expected_revision=repo.read().revision,
    )
    TeamChatAttachmentPolicyService(repo).set_override(
        directory_path="root",
        values={"agent_send_requires_approval": False},
        updated_by="test",
        expected_revision=repo.read().revision,
    )
    storage = TeamChatAttachmentStorage(context.config.runtime_dir)
    staged = storage.stage_agent_file(
        session_id="session-backend",
        source_path="/home/agent/report.txt",
        content=b"report",
        filename="report.txt",
        mime_type="text/plain",
    )
    server = create_server(context)
    arguments = {
        "session_id": "session-backend",
        "recipient_operator_user_id": "operator@example.com",
        "body": "Attached is the report.",
        "attachment_ids": [staged.id],
        "request_id": "send-report-1",
    }

    first = _call_tool(server, TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL, arguments)
    replay = _call_tool(server, TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL, arguments)
    second = _call_tool(
        server,
        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
        {**arguments, "request_id": "send-report-2"},
    )

    assert first["success"] is True
    assert replay["success"] is True
    assert replay["chat_message_id"] == first["chat_message_id"]
    assert second["success"] is False
    assert second["errors"][0]["code"] == "chat_attachment_already_claimed"
    attachment_id = f"chatatt_{staged.id.removeprefix('chatstage_')}"
    stored = repo.read()
    assert stored.chat_attachments[attachment_id].message_id == first["chat_message_id"]
    assert storage.asset_path(attachment_id).read_bytes() == b"report"


def test_team_addon_mcp_registers_operator_directive_tool(tmp_path: Path) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
            TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        ],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    server = create_server(context)

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    session = context.policy.get_session("session-governance")
    visible = {
        tool_name_value(item.tool) for item in context.policy.visible_tools_for_session(session)
    }

    assert TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL in registered
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in registered
    assert TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL in visible
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in visible

    result = _call_tool(
        server,
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        {
            "session_id": "session-governance",
            "team_id": "team",
            "title": "Confirm blocker",
            "message": "Confirm the exact blocker and keep the interim demo available.",
            "request_id": "request-directive-1",
            "source_message_id": "governance-dispatch-1",
        },
    )

    assert result["success"] is True
    assert result["recipient_agent_id"] == "manager"
    replayed = _call_tool(
        server,
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        {
            "session_id": "session-governance",
            "team_id": "team",
            "title": "Confirm blocker",
            "message": "Confirm the exact blocker and keep the interim demo available.",
            "request_id": "request-directive-1",
            "source_message_id": "governance-dispatch-1",
        },
    )
    assert replayed == result
    state = repo.read()
    item = state.inbox[result["inbox_item_id"]]
    assert item.recipient_agent_id == "manager"
    assert item.payload["source"] == "team_governance_mcp"
    assert item.payload["source_message_id"] == "governance-dispatch-1"
    assert item.payload["created_by"] == "agent-session:session-governance"
    assert item.payload["requesting_session_id"] == "session-governance"
    assert [
        item
        for item in state.inbox.values()
        if item.payload.get("source_message_id") == "governance-dispatch-1"
    ] == [item]
    assert state.agent_runtimes["manager"].next_wake_at is None


def test_team_addon_mcp_governance_tool_uses_ambient_session_context(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    server = create_server(context)

    result = _call_tool_with_context(
        server,
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        {
            "team_id": "team",
            "message": "Report current launch risk.",
            "request_id": "request-directive-ambient",
        },
        _make_request_context(session_id="session-governance"),
    )

    assert result["success"] is True
    state = repo.read()
    item = state.inbox[result["inbox_item_id"]]
    assert item.payload["requesting_session_id"] == "session-governance"


def test_team_addon_mcp_rejects_conflicting_explicit_session_before_mutation(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="session_id is bound by the MCP connection"):
        _call_tool_with_context(
            server,
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
            {
                "session_id": "session-other",
                "team_id": "team",
                "message": "This must not be applied.",
                "request_id": "request-directive-conflict",
            },
            _make_request_context(session_id="session-governance"),
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.inbox == before.inbox


def test_team_addon_mcp_normal_tool_requires_active_dispatch_without_mutation(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-manager",
        allowed_tools=[TEAM_RECORD_NOTE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool(
            server,
            TEAM_RECORD_NOTE_TOOL,
            {
                "session_id": "session-manager",
                "body": "This note should not be recorded without a lease.",
                "request_id": "request-note-denied",
            },
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.action_records == before.action_records


def test_team_addon_mcp_record_note_uses_command_implementation(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-manager",
        allowed_tools=[TEAM_RECORD_NOTE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)

    _lease_agent(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-mcp-note",
    )
    server = create_server(context)

    result = _call_tool_with_context(
        server,
        TEAM_RECORD_NOTE_TOOL,
        {
            "body": "MCP command note.",
            "request_id": "request-mcp-note",
        },
        _make_request_context(session_id="session-manager"),
    )

    assert result["success"] is True
    assert result["agent_id"] == "manager"
    assert result["note_id"] == result["action_record_id"]
    assert result["progress_made"] is False
    state = repo.read()
    assert state.action_records[result["action_record_id"]].action["body"] == ("MCP command note.")
    assert state.agent_runtimes["manager"].active_dispatch_id == "dispatch-mcp-note"


def test_team_addon_mcp_slice6_tools_use_command_implementation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_requests: list[list[str]] = []

    def _request_outbox_publication(_context, *, outbox_record_ids):
        normalized_ids = list(outbox_record_ids)
        if not normalized_ids:
            return None
        publication_requests.append(normalized_ids)
        return {
            "outbox_record_ids": normalized_ids,
            "event_ids": ["event-outbox-publication"],
            "wake_notified": True,
        }

    monkeypatch.setattr(
        team_addon_mcp,
        "request_team_outbox_publication_for_records",
        _request_outbox_publication,
    )
    slice6_tools = [
        TEAM_CREATE_CASE_TOOL,
        TEAM_UPDATE_CASE_TOOL,
        TEAM_CREATE_TASK_TOOL,
        TEAM_CREATE_TASKS_TOOL,
        TEAM_UPDATE_TASK_TOOL,
        TEAM_UPDATE_GOAL_TOOL,
        TEAM_STAND_DOWN_AGENT_TOOL,
        TEAM_REVOKE_STAND_DOWN_TOOL,
    ]
    context = _runtime(tmp_path, allowed_tools=[])
    for session_id in ("session-manager", "session-lead", "session-backend"):
        _upsert_session(context, session_id=session_id, allowed_tools=slice6_tools)
    repo = _seed_planning_state(context.config.runtime_dir)

    def _configure_project_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        return state

    repo.update(_configure_project_git)
    _lease_planning_agents(repo)
    server = create_server(context)

    created_case = _call_tool_with_context(
        server,
        TEAM_CREATE_CASE_TOOL,
        {
            "id": "case-mcp",
            "project_id": "project",
            "title": "MCP case",
            "request_id": "request-mcp-case",
        },
        _make_request_context(session_id="session-manager"),
    )
    updated_case = _call_tool_with_context(
        server,
        TEAM_UPDATE_CASE_TOOL,
        {
            "case_id": "case-mcp",
            "title": "MCP case updated",
            "request_id": "request-mcp-case-update",
        },
        _make_request_context(session_id="session-manager"),
    )
    closed_case = _call_tool_with_context(
        server,
        TEAM_UPDATE_CASE_TOOL,
        {
            "case_id": "case-mcp",
            "status": "closed",
            "request_id": "request-mcp-case-close",
        },
        _make_request_context(session_id="session-manager"),
    )
    created_task = _call_tool_with_context(
        server,
        TEAM_CREATE_TASK_TOOL,
        {
            "task_id": "task-mcp",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 1,
            "title": "MCP task",
            "description": "Build the MCP task.",
            "git_base_branch": "feature/mcp",
            "request_id": "request-mcp-task",
        },
        _make_request_context(session_id="session-lead"),
    )
    created_tasks = _call_tool_with_context(
        server,
        TEAM_CREATE_TASKS_TOOL,
        {
            "tasks": [
                {
                    "task_id": "task-mcp-batch-a",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Batch A",
                    "description": "First batch task.",
                    "git_base_branch": "feature/batch",
                },
                {
                    "task_id": "task-mcp-batch-b",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Batch B",
                    "description": "Second batch task.",
                    "parent_task_id": "task-mcp-batch-a",
                    "dependency_task_ids": ["task-mcp"],
                },
            ],
            "request_id": "request-mcp-task-batch",
        },
        _make_request_context(session_id="session-lead"),
    )
    _lease_agent(
        repo,
        agent_id="backend",
        dispatch_id="dispatch-backend-after-task-create",
    )
    updated_task = _call_tool_with_context(
        server,
        TEAM_UPDATE_TASK_TOOL,
        {
            "task_id": "task-mcp",
            "state": "in_progress",
            "git_base_branch": "feature/mcp-updated",
            "request_id": "request-mcp-task-update",
        },
        _make_request_context(session_id="session-backend"),
    )
    updated_goal = _call_tool_with_context(
        server,
        TEAM_UPDATE_GOAL_TOOL,
        {
            "project_id": "project",
            "goal_id": "goal",
            "state": "in_progress",
            "request_id": "request-mcp-goal",
        },
        _make_request_context(session_id="session-lead"),
    )
    stood_down = _call_tool_with_context(
        server,
        TEAM_STAND_DOWN_AGENT_TOOL,
        {
            "agent_id": "backend",
            "reason": "Pause backend.",
            "request_id": "request-mcp-stand-down",
            "scope": "agent",
        },
        _make_request_context(session_id="session-lead"),
    )
    revoked = _call_tool_with_context(
        server,
        TEAM_REVOKE_STAND_DOWN_TOOL,
        {"agent_id": "backend", "request_id": "request-mcp-revoke-stand-down"},
        _make_request_context(session_id="session-lead"),
    )

    assert created_case["success"] is True
    assert updated_case["updated_case_ids"] == ["case-mcp"]
    assert closed_case["updated_case_ids"] == ["case-mcp"]
    assert closed_case["outbox_publication_wake"]["wake_notified"] is True
    assert publication_requests == [closed_case["outbox_record_ids"]]
    assert created_task["created_task_ids"] == ["task-mcp"]
    assert created_task["published_event_ids"]
    assert created_task["outbox_record_ids"]
    assert created_task["completed_outbox_record_ids"] == created_task["outbox_record_ids"]
    wake_outbox_records = TeamStateRepository(context.config.runtime_dir).list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE
    )
    created_wake_outbox = [
        record for record in wake_outbox_records if record.id in created_task["outbox_record_ids"]
    ]
    assert len(created_wake_outbox) == 1
    assert created_wake_outbox[0].status.value == "completed"
    scheduled = ScheduledInternalEventStore(
        default_orchestration_database_path(context.config.runtime_dir)
    ).list_all()
    scheduled_wakes = [
        record
        for record in scheduled
        if record.event_type == AGENT_WAKE_REQUESTED_EVENT_TYPE
        and record.payload["agent_id"] == "backend"
    ]
    assert scheduled_wakes
    assert all(record.status == "pending" for record in scheduled_wakes)
    assert created_tasks["created_task_ids"] == [
        "task-mcp-batch-a",
        "task-mcp-batch-b",
    ]
    assert updated_task["updated_task_ids"] == ["task-mcp"]
    assert updated_goal["updated_goal_ids"] == ["goal"]
    assert stood_down["created_stand_down_ids"]
    assert revoked["revoked_stand_down_ids"] == stood_down["created_stand_down_ids"]
    state = repo.read()
    assert state.stand_downs[stood_down["stand_down_id"]].scope == "agent"
    assert state.cases["case-mcp"].title == "MCP case updated"
    assert state.cases["case-mcp"].status.value == "closed"
    assert state.tasks["task-mcp"].state.value == "in_progress"
    assert state.tasks["task-mcp"].git_base_branch == "feature/mcp-updated"
    assert state.tasks["task-mcp-batch-a"].git_base_branch == "feature/batch"
    assert state.tasks["task-mcp-batch-b"].git_base_branch == "feature/batch"
    assert state.projects["project"].goals[0].state == TeamRecordState.IN_PROGRESS


def test_team_addon_mcp_reopens_finished_task_for_reviewed_rework(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-manager",
        allowed_tools=[TEAM_REOPEN_TASK_FOR_REWORK_TOOL],
    )
    repo = _seed_slice8_state(context.config.runtime_dir)

    def _finish_task(state):
        state.tasks["task"].state = TeamTaskState.FINISHED
        return state

    repo.update(_finish_task)
    _lease_agent(
        repo,
        agent_id="manager",
        dispatch_id="dispatch-mcp-reopen-rework",
    )
    server = create_server(context)

    result = _call_tool_with_context(
        server,
        TEAM_REOPEN_TASK_FOR_REWORK_TOOL,
        {
            "task_id": "task",
            "accepted_deliverable_id": "deliverable",
            "reason": "The accepted candidate lacks immutable provenance.",
            "request_id": "request-mcp-reopen-rework",
        },
        _make_request_context(session_id="session-manager"),
    )

    assert result["success"] is True
    assert result["supersedes_deliverable_id"] == "deliverable"
    state = repo.read()
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.deliverables["deliverable"].rework_requested_by_agent_id == "manager"
    assert state.review_requests["review-deliverable"].state == TeamRecordState.ACCEPTED
    rework_item = state.inbox[result["created_inbox_item_ids"][0]]
    assert rework_item.recipient_agent_id == "backend"
    assert rework_item.payload["required_tool"] == ("team_submit_deliverable_for_review")


def test_team_addon_mcp_designates_existing_task_as_integration_boundary(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = _seed_planning_state(context.config.runtime_dir)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="clarification",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Clarify and integrate",
        description="Coordinate the implementation.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="implementation",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Implement",
        description="Produce the contribution.",
        created_by_agent_id="lead",
        parent_task_id="clarification",
    )
    admin.update_task(
        task_id="implementation",
        state=TeamTaskState.FINISHED,
    )
    _upsert_session(
        context,
        session_id="session-lead",
        allowed_tools=[TEAM_CREATE_TASK_TOOL, TEAM_UPDATE_TASK_TOOL],
    )
    _lease_agent(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-mcp-integration-boundary",
    )
    server = create_server(context)

    result = _call_tool_with_context(
        server,
        TEAM_UPDATE_TASK_TOOL,
        {
            "task_id": "clarification",
            "integration_boundary": True,
            "deliverable_contract": {
                "require_git_provenance": True,
                "required_contribution_task_ids": ["implementation"],
            },
            "request_id": "request-mcp-integration-boundary",
        },
        _make_request_context(session_id="session-lead"),
    )

    assert result["success"] is True, result
    task = repo.read().tasks["clarification"]
    assert task.integration_boundary is True
    assert task.deliverable_contract is not None
    assert task.deliverable_contract.require_git_provenance is True
    assert task.deliverable_contract.required_contribution_task_ids == ["implementation"]

    admin.create_or_reprioritize_task(
        task_id="ordinary-after-submission",
        owner_agent_id="lead",
        project_id="project",
        priority=2,
        title="Ordinary task",
        description="Edit after a deliverable exists.",
        created_by_agent_id="lead",
    )
    admin.record_deliverable(
        deliverable_id="ordinary-deliverable",
        task_id="ordinary-after-submission",
        producer_agent_id="lead",
        summary="Ordinary deliverable.",
        state=TeamRecordState.REJECTED,
    )
    admin.update_task(
        task_id="ordinary-after-submission",
        state=TeamTaskState.IN_PROGRESS,
    )
    _lease_agent(
        repo,
        agent_id="lead",
        dispatch_id="dispatch-mcp-echo-unchanged-contract",
    )
    echoed_noop = _call_tool_with_context(
        server,
        TEAM_UPDATE_TASK_TOOL,
        {
            "task_id": "ordinary-after-submission",
            "description": "Description edited after submission.",
            "integration_boundary": False,
            "deliverable_contract": None,
            "request_id": "request-mcp-echo-unchanged-contract",
        },
        _make_request_context(session_id="session-lead"),
    )
    assert echoed_noop["success"] is True, echoed_noop.get("errors")
    ordinary_task = repo.read().tasks["ordinary-after-submission"]
    assert ordinary_task.description == "Description edited after submission."
    assert ordinary_task.integration_boundary is False
    assert ordinary_task.deliverable_contract is None

    created = _call_tool_with_context(
        server,
        TEAM_CREATE_TASK_TOOL,
        {
            "task_id": "final-integration",
            "owner_agent_id": "lead",
            "project_id": "project",
            "priority": 1,
            "title": "Final integration",
            "description": "Compose the accepted implementation.",
            "dependency_task_ids": ["implementation"],
            "integration_boundary": True,
            "deliverable_contract": {
                "require_git_provenance": True,
                "required_contribution_task_ids": ["implementation"],
            },
            "request_id": "request-mcp-create-integration-boundary",
        },
        _make_request_context(session_id="session-lead"),
    )
    assert created["success"] is True, created
    assert repo.read().tasks["final-integration"].integration_boundary is True


def test_team_addon_mcp_slice6_tool_requires_active_lease_without_mutation(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-manager",
        allowed_tools=[TEAM_CREATE_CASE_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool_with_context(
            server,
            TEAM_CREATE_CASE_TOOL,
            {"title": "Denied case", "request_id": "request-denied-case"},
            _make_request_context(session_id="session-manager"),
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.cases == before.cases


def test_team_addon_mcp_slice8_git_intent_tools_smoke(tmp_path: Path) -> None:
    tools = [
        TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
        TEAM_UPDATE_FEATURE_BRANCH_TOOL,
        TEAM_REQUEST_RELEASE_TOOL,
        TEAM_RECOVER_RELEASE_TOOL,
    ]
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-backend", allowed_tools=tools)
    _upsert_session(context, session_id="session-lead", allowed_tools=tools)
    repo = _seed_slice8_state(context.config.runtime_dir)
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend-slice8")
    _lease_agent(repo, agent_id="lead", dispatch_id="dispatch-lead-slice8")
    server = create_server(context)

    review_result = _call_tool_with_context(
        server,
        TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
        {
            "deliverable_id": "deliverable",
            "source_branch": "agents/session-backend/work",
            "request_id": "request-mcp-review",
        },
        _make_request_context(session_id="session-backend"),
    )
    feature_result = _call_tool_with_context(
        server,
        TEAM_UPDATE_FEATURE_BRANCH_TOOL,
        {
            "task_id": "task",
            "source_review_branches": [review_result["target_review_branch"]],
            "request_id": "request-mcp-feature",
        },
        _make_request_context(session_id="session-backend"),
    )
    _team_admin_service(repo).update_task(
        task_id="task",
        state=TeamTaskState.FINISHED,
    )
    release_result = _call_tool_with_context(
        server,
        TEAM_REQUEST_RELEASE_TOOL,
        {
            "id": "rel-1",
            "project_id": "project",
            "deliverable_ids": ["deliverable"],
            "request_id": "request-mcp-release",
        },
        _make_request_context(session_id="session-lead"),
    )
    assert release_result["success"] is True
    _team_git_release_service(repo).record_git_promotion_result(
        promotion_id=release_result["promotion_event_ids"][0],
        success=False,
        error="merge conflict while merging feature",
    )
    recovery_result = _call_tool_with_context(
        server,
        TEAM_RECOVER_RELEASE_TOOL,
        {
            "release_package_id": "rel-1",
            "agent_branch": "refs/heads/agents/lead/release-resolved",
            "commit_sha": "708b1b89daaa9f47f7bb5f7a96d6935cf4b45932",
            "request_id": "request-mcp-recovery",
        },
        _make_request_context(session_id="session-lead"),
    )

    assert review_result["success"] is True
    assert review_result["intent_recorded"] is True
    assert review_result["async_effect_pending"] is True
    assert review_result["target_review_branch"] == "team/team/reviews/deliverable"
    assert feature_result["success"] is True
    assert feature_result["target_feature_branch"] == "team/team/features/task-backend"
    assert release_result["success"] is True
    assert release_result["release_package_id"] == "rel-1"
    assert len(release_result["promotion_event_ids"]) == 1
    assert recovery_result["success"] is True
    assert recovery_result["release_package_id"] == "rel-1"
    assert recovery_result["promotion_id"] == release_result["promotion_event_ids"][0]
    state = repo.read()
    assert state.agent_runtimes["backend"].active_dispatch_id == "dispatch-backend-slice8"
    assert state.agent_runtimes["lead"].active_dispatch_id == "dispatch-lead-slice8"
    assert len(repo.list_outbox_records()) == 5


def test_team_addon_mcp_slice8_tool_requires_active_dispatch_without_mutation(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-lead",
        allowed_tools=[TEAM_REQUEST_RELEASE_TOOL],
    )
    repo = _seed_slice8_state(context.config.runtime_dir)
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool_with_context(
            server,
            TEAM_REQUEST_RELEASE_TOOL,
            {
                "project_id": "project",
                "deliverable_ids": ["deliverable"],
                "request_id": "request-no-lease-release",
            },
            _make_request_context(session_id="session-lead"),
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.release_packages == before.release_packages
    assert after.git_promotions == before.git_promotions


def test_team_addon_mcp_records_audited_initial_release_decision(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-lead",
        allowed_tools=[TEAM_DECIDE_INITIAL_RELEASE_TOOL],
    )
    repo = _seed_slice8_state(context.config.runtime_dir)

    def _track_initial_release(state):
        state.deliverables[
            "deliverable"
        ].initial_release_disposition = DeliverableInitialReleaseDisposition()
        return state

    repo.update(_track_initial_release)
    _lease_agent(repo, agent_id="lead", dispatch_id="dispatch-initial-release")
    server = create_server(context)

    result = _call_tool_with_context(
        server,
        TEAM_DECIDE_INITIAL_RELEASE_TOOL,
        {
            "deliverable_ids": ["deliverable"],
            "decision": "not_planned",
            "reason": "This accepted result remains internal-only.",
            "request_id": "request-mcp-initial-release",
        },
        _make_request_context(session_id="session-lead"),
    )

    assert result["success"] is True
    disposition = repo.read().deliverables["deliverable"].initial_release_disposition
    assert disposition is not None
    assert disposition.state == InitialReleaseDispositionState.NOT_PLANNED
    assert disposition.decided_by_actor == "agent:lead"
    assert disposition.reason == "This accepted result remains internal-only."


def test_team_addon_mcp_slice9_tools_smoke_and_worker_visibility(
    tmp_path: Path,
) -> None:
    persistent_tools = [
        TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
        TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    ]
    context = _runtime(tmp_path, allowed_tools=[])
    directories = DirectoryService(context.directories)
    directories.create(parent="root", name="teams")
    directories.create(parent="root/teams", name="alpha")
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=persistent_tools,
        directory_path="root/teams/alpha",
    )
    repo = _seed_planning_state(context.config.runtime_dir)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Task",
        description="Implement the task.",
        created_by_agent_id="lead",
    )

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable_ephemeral)
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend-slice9")
    server = create_server(context)

    requested = _call_tool_with_context(
        server,
        TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
        {
            "id": "ephemeral-1",
            "task_id": "task",
            "worker_session_id": "session-ephemeral",
            "title": "Worker",
            "prompt": "Implement the scoped task.",
            "allowed_tools": [TEAM_CREATE_TASK_TOOL],
            "request_id": "request-ephemeral-start",
        },
        _make_request_context(session_id="session-backend"),
    )
    _upsert_session(
        context,
        session_id="session-ephemeral",
        allowed_tools=requested["events"][0]["payload"]["allowed_tools"],
        directory_path=requested["events"][0]["payload"]["directory_path"],
    )
    worker_session = context.policy.get_session("session-ephemeral")
    visible = {
        tool_name_value(item.tool)
        for item in context.policy.visible_tools_for_session(worker_session)
    }

    reported = _call_tool_with_context(
        server,
        TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
        {
            "summary": "Worker result is ready.",
            "artifact_refs": ["artifact-space:/teams/team/ephemeral/result"],
            "agent_branch": "agents/session-ephemeral/ephemeral-1",
            "request_id": "request-ephemeral-result",
        },
        _make_request_context(session_id="session-ephemeral"),
    )
    accepted = _call_tool_with_context(
        server,
        TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
        {
            "assignment_id": "ephemeral-1",
            "deliverable_id": "deliverable-ephemeral",
            "request_id": "request-ephemeral-accept",
        },
        _make_request_context(session_id="session-backend"),
    )

    assert requested["success"] is True
    assert requested["intent_recorded"] is True
    assert requested["async_effect_pending"] is True
    assert requested["assignment_id"] == "ephemeral-1"
    assert requested["worker_session_id"] == "session-ephemeral"
    assert requested["events"][0]["payload"]["directory_path"] == "root/teams/alpha"
    assert requested["events"][0]["payload"]["allowed_tools"] == [TEAM_REPORT_EPHEMERAL_RESULT_TOOL]
    assert visible == {TEAM_REPORT_EPHEMERAL_RESULT_TOOL}
    assert reported["success"] is True
    assert reported["assignment_id"] == "ephemeral-1"
    assert reported["state"] == "completed"
    assert accepted["success"] is True
    final = repo.read()
    assert final.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert final.deliverables["deliverable-ephemeral"].producer_agent_id == "backend"
    assert final.agent_runtimes["backend"].active_dispatch_id == "dispatch-backend-slice9"


def test_team_addon_mcp_slice9_request_requires_active_dispatch_without_mutation(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(
        context,
        session_id="session-backend",
        allowed_tools=[TEAM_REQUEST_EPHEMERAL_AGENT_TOOL],
    )
    repo = _seed_planning_state(context.config.runtime_dir)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Task",
        description="Implement the task.",
        created_by_agent_id="lead",
    )
    before = repo.read()
    server = create_server(context)

    with pytest.raises(ToolError, match="active Team dispatch lease"):
        _call_tool_with_context(
            server,
            TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
            {
                "task_id": "task",
                "title": "Denied worker",
                "prompt": "This should not start.",
                "request_id": "request-ephemeral-no-lease",
            },
            _make_request_context(session_id="session-backend"),
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.ephemeral_assignments == before.ephemeral_assignments


def test_team_addon_mcp_slice9_worker_assignment_mismatch_denied(
    tmp_path: Path,
) -> None:
    tools = [TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL]
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-backend", allowed_tools=tools)
    repo = _seed_planning_state(context.config.runtime_dir)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Task",
        description="Implement the task.",
        created_by_agent_id="lead",
    )

    def _enable_ephemeral(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=2)
        return state

    repo.update(_enable_ephemeral)
    _lease_agent(repo, agent_id="backend", dispatch_id="dispatch-backend-slice9")
    server = create_server(context)

    requested = _call_tool_with_context(
        server,
        TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
        {
            "assignments": [
                {
                    "id": "ephemeral-1",
                    "task_id": "task",
                    "worker_session_id": "session-ephemeral-1",
                    "title": "Worker 1",
                    "prompt": "Do the first part.",
                },
                {
                    "id": "ephemeral-2",
                    "task_id": "task",
                    "worker_session_id": "session-ephemeral-2",
                    "title": "Worker 2",
                    "prompt": "Do the second part.",
                },
            ],
            "request_id": "request-ephemeral-batch",
        },
        _make_request_context(session_id="session-backend"),
    )
    _upsert_session(
        context,
        session_id="session-ephemeral-1",
        allowed_tools=[TEAM_REPORT_EPHEMERAL_RESULT_TOOL],
    )
    _upsert_session(
        context,
        session_id="session-ephemeral-2",
        allowed_tools=[TEAM_REPORT_EPHEMERAL_RESULT_TOOL],
    )
    before = repo.read()

    assert requested["success"] is True
    with pytest.raises(ToolError, match="assignment_id is bound"):
        _call_tool_with_context(
            server,
            TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
            {
                "assignment_id": "ephemeral-1",
                "summary": "Wrong worker result.",
                "request_id": "request-wrong-assignment",
            },
            _make_request_context(session_id="session-ephemeral-2"),
        )

    after = repo.read()
    assert after.revision == before.revision
    assert after.ephemeral_assignments["ephemeral-1"].state.value == "requested"
    assert after.ephemeral_assignments["ephemeral-2"].state.value == "requested"


def test_team_addon_mcp_marks_governance_agenda_handled(tmp_path: Path) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        {
            "session_id": "session-governance",
            "agenda_item_id": "agenda",
        },
    )

    assert result["success"] is True
    assert result["agenda_item_id"] == "agenda"
    assert result["governance_group_id"] == "portfolio"
    assert result["state"] == "handled"
    state = repo.read()
    assert state.governance_agenda["agenda"].state == GovernanceAgendaItemState.HANDLED


def test_team_addon_mcp_rejects_agenda_handling_outside_governance_scope(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="unmanaged-team",
        name="Unmanaged Team",
        manager_agent_id="unmanaged-manager",
        manager_session_id="session-unmanaged-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="other-governance-manager",
        session_id="session-other-governance",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="other-portfolio",
        name="Other Portfolio",
        manager_agent_id="other-governance-manager",
        member_team_ids=["unmanaged-team"],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="outside-agenda",
        governance_group_id="other-portfolio",
        item_type="operator_directive",
        title="Review other status",
        summary="Check other status.",
        team_id="unmanaged-team",
        expected_revision=repo.read().revision,
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="active governance manager session"):
        _call_tool(
            server,
            TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
            {
                "session_id": "session-governance",
                "agenda_item_id": "outside-agenda",
            },
        )


def test_team_addon_mcp_agenda_handling_reports_revision_conflict(
    tmp_path: Path,
) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL],
    )
    repo = _seed_governance_state(context.config.runtime_dir)
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        {
            "session_id": "session-governance",
            "agenda_item_id": "agenda",
            "expected_revision": 0,
        },
    )

    assert result["success"] is False
    assert result["agenda_item_id"] == "agenda"
    assert result["expected_revision"] == 0
    assert result["current_revision"] == repo.read().revision


def test_team_addon_mcp_rejects_directives_outside_governance_scope(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL])
    _seed_governance_state(context.config.runtime_dir)
    server = create_server(context)

    result = _call_tool(
        server,
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        {
            "session_id": "session-governance",
            "team_id": "other-team",
            "message": "This team is outside the governance group.",
            "request_id": "request-directive-outside-scope",
        },
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] in {
        "team_not_in_governance_group",
        "governance_group_required",
    }
