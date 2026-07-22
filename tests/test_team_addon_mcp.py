from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonLoaderConfig
from evander.config import GatewayConfig
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.events.scheduled import scheduled_internal_event_store_path
from evander.gateway import create_server
from evander.models import ApprovalStatus, SessionManifest, ToolName, tool_name_value
from evander.runtime import build_runtime
from evander_team_addon.mcp import (
    MCP_TOOL_NAMES,
    TEAM_CREATE_CASE_TOOL,
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_CREATE_TASKS_TOOL,
    TEAM_CREATE_TASK_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    TEAM_PUBLISH_REVIEW_BRANCH_TOOL,
    TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    TEAM_RECORD_NOTE_TOOL,
    TEAM_RECOVER_RELEASE_TOOL,
    TEAM_REPORT_EPHEMERAL_RESULT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
    TEAM_REQUEST_EPHEMERAL_AGENTS_TOOL,
    TEAM_REQUEST_RELEASE_TOOL,
    TEAM_RETRY_RELEASE_GATE_TOOL,
    TEAM_REVOKE_STAND_DOWN_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    TEAM_STAND_DOWN_AGENT_TOOL,
    TEAM_SUBMIT_DELIVERABLE_TOOL,
    TEAM_UPDATE_CASE_TOOL,
    TEAM_UPDATE_FEATURE_BRANCH_TOOL,
    TEAM_UPDATE_GOAL_TOOL,
    TEAM_UPDATE_TASK_TOOL,
)
from evander_team_addon.chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from evander_team_addon.models import (
    AgentRuntime,
    DeliverableReviewManifest,
    DeliverableVerification,
    EphemeralAgentPolicy,
    GovernanceAgendaItemState,
    ProjectGitConfig,
    ProjectGoal,
    ProjectReleasePolicy,
    ReviewRequest,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import (
    ADDON_ID,
    TeamStateRepository,
)
from evander_team_addon.services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.commands.chat_commands import TeamChatCommandService
from evander_team_addon.services.git_release_service import TeamGitReleaseService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_git_release_service(repo: TeamStateRepository) -> TeamGitReleaseService:
    return TeamGitReleaseService(repo)


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


def _upsert_session(context, *, session_id: str, allowed_tools: list[str]) -> None:
    context.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
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
            state.agent_runtimes[agent_id] = AgentRuntime(
                agent_id=agent_id,
                active_dispatch_id=f"dispatch-{agent_id}",
                wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
                wake_reason="scheduled",
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
        )
        project.release_policy = ProjectReleasePolicy(allow_ungated=True)
        deliverable = state.deliverables["deliverable"]
        deliverable.verification = DeliverableVerification(status="passed")
        refs = deliverable.git_refs
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
        state.agent_runtimes[agent_id] = AgentRuntime(
            agent_id=agent_id,
            active_dispatch_id=dispatch_id,
            wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
            wake_reason="scheduled",
        )
        return state

    repo.update(_lease)


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
    publish_description = tools[TEAM_PUBLISH_REVIEW_BRANCH_TOOL].description or ""
    retry_release_description = tools[TEAM_RETRY_RELEASE_GATE_TOOL].description or ""
    assert "verification records status" in submit_description
    assert "git_refs are provenance only" in submit_description
    assert "not required merely because team_submit_deliverable included git_refs" in (
        publish_description
    )
    assert "infrastructure-failed or timed-out verification" in retry_release_description
    assert "process-blocked reviews" in retry_release_description


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


def test_teamless_governance_agent_cannot_reply_outside_active_team_chat(
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
    before = repo.read()
    server = create_server(context)

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

    def _lease_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            active_dispatch_id="dispatch-mcp-note",
            wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
            wake_reason="scheduled",
        )
        return state

    repo.update(_lease_manager)
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
) -> None:
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
                },
                {
                    "task_id": "task-mcp-batch-b",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Batch B",
                    "description": "Second batch task.",
                    "dependency_task_ids": ["task-mcp-batch-a"],
                },
            ],
            "request_id": "request-mcp-task-batch",
        },
        _make_request_context(session_id="session-lead"),
    )
    updated_task = _call_tool_with_context(
        server,
        TEAM_UPDATE_TASK_TOOL,
        {
            "task_id": "task-mcp",
            "state": "in_progress",
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
    scheduled = json.loads(
        scheduled_internal_event_store_path(context.config.runtime_dir).read_text(
            encoding="utf-8",
        )
    )
    scheduled_wakes = [
        record
        for record in scheduled.values()
        if record["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
        and record["payload"]["agent_id"] == "backend"
    ]
    assert scheduled_wakes
    assert all(record["status"] == "pending" for record in scheduled_wakes)
    assert created_tasks["created_task_ids"] == [
        "task-mcp-batch-a",
        "task-mcp-batch-b",
    ]
    assert updated_task["updated_task_ids"] == ["task-mcp"]
    assert updated_goal["updated_goal_ids"] == ["goal"]
    assert stood_down["created_stand_down_ids"]
    assert revoked["revoked_stand_down_ids"] == stood_down["created_stand_down_ids"]
    state = repo.read()
    assert state.cases["case-mcp"].title == "MCP case updated"
    assert state.tasks["task-mcp"].state.value == "in_progress"
    assert state.projects["project"].goals[0].state == TeamRecordState.IN_PROGRESS


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
    assert len(repo.list_outbox_records()) == 4


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


def test_team_addon_mcp_slice9_tools_smoke_and_worker_visibility(
    tmp_path: Path,
) -> None:
    persistent_tools = [
        TEAM_REQUEST_EPHEMERAL_AGENT_TOOL,
        TEAM_ACCEPT_EPHEMERAL_RESULT_TOOL,
    ]
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-backend", allowed_tools=persistent_tools)
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
