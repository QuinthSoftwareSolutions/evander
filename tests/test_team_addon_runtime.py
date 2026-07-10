from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.composition import build_admin_runtime
from evander.config import GatewayConfig
from evander.artifact_spaces import ArtifactSpaceStore
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.events.scheduled import scheduled_internal_event_store_path
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.model_usage.store import ModelUsageStore
from evander.published_artifacts import PublishedArtifactStore
from evander.queries.providers.base import QueryRuntimeContext
from evander.workflow.packs import WorkflowPack
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from evander_team_addon.mcp import (
    GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    ORDINARY_TEAM_MCP_TOOL_NAMES,
    READ_TEAM_MCP_TOOL_NAMES,
    TEAM_CREATE_CASE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
)
from evander_team_addon.models import (
    AgentActionRecord,
    AgentAvatar,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentProjectAssignment,
    AgentRuntime,
    AgentStatus,
    AgentTask,
    AgentVoiceProfile,
    Deliverable,
    EphemeralAgentPolicy,
    EphemeralAssignment,
    GitWorkContext,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamCase,
    TeamCaseStatus,
    TeamConversation,
    TeamConversationMessage,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.provider_schemas import (
    TeamCreateCaseEffectDefinition,
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamCreateTaskEffectDefinition,
    TeamFinalizeAgentDispatchEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamStateQueryDefinition,
    TeamUpdateCaseEffectDefinition,
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeGovernanceManagersEffectDefinition,
)
from evander_team_addon.providers import (
    LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS,
    TEAM_AGENT_GUIDANCE_PATH,
    TEAM_AGENT_GUIDANCE_SECTION_ID,
    TEAM_AGENT_GUIDANCE_VERSION,
    TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
    TEAM_GOVERNANCE_GUIDANCE_VERSION,
    TeamCreateCaseEffectProvider,
    TeamCreateOperatorDirectiveEffectProvider,
    TeamCreateTaskEffectProvider,
    TeamFinalizeAgentDispatchEffectProvider,
    TeamAgentPromptCompletionFinalizeHook,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamSessionRoutingQueryProvider,
    TeamStateQueryProvider,
    TeamUpdateCaseEffectProvider,
    TeamWakeDueAgentsEffectProvider,
    TeamWakeGovernanceManagersEffectProvider,
    _build_prompt,
    _publish_deliverable_artifact_bundle,
    _publish_team_events,
)
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from evander_team_addon.services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.ephemeral_service import TeamEphemeralService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.services.commands import TeamEphemeralCommandService
from evander_team_addon.workflow_packs import team_runtime_v1

ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES = (
    *ORDINARY_TEAM_MCP_TOOL_NAMES,
    *READ_TEAM_MCP_TOOL_NAMES,
)


def _command_service(repo: TeamStateRepository) -> TeamEphemeralCommandService:
    return TeamEphemeralCommandService(repo)


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_chat_service(repo: TeamStateRepository) -> TeamChatService:
    return TeamChatService(repo)


def _team_runtime_service(repo: TeamStateRepository) -> TeamRuntimeService:
    return TeamRuntimeService(repo)


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo)


def _team_ephemeral_service(repo: TeamStateRepository) -> TeamEphemeralService:
    return TeamEphemeralService(repo)


def _team_runtime_sections() -> dict[str, list[dict[str, object]]]:
    pack = WorkflowPack.model_validate(team_runtime_v1())
    sections: dict[str, list[dict[str, object]]] = {
        "triggers": [],
        "gates": [],
        "queries": [],
        "effects": [],
        "edges": [edge.model_dump(mode="json") for edge in pack.ir.edges],
    }
    for ir_node in pack.ir.nodes:
        node = ir_node.node.model_dump(mode="json")
        config = node.pop("config", {}) or {}
        node.update(config)
        node_type = str(node["type"])
        if node_type.startswith("trigger:"):
            sections["triggers"].append(node)
        elif node_type.startswith("gate:"):
            sections["gates"].append(node)
        elif node_type.startswith("query:"):
            sections["queries"].append(node)
        elif node_type.startswith("effect:"):
            sections["effects"].append(node)
    return sections


def _flatten_workflow_config_for_editor(
    config: dict[str, object],
) -> dict[str, object]:
    flattened = json.loads(json.dumps(config))
    for collection_name in ("triggers", "gates", "queries", "effects"):
        entries = flattened.get(collection_name)
        if not isinstance(entries, list):
            continue
        next_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                next_entries.append(entry)
                continue
            nested_config = entry.pop("config", None)
            if isinstance(nested_config, dict):
                entry = {**nested_config, **entry}
            next_entries.append(entry)
        flattened[collection_name] = next_entries
    return flattened


def _legacy_outputs_config(config: dict[str, object]) -> dict[str, object]:
    legacy = json.loads(json.dumps(config))
    effects = legacy.get("effects")
    if not isinstance(effects, list):
        return legacy
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        if effect.get("type") not in {
            "effect:team:record-git-promotion",
            "effect:team:mark-release-published",
        }:
            continue
        if "result_outputs" in effect and "outputs" not in effect:
            effect["outputs"] = effect.pop("result_outputs")
    return legacy


def test_team_runtime_pack_loads_as_current_workflow_pack(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True)
    (packs_dir / "team-runtime.v1.json").write_text(
        json.dumps(team_runtime_v1(), indent=2),
        encoding="utf-8",
    )
    config_dir = tmp_path / "config" / "trigger-orchestrator"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["team-runtime.v1.json"],
                "addon_loader": {
                    "modules": ["evander_team_addon"],
                    "enabled_entry_points": False,
                    "enabled_addon_ids": ["team-addon"],
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        tmp_path,
        base_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
    )

    assert _node_ids(config.triggers) == [
        "team-cron",
        "team-agent-run-completed",
        "team-chat-message-queued",
        "team-agent-wake-requested",
        "team-ephemeral-requested",
        "team-operator-escalation",
        "team-git-promotion-requested",
        "team-git-release-recovery-requested",
        "system.chat_assistant",
    ]
    assert "team-wake-due-agents" in _node_ids(config.effects)
    assert "team-wake-queued-chat-message" in _node_ids(config.effects)
    assert "team-notify-operator" in _node_ids(config.effects)
    assert "team-git-promote-review-branch" in _node_ids(config.effects)
    assert "team-promote-release-recovery-branch" in _node_ids(config.effects)

    service = build_admin_runtime(tmp_path / "runtime")
    document = service.workflow_config_admin.read_workflow_editor_document()
    assert service.workflow_config_admin.read_workflow_editor_document().revision == document.revision
    assert document.output_schemas["team-route-completed-session"]["persistent"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-route-completed-session"]["ignored"] == {"type": "boolean"}
    assert "team-read-persistent-action" not in document.output_schemas
    assert document.output_schemas["team-finalize-persistent-dispatch"]["finalized"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-finalize-persistent-dispatch"]["progress_made"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-wake-due-agents"]["prepared_project_repos"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["failed_project_repo_preparations"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["transient_conflicts"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["retry_event_ids"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["retry_scheduled_count"] == {
        "type": "integer"
    }
    assert document.output_schemas["team-wake-due-agents"]["git_work_contexts"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["review_artifact_imports"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["review_artifact_import_errors"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["cost_guard_blocked_team_ids"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["cost_guard_blocked_teams"] == {
        "type": "array"
    }
    assert document.output_schemas["team-consolidate-feature-branches"]["commit_sha"] == {
        "type": ["string", "null"]
    }
    graphs = [
        graph.model_dump(mode="json") if hasattr(graph, "model_dump") else graph
        for graph in document.graphs
    ]
    flattened_config = _flatten_workflow_config_for_editor(document.config)
    effects = {
        str(effect["id"]): effect
        for effect in flattened_config.get("effects", [])
        if isinstance(effect, dict)
    }
    assert effects["team-wake-due-agents"]["low_priority_wake_after_cycles"] == 4
    assert effects["team-wake-due-agents"].get("respect_wake_interval", True) is True
    assert "outputs" not in effects["team-record-review-promotion"]
    assert effects["team-record-review-promotion"]["result_outputs"] == {
        "review_branch": "${{team-git-promote-review-branch.review_branch ?? null}}",
        "host_repo_path": "${{team-git-promote-review-branch.host_repo_path ?? null}}",
        "message": "${{team-git-promote-review-branch.message ?? null}}",
    }
    validation = service.workflow_config_admin.validate_workflow_editor_document(
        config=flattened_config,
        layout=document.layout,
        graphs=graphs,
    )
    assert validation.validation_diagnostics == []
    legacy_validation = service.workflow_config_admin.validate_workflow_editor_document(
        config=_legacy_outputs_config(flattened_config),
        layout=document.layout,
        graphs=graphs,
    )
    assert legacy_validation.validation_diagnostics == []
    current_document = service.workflow_config_admin.read_workflow_editor_document()
    save = service.workflow_config_admin.save_workflow_editor_document(
        config=flattened_config,
        layout=document.layout,
        graphs=graphs,
        expected_revision=current_document.revision,
    )
    assert save.validation_diagnostics == []


def _node_ids(nodes: object) -> list[str]:
    return [str(getattr(node, "id")) for node in nodes]


class FakeResumer:
    def __init__(
        self,
        result: dict[str, object] | None = None,
        on_resume: object | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or {"thread_id": "thread-1"}
        self.on_resume = on_resume

    def resume_agent(self, **kwargs: object) -> dict[str, object]:
        if callable(self.on_resume):
            self.on_resume(dict(kwargs))
        self.calls.append(dict(kwargs))
        return dict(self.result)


class FakeMetadataUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"success": True}


class FakeSessionStore(FakeMetadataUpdater):
    def __init__(self) -> None:
        super().__init__()
        self.sessions = {
            "session-backend": SimpleNamespace(
                session_id="session-backend",
                vm_id="vm-backend",
                vm_provider="libvirt",
                directory_path="root",
                metadata={},
            )
        }

    def read_all_sessions(self) -> dict[str, object]:
        return self.sessions

    def get_session(self, session_id: str) -> object | None:
        return self.sessions.get(session_id)

    def upsert_session(self, session: object) -> object:
        self.sessions[str(getattr(session, "session_id"))] = session
        return session

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        super().session_update_metadata(**kwargs)
        target = str(kwargs["target"])
        metadata = kwargs["metadata"]
        session = self.sessions[target]
        session.metadata = _deep_merge(dict(session.metadata), metadata)
        return {"success": True, "metadata": session.metadata}


class FakeExecutionStore:
    def __init__(self, records: list[object]) -> None:
        self.records = records

    def list_executions(self) -> list[object]:
        return list(self.records)


class FakeFileCopier:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def vm_copy_files(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        files = list(kwargs.get("files") or [])
        return {
            "success": True,
            "vm_name": kwargs.get("target"),
            "copied_count": len(files),
            "copied_paths": [file["destination_path"] for file in files],
        }


class FakeRepoCloner:
    def __init__(
        self,
        *,
        success: bool = True,
        message: str = "repo prepared",
        on_clone: object | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.success = success
        self.message = message
        self.on_clone = on_clone

    def clone_repo_into_vm(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        if callable(self.on_clone):
            self.on_clone()
        return SimpleNamespace(
            success=self.success,
            vm_name=str(kwargs.get("vm_name") or "vm-backend"),
            message=self.message,
            target_dir="/home/agent/Truman",
            guest_repo_name="Truman",
            remote_url="ssh://evander-git-host/git-remotes/Truman.git",
        )


class FakeArtifactReader:
    def __init__(self, content: str) -> None:
        self.content = content

    def read_session_artifact_text(self, **_: object) -> dict[str, object]:
        return {"content": self.content}


def _deep_merge(base: dict[str, object], update: object) -> dict[str, object]:
    if not isinstance(update, dict):
        return base
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _team_context_from_prompt(prompt: str) -> dict[str, object]:
    return json.loads(
        prompt.split("Team context JSON:\n", 1)[1].split(
            "\n\nMCP action surface",
            1,
        )[0]
    )


def _seed_team(runtime_dir: Path, *, max_ephemeral: int = 0) -> TeamStateRepository:
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
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=max_ephemeral),
        expected_revision=3,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=4,
    )
    return repo


def _seed_git_work_context(
    repo: TeamStateRepository,
    *,
    context_id: str = "ctx-task",
    agent_branch: str = "agents/session-backend/review",
    commit_sha: str | None = "abc123",
    dispatch_id: str | None = None,
) -> None:
    def _mutate(state: TeamState) -> TeamState:
        state.git_work_contexts[context_id] = GitWorkContext(
            id=context_id,
            agent_id="backend",
            task_id="task",
            base_branch="main",
            base_commit="base123",
            agent_branch=agent_branch,
            commit_sha=commit_sha,
            dispatch_id=dispatch_id,
        )
        return state

    repo.update(_mutate)


def test_agent_action_uses_active_dispatch_for_git_context_binding(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _seed_git_work_context(
        repo,
        context_id="ctx-other-dispatch",
        agent_branch="agents/session-backend/work-session-backend",
        commit_sha=None,
        dispatch_id="dispatch-other",
    )

    def _set_runtime(state: TeamState) -> TeamState:
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id="dispatch-active",
        )
        return state

    repo.update(_set_runtime)

    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-wrong-context",
                    "task_id": "task",
                    "summary": "Ready.",
                    "branch_context_id": "ctx-other-dispatch",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert "belongs to dispatch dispatch-other, not dispatch-active" in result["errors"][0]
    assert "deliverable-wrong-context" not in state.deliverables


def _add_luke_reviewer(
    repo: TeamStateRepository,
    *,
    with_review_profile: bool = True,
) -> None:
    _team_admin_service(repo).create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        description="Review deliverables; do not own implementation work.",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="luke",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-luke",
        display_name="Luke",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    if with_review_profile:
        _team_admin_service(repo).create_review_profile(
            profile_id="project-review-profile",
            team_id="team",
            project_id="project",
            name="Project review",
            description="Default review checks for project deliverables.",
            applicable_role_ids=["project__reviewer"],
            expected_revision=repo.read().revision,
        )


def _seed_manager_review_stall(
    repo: TeamStateRepository,
    *,
    deliverable_id: str = "deliverable-stalled",
    review_id: str = "review-manager",
) -> None:
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": deliverable_id,
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": review_id,
                    "deliverable_id": deliverable_id,
                    "reviewer_agent_id": "manager",
                }
            ],
        },
    )
    assert result["success"] is True

    def _handle_all_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_all_inbox)


def _lease_manager_fallback(
    repo: TeamStateRepository,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=now or datetime(2026, 1, 1, tzinfo=UTC)
    )
    return next(lease for lease in leases if lease["agent"]["id"] == "manager")


def _set_manager_fallback_runtime(repo: TeamStateRepository) -> str:
    dispatch_id = "team-dispatch-manager-fallback"

    def _mark_manager_fallback(state: TeamState) -> TeamState:
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            active_dispatch_id=dispatch_id,
            wake_reason="manager_fallback",
            wake_leased_until=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        )
        return state

    repo.update(_mark_manager_fallback)
    return dispatch_id


def _add_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str,
    state: TeamTaskState = TeamTaskState.PENDING,
) -> None:
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=1,
        title=f"Task {task_id}",
        description="Work to deliver.",
        created_by_agent_id="lead",
        state=state,
    )


def _set_task_graph(
    repo: TeamStateRepository,
    *,
    task_id: str,
    parent_task_id: str | None = None,
    dependency_task_ids: list[str] | None = None,
) -> None:
    def _mutate(state: TeamState) -> TeamState:
        task = state.tasks[task_id]
        if parent_task_id is not None:
            task.parent_task_id = parent_task_id
        if dependency_task_ids is not None:
            task.dependency_task_ids = list(dependency_task_ids)
        return state

    repo.update(_mutate)


def _add_pending_inbox(
    repo: TeamStateRepository,
    *,
    item_id: str,
    recipient_agent_id: str,
) -> None:
    def _mutate(state: TeamState) -> TeamState:
        state.inbox[item_id] = AgentInboxItem(
            id=item_id,
            recipient_agent_id=recipient_agent_id,
            item_type=TeamInboxItemType.STATUS_REQUEST,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_mutate)


def _add_action_record(
    repo: TeamStateRepository,
    *,
    action_id: str,
    dispatch_id: str,
    created_at: datetime,
    success: bool,
    error: str | None = None,
    action: dict[str, object] | None = None,
    feedback_delivered_at: datetime | None = None,
) -> None:
    def _mutate(state: TeamState) -> TeamState:
        state.action_records[action_id] = AgentActionRecord(
            id=action_id,
            agent_id="backend",
            dispatch_id=dispatch_id,
            action=action or {},
            success=success,
            error=error,
            created_at=created_at,
            feedback_delivered_at=feedback_delivered_at,
        )
        return state

    repo.update(_mutate)


def _leased_agent_ids(leases: list[dict[str, object]]) -> list[str]:
    return [str(lease["agent"]["id"]) for lease in leases]


def test_repository_leases_due_agents_and_applies_action(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert len(leases) == 1
    assert leases[0]["agent"]["id"] == "backend"
    dispatch_id = leases[0]["dispatch_id"]
    assert state.agent_runtimes["backend"].active_dispatch_id == dispatch_id

    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "task_updates": [{"task_id": "task", "state": "in_progress"}],
            "questions": [{"task_id": "task", "question": "Need API contract?"}],
        },
    )

    assert result["success"] is True
    assert state.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.agent_runtimes["backend"].no_progress_count == 0
    assert len(state.inbox) == 1
    assert next(iter(state.inbox.values())).recipient_agent_id == "lead"


def test_manager_fallback_not_leased_while_team_member_is_awake(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _mark_backend_awake(state: TeamState) -> TeamState:
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id="team-dispatch-backend",
            wake_reason="scheduled",
            wake_leased_until=now + timedelta(minutes=15),
            next_wake_at=now + timedelta(minutes=15),
        )
        return state

    repo.update(_mark_backend_awake)

    state, leases = _team_runtime_service(repo).lease_due_agents(now=now)

    assert leases == []
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-dispatch-backend"
    manager_runtime = state.agent_runtimes.get("manager")
    assert manager_runtime is None or manager_runtime.active_dispatch_id is None
    assert manager_runtime is None or manager_runtime.wake_reason is None

    scoped_state, scoped_leases = _team_runtime_service(repo).lease_due_agents(
        now=now, agent_id="manager"
    )

    assert scoped_leases == []
    manager_runtime = scoped_state.agent_runtimes.get("manager")
    assert manager_runtime is None or manager_runtime.active_dispatch_id is None
    assert manager_runtime is None or manager_runtime.wake_reason is None


def test_dependency_waiting_owner_not_leased(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    # A pending dependency owned by another agent keeps "task" waiting.
    _add_task(repo, task_id="dep", owner_agent_id="lead")
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert "backend" not in _leased_agent_ids(leases)


def test_dependency_waiting_owner_leased_with_pending_inbox(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _add_task(repo, task_id="dep", owner_agent_id="lead")
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])
    _add_pending_inbox(repo, item_id="inbox-1", recipient_agent_id="backend")

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["reason"] == "inbox"


def test_owner_leaseable_after_dependencies_complete(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _add_task(repo, task_id="dep", owner_agent_id="lead", state=TeamTaskState.ACCEPTED)
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert "backend" in _leased_agent_ids(leases)


def test_parent_owner_with_open_child_remains_leaseable_while_waiting(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    # "task" (owned by backend) waits on a pending dependency owned by lead...
    _add_task(repo, task_id="blocker", owner_agent_id="lead")
    _set_task_graph(repo, task_id="task", dependency_task_ids=["blocker"])
    # ...but it has an open child task owned by another agent.
    _add_task(repo, task_id="child", owner_agent_id="lead")
    _set_task_graph(repo, task_id="child", parent_task_id="task")

    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert state.dependency_status("task") == "waiting"
    assert state.subtask_counts("task")["open"] == 1
    assert "backend" in _leased_agent_ids(leases)


def test_lease_payload_tasks_include_derived_graph_fields(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_case(
        case_id="case",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        task_ids=["task"],
        expected_revision=repo.read().revision,
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    task_payload = backend_lease["tasks"][0]
    assert backend_lease["cases"][0]["id"] == "case"
    assert task_payload["case_id"] == "case"
    assert task_payload["dependency_status"] == "satisfied"
    assert task_payload["child_task_ids"] == []
    assert task_payload["blocked_by_task_ids"] == []
    assert task_payload["subtask_counts"]["total"] == 0


def test_latest_failed_action_feedback_is_leased_once(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _add_action_record(
        repo,
        action_id="action-old",
        dispatch_id="dispatch-old",
        created_at=base_time,
        success=False,
        error="agent backend cannot deliver task old-task",
        action={"deliverables": [{"task_id": "old-task"}]},
    )
    _add_action_record(
        repo,
        action_id="action-new",
        dispatch_id="dispatch-new",
        created_at=base_time + timedelta(minutes=1),
        success=False,
        error="release_branch must not start with 'refs/'",
        action={
            "git_release_requests": [
                {"project_id": "project", "release_branch": "refs/heads/release"}
            ]
        },
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=2)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["reason"] == "action_feedback"
    assert backend_lease["recent_action_feedback"] == [
        {
            "action_record_id": "action-new",
            "dispatch_id": "dispatch-new",
            "created_at": (base_time + timedelta(minutes=1)).isoformat(),
            "error": "release_branch must not start with 'refs/'",
            "action_fields": ["git_release_requests"],
            "correction_hints": [
                "Use bare branch names like agents/... or team/..., not refs/heads/...."
            ],
        }
    ]

    _team_admin_service(repo).mark_action_feedback_delivered(
        agent_id="backend",
        source_dispatch_id="dispatch-new",
        delivery_dispatch_id=str(backend_lease["dispatch_id"]),
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        dispatch_id=str(backend_lease["dispatch_id"]),
        action={
            "agent_id": "backend",
            "dispatch_id": str(backend_lease["dispatch_id"]),
            "notes": "Feedback received.",
        },
    )

    assert result["success"] is True
    assert state.action_records["action-new"].feedback_delivered_at is not None
    assert (
        state.action_records["action-new"].feedback_delivered_by_dispatch_id
        == backend_lease["dispatch_id"]
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=20)
    )
    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["recent_action_feedback"] == []


def test_release_request_feedback_points_to_feature_promotion(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _add_action_record(
        repo,
        action_id="action-release",
        dispatch_id="dispatch-release",
        created_at=base_time,
        success=False,
        error=(
            "deliverable deliverable has neither a feature branch, review branch, "
            "agent branch, nor artifact refs"
        ),
        action={"git_release_requests": [{"project_id": "project"}]},
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=1)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["reason"] == "action_feedback"
    feedback = backend_lease["recent_action_feedback"][0]
    assert feedback["action_fields"] == ["git_release_requests"]
    assert any("review branch" in hint for hint in feedback["correction_hints"])
    assert any("git_refs.agent_branch" in hint for hint in feedback["correction_hints"])


def test_combined_git_release_feedback_points_to_two_step_sequence(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _add_action_record(
        repo,
        action_id="action-combined",
        dispatch_id="dispatch-combined",
        created_at=base_time,
        success=False,
        error=("cannot combine git_feature_updates and git_release_requests in one Team action"),
        action={
            "git_feature_updates": [{"task_id": "task"}],
            "git_release_requests": [{"project_id": "project"}],
        },
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=1)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    feedback = backend_lease["recent_action_feedback"][0]
    assert feedback["action_fields"] == ["git_feature_updates", "git_release_requests"]
    assert any("git_release_requests alone" in hint for hint in feedback["correction_hints"])


def test_duplicate_release_package_feedback_points_to_existing_package(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _add_action_record(
        repo,
        action_id="action-duplicate-release",
        dispatch_id="dispatch-duplicate-release",
        created_at=base_time,
        success=False,
        error=(
            "release package rel-1 already exists; state blocked; "
            "release_branch team/team/releases/rel-1"
        ),
        action={"git_release_requests": [{"id": "rel-1", "project_id": "project"}]},
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=1)
    )

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    feedback = backend_lease["recent_action_feedback"][0]
    assert feedback["action_fields"] == ["git_release_requests"]
    assert any(
        "existing or blocked package" in hint
        and "release_branch/error" in hint
        and "next_action_hint" in hint
        and "failed feature or release promotion" in hint
        and "do not reuse the same id" in hint
        for hint in feedback["correction_hints"]
    )


def test_wake_due_agents_provider_prompts_and_mirrors_metadata(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).set_agent_avatar(
        agent_id="backend",
        avatar=AgentAvatar(
            asset_id="av_wakeprivate0000000000000000000001",
            source="generated",
            mime_type="image/webp",
            sha256="b" * 64,
            style_preset="minimal",
            prompt_note="private runtime prompt note",
            model="gpt-image-2",
            preview_id="av_previewwake00000000000000000001",
        ),
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(
            voice="cedar",
            speaking_style="private runtime voice direction",
        ),
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )

    def assert_context_registered(call: dict[str, object]) -> None:
        usage_context = ModelUsageStore(tmp_path / "model-usage.db").get_correlation_context(
            call["dispatch_id"]
        )
        assert usage_context is not None
        assert usage_context.team_id == "team"
        assert usage_context.project_id == "project"
        assert usage_context.agent_id == "backend"

    resumer = FakeResumer(on_resume=assert_context_registered)
    session_store = FakeSessionStore()
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert len(file_copier.calls) == 1
    copied_files = file_copier.calls[0]["files"]
    guidance_files = copied_files
    assert guidance_files == [
        {
            "source_path": None,
            "destination_path": TEAM_AGENT_GUIDANCE_PATH,
            "content": guidance_files[0]["content"],
            "content_format": None,
            "mode": "0644",
            "overwrite": True,
            "merge_strategy": "managed_section",
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            "remove_section_ids": list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS),
        }
    ]
    guidance = str(guidance_files[0]["content"])
    assert "Continue your assigned team-agent work" not in guidance
    assert "managers and project leads may create top-level tasks" not in guidance
    assert "non-lead roles" not in guidance
    assert 'Persistent agent id: "backend"' in guidance
    assert "Current display name: not configured; use your agent id" in guidance
    assert "Use the Team MCP tools granted to your session" in guidance
    assert TEAM_CREATE_CASE_TOOL in guidance
    assert "action schema" not in guidance
    assert "helper scripts" in guidance
    assert "task creation mandate in your wake prompt" in guidance
    assert "Core MCP surface:" in guidance
    assert "a superior may set state: finished on a subordinate non-terminal task" in guidance
    assert "Do not accept or reject someone else's task" in guidance
    assert "Use team_decide_review only for review requests assigned to you" in guidance
    assert "idle profile-eligible reviewer" in guidance
    assert "override_routing_gap_reason" in guidance
    assert "Release requests stand alone" in guidance
    assert "git_refs.review_branch or git_refs.agent_branch" in guidance
    assert "failed feature or release promotion" in guidance
    assert "allowed agents/... ref" in guidance
    assert "supersedes_deliverable_id" in guidance
    assert "item_type operator_directive" in guidance
    assert "response_requested is false" in guidance
    assert "no-op replies" in guidance
    assert "priority low, normal, or high" in guidance
    assert "Do not start tasks" in guidance
    assert "team_create_case" in guidance
    assert "team_create_tasks" in guidance
    assert "team_submit_deliverable" in guidance
    assert "team_submit_deliverable_for_review" in guidance
    assert "Use not_run when test tooling" in guidance
    assert "git_refs are provenance only" in guidance
    assert "team_record_note" in guidance
    assert "not a completion ritual" in guidance
    assert "team-action.json" not in guidance
    assert "team-finish" not in guidance
    assert "schema_version" not in guidance
    assert result["dispatches"][0]["team_mcp_tools"] == {
        "session_id": "session-backend",
        "requested_tools": list(ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES),
        "already_allowed_tools": [],
        "granted_tools": list(ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES),
        "removed_tools": [],
        "error": None,
    }
    assert session_store.sessions["session-backend"].allowed_tools == list(
        ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES
    )
    assert resumer.calls[0]["target"] == "session-backend"
    assert resumer.calls[0]["dispatch_id"] == result["dispatches"][0]["dispatch_id"]
    usage_context = ModelUsageStore(tmp_path / "model-usage.db").get_correlation_context(
        result["dispatches"][0]["dispatch_id"]
    )
    assert usage_context is not None
    assert usage_context.team_id == "team"
    assert usage_context.project_id == "project"
    assert usage_context.agent_id == "backend"
    prompt = str(resumer.calls[0]["prompt"])
    assert "wakeprivate" not in prompt
    assert "private runtime prompt note" not in prompt
    assert '"avatar"' not in prompt
    assert "private runtime voice direction" not in prompt
    assert '"voice_profile"' not in prompt
    for forbidden in (
        "team-action.json",
        "team-action.schema.json",
        "team-action.example.json",
        "team-finish",
        "schema_version",
        '"agent_id"',
        '"dispatch_id"',
    ):
        assert forbidden not in prompt
    assert "MCP action surface" in prompt
    assert "Deliverable mechanics for this wake" in prompt
    assert "Git release sequencing" in prompt
    assert "Do not combine team_update_feature_branch" in prompt
    assert "Valid owned task_id values for deliverable tools are: task" in prompt
    assert "/artifacts/team-deliverables/task/report.md" in prompt
    assert "team-deliverables/task/report.md" in prompt
    assert TEAM_AGENT_GUIDANCE_PATH in prompt
    assert TEAM_AGENT_GUIDANCE_SECTION_ID in prompt
    assert "Task creation mandate for this wake" in prompt
    assert "do not have create_root_task" in prompt
    assert "You may NOT create top-level tasks" in prompt
    assert "Every team_create_task or team_create_tasks call must set parent_task_id" in prompt
    assert "Identity detail: your full role_description is included" in prompt
    assert "Use team_get_my_identity" in prompt
    assert "A previous Team operation had validation errors" not in prompt
    for field_name in (
        "dependency_status",
        "child_task_ids",
        "subtask_counts",
    ):
        assert field_name in prompt
    assert "Release requests create new release packages only" in prompt
    assert "next_action_hint" in prompt
    assert "team_recover_release" in prompt
    assert session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"] == {
        "seeded": True,
        "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
        "agent_id": "backend",
        "display_name": None,
        "inactive": False,
        "inactive_reason": None,
        "copied_paths": [TEAM_AGENT_GUIDANCE_PATH],
    }
    assert session_store.calls[1]["metadata"]["team_addon"]["agent_id"] == "backend"


def test_wake_due_agents_provider_preserves_active_governance_tools(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="backend",
        member_team_ids=["team"],
        goals=[{"id": "goal", "description": "Watch Team health"}],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda-1",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review Team health",
        summary="Check the implementation team.",
        team_id="team",
        expected_revision=repo.read().revision,
    )
    _state, governance_leases = _team_runtime_service(repo).lease_due_governance_managers(limit=1)
    assert len(governance_leases) == 1

    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].allowed_tools = list(GOVERNANCE_TEAM_MCP_TOOL_NAMES)
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            limit=1,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert result["dispatches"][0]["agent_id"] == "backend"
    team_tools = result["dispatches"][0]["team_mcp_tools"]
    assert team_tools["requested_tools"] == list(ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES)
    assert team_tools["granted_tools"] == list(ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES)
    assert team_tools["removed_tools"] == []
    assert session_store.sessions["session-backend"].allowed_tools == [
        *ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES,
        *GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    ]


def test_wake_due_agents_provider_imports_review_artifact_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_space_path = "/teams/team/projects/project/deliverables/deliverable-1"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_key = "team/team/project/deliverable-1/dispatch-backend/bundle"
    artifact_record = PublishedArtifactStore(tmp_path).publish_single_file(
        key=artifact_key,
        session_id="session-backend",
        filename="report.md",
        artifact_path="report.md",
        content=b"review me\n",
    )
    artifact_store = ArtifactSpaceStore(tmp_path)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={
            "team_id": "team",
            "project_id": "project",
            "deliverable_id": "deliverable-1",
            "source": "team_addon",
        },
        subject="team-agent:backend",
    )
    artifact_state = artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject="team-agent:backend",
    )
    artifact_version = artifact_state.latest_versions_for_space(
        artifact_space_path,
        artifact_id="bundle",
    )[0]
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                    "artifact_refs": [artifact_ref],
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-luke",
                    "deliverable_id": "deliverable-1",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )
    assert result["success"] is True
    ref_hash = hashlib.sha256(artifact_space_path.encode("utf-8")).hexdigest()[:16]
    expected_destination = (
        f"team-review-inputs/review-luke/deliverable-1/bundle/ref-{ref_hash}-{artifact_version.id}"
    )
    exec_commands: list[str] = []

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del resolved_config
        effect = self.effect_definition
        exec_commands.append(str(effect.command))  # type: ignore[attr-defined]
        return {
            "success": True,
            "session_id": "session-luke",
            "vm_name": "vm-luke",
            "execution_backend": "libvirt_vm",
            "stdout": json.dumps(
                {
                    "success": True,
                    "artifacts_root": "/artifacts",
                    "destination_path": expected_destination,
                    "imported_file_count": 1,
                    "imported_paths": [f"{expected_destination}/report.md"],
                    "source_byte_count": len("review me\n"),
                    "overwrite": True,
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-luke"] = SimpleNamespace(
        session_id="session-luke",
        vm_id="vm-luke",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="luke",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert len(result["review_artifact_imports"]) == 1
    review_import = result["review_artifact_imports"][0]
    assert review_import == result["dispatches"][0]["review_artifact_imports"][0]
    assert review_import["review_request_id"] == "review-luke"
    assert review_import["deliverable_id"] == "deliverable-1"
    assert review_import["artifact_ref"] == artifact_ref
    assert review_import["artifact_space_path"] == artifact_space_path
    assert review_import["artifact_key"] == artifact_key
    assert review_import["destination_path"] == expected_destination
    assert review_import["artifact_path"] == f"/artifacts/{expected_destination}"
    assert review_import["imported_paths"] == [f"{expected_destination}/report.md"]
    assert review_import["imported_file_count"] == 1
    assert result["review_artifact_import_errors"] == []
    prompt = str(resumer.calls[0]["prompt"])
    assert "Review artifact access for this wake" in prompt
    assert "instead of trying to open artifact-space refs directly" in prompt
    prompt_context = _team_context_from_prompt(prompt)
    assert prompt_context["review_artifact_imports"] == result["review_artifact_imports"]
    assert "review_artifact_import_errors" not in prompt_context
    assert expected_destination in exec_commands[0]
    artifact_copy = file_copier.calls[-1]["files"][0]
    assert artifact_copy["source_path"] == str(
        PublishedArtifactStore(tmp_path).get(artifact_key).blob_path
    )
    assert str(artifact_copy["destination_path"]).endswith(f"{artifact_record.storage_id}.tar.gz")


def test_wake_due_agents_provider_respects_review_artifact_space_read_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_space_path = "/teams/team/projects/project/deliverables/restricted"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_key = "team/team/project/restricted/dispatch-backend/bundle"
    PublishedArtifactStore(tmp_path).publish_single_file(
        key=artifact_key,
        session_id="session-backend",
        filename="secret.md",
        artifact_path="secret.md",
        content=b"secret\n",
    )
    artifact_store = ArtifactSpaceStore(tmp_path)
    artifact_store.ensure_space(
        path=artifact_space_path,
        grants={
            "read_subjects": ["team-agent:lead"],
            "write_subjects": ["team-agent:backend"],
            "manage_subjects": ["team-agent:lead"],
        },
        subject="team-agent:lead",
    )
    artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject="team-agent:backend",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                    "artifact_refs": [artifact_ref],
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-luke",
                    "deliverable_id": "deliverable-1",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )
    assert result["success"] is True

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("unreadable artifact-space version should not import")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-luke"] = SimpleNamespace(
        session_id="session-luke",
        vm_id="vm-luke",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="luke",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["review_artifact_imports"] == []
    assert len(result["review_artifact_import_errors"]) == 1
    import_error = result["review_artifact_import_errors"][0]
    assert import_error["review_request_id"] == "review-luke"
    assert import_error["artifact_ref"] == artifact_ref
    assert import_error["artifact_space_path"] == artifact_space_path
    assert "team-agent:luke cannot read artifact space" in import_error["error"]
    assert "artifact_key" not in import_error
    prompt_context = _team_context_from_prompt(str(resumer.calls[0]["prompt"]))
    assert (
        prompt_context["review_artifact_import_errors"] == result["review_artifact_import_errors"]
    )


def test_wake_due_agents_provider_isolates_multiple_review_artifact_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_store = ArtifactSpaceStore(tmp_path)
    refs: list[str] = []
    keys: list[str] = []
    for index in (1, 2):
        artifact_space_path = f"/teams/team/projects/project/deliverables/deliverable-{index}"
        artifact_key = f"team/team/project/deliverable-{index}/dispatch-backend/bundle"
        PublishedArtifactStore(tmp_path).publish_single_file(
            key=artifact_key,
            session_id="session-backend",
            filename=f"report-{index}.md",
            artifact_path="report.md",
            content=f"review {index}\n".encode("utf-8"),
        )
        artifact_store.ensure_space(
            path=artifact_space_path,
            metadata={
                "team_id": "team",
                "project_id": "project",
                "deliverable_id": f"deliverable-{index}",
                "source": "team_addon",
            },
            subject="team-agent:backend",
        )
        artifact_store.publish_version(
            space_path=artifact_space_path,
            artifact_id="bundle",
            artifact_key=artifact_key,
            created_by_subject="team-agent:backend",
        )
        refs.append(f"artifact-space:{artifact_space_path}")
        keys.append(artifact_key)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                    "artifact_refs": refs,
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-luke",
                    "deliverable_id": "deliverable-1",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )
    assert result["success"] is True

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        return {
            "success": True,
            "session_id": "session-luke",
            "vm_name": "vm-luke",
            "execution_backend": "libvirt_vm",
            "stdout": json.dumps(
                {
                    "success": True,
                    "artifacts_root": "/artifacts",
                    "destination_path": "ignored",
                    "imported_file_count": 1,
                    "imported_paths": ["ignored/report.md"],
                    "source_byte_count": len("review 1\n"),
                    "overwrite": True,
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-luke"] = SimpleNamespace(
        session_id="session-luke",
        vm_id="vm-luke",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="luke",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["review_artifact_import_errors"] == []
    assert len(result["review_artifact_imports"]) == 2
    destinations = [record["destination_path"] for record in result["review_artifact_imports"]]
    assert len(set(destinations)) == 2
    assert all(
        destination.startswith("team-review-inputs/review-luke/deliverable-1/bundle/ref-")
        for destination in destinations
    )
    assert {record["artifact_key"] for record in result["review_artifact_imports"]} == set(keys)


def test_wake_due_agents_provider_reports_review_artifact_import_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_space_path = "/teams/team/projects/project/deliverables/missing"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                    "artifact_refs": [artifact_ref],
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-luke",
                    "deliverable_id": "deliverable-1",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )
    assert result["success"] is True

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("missing artifact-space version should not import")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-luke"] = SimpleNamespace(
        session_id="session-luke",
        vm_id="vm-luke",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="luke",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert result["review_artifact_imports"] == []
    assert len(result["review_artifact_import_errors"]) == 1
    import_error = result["review_artifact_import_errors"][0]
    assert import_error["review_request_id"] == "review-luke"
    assert import_error["deliverable_id"] == "deliverable-1"
    assert import_error["artifact_ref"] == artifact_ref
    assert import_error["artifact_space_path"] == artifact_space_path
    assert "artifact space does not exist" in import_error["error"]
    assert "session_id" not in import_error
    prompt = str(resumer.calls[0]["prompt"])
    assert "Review artifact access for this wake" in prompt
    assert "treat the review artifact as unavailable" in prompt
    prompt_context = _team_context_from_prompt(prompt)
    assert (
        prompt_context["review_artifact_import_errors"] == result["review_artifact_import_errors"]
    )
    assert "review_artifact_imports" not in prompt_context


def test_wake_due_agents_provider_does_not_import_review_artifacts_from_status_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_space_path = "/teams/team/projects/project/deliverables/deliverable-1"
    artifact_ref = f"artifact-space:{artifact_space_path}"
    artifact_key = "team/team/project/deliverable-1/dispatch-backend/bundle"
    PublishedArtifactStore(tmp_path).publish_single_file(
        key=artifact_key,
        session_id="session-backend",
        filename="report.md",
        artifact_path="report.md",
        content=b"review me\n",
    )
    artifact_store = ArtifactSpaceStore(tmp_path)
    artifact_store.ensure_space(
        path=artifact_space_path,
        metadata={
            "team_id": "team",
            "project_id": "project",
            "deliverable_id": "deliverable-1",
            "source": "team_addon",
        },
        subject="team-agent:backend",
    )
    artifact_store.publish_version(
        space_path=artifact_space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject="team-agent:backend",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                    "artifact_refs": [artifact_ref],
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "status_requests": [
                {
                    "recipient_agent_id": "luke",
                    "question": (
                        "Please inspect /artifacts/team-review-inputs/review-luke/"
                        f"deliverable-1/bundle/ref-example/report.md for {artifact_ref}."
                    ),
                    "response_requested": True,
                }
            ],
        },
    )
    assert result["success"] is True

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("status requests must not import review artifacts")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-luke"] = SimpleNamespace(
        session_id="session-luke",
        vm_id="vm-luke",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="luke",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert result["review_artifact_imports"] == []
    assert result["review_artifact_import_errors"] == []
    prompt_context = _team_context_from_prompt(str(resumer.calls[0]["prompt"]))
    assert "review_artifact_imports" not in prompt_context
    assert "review_artifact_import_errors" not in prompt_context
    guidance_files = [
        file
        for call in file_copier.calls
        for file in call.get("files") or []
        if file.get("destination_path") == "/home/agent/AGENTS.md"
    ]
    assert guidance_files
    assert "Review artifact handoff is through team_request_review" in str(
        guidance_files[-1]["content"]
    )
    assert "do not pass local artifact paths through communication tools" in str(
        guidance_files[-1]["content"]
    )


def test_wake_due_agents_provider_retries_queued_chat_before_regular_wake(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _mark_backend_busy(state: TeamState) -> TeamState:
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            active_dispatch_id="team-dispatch-active",
            wake_leased_until=now + timedelta(minutes=10),
        )
        return state

    repo.update(_mark_backend_busy)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer this after your current wake finishes.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )

    def _release_backend(state: TeamState) -> TeamState:
        state.agent_runtimes["backend"].wake_leased_until = None
        return state

    repo.update(_release_backend)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            limit=1,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 0
    assert result["leases"] == []
    assert result["dispatches"] == []
    assert len(result["chat_dispatches"]) == 1
    chat_dispatch = result["chat_dispatches"][0]
    assert chat_dispatch["dispatch_id"] == dispatch_id
    assert chat_dispatch["message_id"] == message.id
    assert chat_dispatch["team_chat_mcp_tools"] == {
        "session_id": "session-backend",
        "requested_tools": [TEAM_REPLY_TO_CHAT_TOOL, *ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES],
        "already_allowed_tools": [],
        "granted_tools": [TEAM_REPLY_TO_CHAT_TOOL, *ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES],
        "removed_tools": [],
        "error": None,
    }
    assert session_store.sessions["session-backend"].allowed_tools == [
        TEAM_REPLY_TO_CHAT_TOOL,
        *ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES,
    ]
    assert resumer.calls[0]["target"] == "session-backend"
    assert resumer.calls[0]["dispatch_id"] == dispatch_id
    prompt = str(resumer.calls[0]["prompt"])
    assert "Team Add-on Chat tab" in prompt
    assert f"Human operator message ID: {message.id}" in prompt
    assert f"Reply with the {TEAM_REPLY_TO_CHAT_TOOL} MCP tool" in prompt
    assert "Current dispatch Team state mutation: allowed" in prompt
    assert "ordinary Team MCP tools granted to this chat wake" in prompt
    assert "Ignore earlier transcript statements that a prior chat wake was reply-only" in prompt
    assert "team-action.json" not in prompt
    assert "schema_version" not in prompt
    assert "dispatch_id" not in prompt
    assert "Please answer this after your current wake finishes." in prompt
    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversation_messages[message.id].status == "running"
    assert reread.agent_runtimes["backend"].active_dispatch_id == dispatch_id
    assert reread.agent_runtimes["backend"].wake_reason == "team_chat"


def test_wake_due_agents_provider_grants_reply_only_chat_tool_when_mutation_disabled(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please just reply.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def _disable_mutation(state: TeamState) -> TeamState:
        state.conversation_messages[message.id].metadata["allow_team_mutations"] = False
        return state

    repo.update(_disable_mutation)
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id, dispatch_id=dispatch_id
    )
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].allowed_tools = list(ORDINARY_TEAM_MCP_TOOL_NAMES)
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            limit=1,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    chat_tools = result["chat_dispatches"][0]["team_chat_mcp_tools"]
    assert chat_tools["requested_tools"] == [TEAM_REPLY_TO_CHAT_TOOL]
    assert chat_tools["granted_tools"] == [TEAM_REPLY_TO_CHAT_TOOL]
    assert chat_tools["removed_tools"] == list(ORDINARY_TEAM_MCP_TOOL_NAMES)
    assert session_store.sessions["session-backend"].allowed_tools == [TEAM_REPLY_TO_CHAT_TOOL]
    prompt = str(resumer.calls[0]["prompt"])
    assert "reply-only" in prompt
    assert "no Team state change was performed in this dispatch" in prompt
    assert "Do not promise to make the change later" in prompt
    assert "Current dispatch Team state mutation: allowed" not in prompt
    assert "ordinary Team MCP tools granted to this chat wake" not in prompt


def test_wake_due_agents_provider_limit_zero_dispatches_no_queued_chat(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer when a wake cycle is available.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            limit=0,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
            session_metadata_updater=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 0
    assert result["leases"] == []
    assert result["dispatches"] == []
    assert result["chat_dispatches"] == []
    assert resumer.calls == []
    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversation_messages[message.id].status == "queued"
    assert reread.conversation_messages[message.id].dispatch_id == dispatch_id
    runtime = reread.agent_runtimes.get("backend")
    assert runtime is None or runtime.active_dispatch_id is None
    assert runtime is None or runtime.wake_leased_until is None


def test_wake_governance_managers_uses_governance_prompt_without_action_seed(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).register_agent(
        agent_id="portfolio-manager",
        session_id="session-portfolio-manager",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="portfolio-manager",
        member_team_ids=["team"],
        goals=[{"id": "cost", "description": "Keep Team under budget"}],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_governance_agenda_item(
        agenda_item_id="agenda-1",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review weekly status",
        summary="Check whether Team is blocked.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    def assert_context_registered(call: dict[str, object]) -> None:
        usage_context = ModelUsageStore(tmp_path / "model-usage.db").get_correlation_context(
            call["dispatch_id"]
        )
        assert usage_context is not None
        assert usage_context.team_id is None
        assert usage_context.project_id is None
        assert usage_context.agent_id == "portfolio-manager"

    resumer = FakeResumer(on_resume=assert_context_registered)
    file_copier = FakeFileCopier()
    session_store = FakeSessionStore()
    session_store.sessions["session-portfolio-manager"] = SimpleNamespace(
        session_id="session-portfolio-manager",
        vm_id="vm-portfolio-manager",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    provider = TeamWakeGovernanceManagersEffectProvider()
    provider.initialize(
        TeamWakeGovernanceManagersEffectDefinition(
            id="wake-governance",
            display_name="Wake governance",
            type="effect:team:wake-governance-managers",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert result["leases"][0]["reason"] == "operator_directive"
    assert result["leases"][0]["governance_group"]["id"] == "portfolio"
    assert result["leases"][0]["agenda"][0]["id"] == "agenda-1"
    assert len(file_copier.calls) == 1
    guidance_files = file_copier.calls[0]["files"]
    assert [file["section_id"] for file in guidance_files] == [
        TEAM_AGENT_GUIDANCE_SECTION_ID,
        TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
    ]
    assert guidance_files[0]["remove_section_ids"] == list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS)
    inactive_guidance = str(guidance_files[0]["content"])
    assert "not currently assigned to a Team" in inactive_guidance
    assert "Normal Team-agent task" in inactive_guidance
    assert (
        "Inactive or teamless sessions must not use ordinary Team action tools" in inactive_guidance
    )
    assert "team-action.json" not in inactive_guidance
    guidance = str(guidance_files[1]["content"])
    assert 'Persistent agent id: "portfolio-manager"' in guidance
    assert "has no team_id" in guidance
    assert "Member teams remain separate execution units" in guidance
    assert "Do not take ownership of member-team project tasks" in guidance
    assert "Governance wakes grant governance MCP tools only" in guidance
    assert "team-action.json" not in guidance
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in guidance
    assert len(resumer.calls) == 1
    assert resumer.calls[0]["target"] == "session-portfolio-manager"
    assert "governance manager" in str(resumer.calls[0]["prompt"])
    assert "Do not take ownership of member-team project tasks" in str(resumer.calls[0]["prompt"])
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in str(resumer.calls[0]["prompt"])
    assert "team_update_task" not in str(resumer.calls[0]["prompt"])
    assert "team_submit_deliverable" not in str(resumer.calls[0]["prompt"])
    assert result["dispatches"][0]["governance_agent_guidance"] == {
        "seeded": True,
        "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        "agent_id": "portfolio-manager",
        "display_name": None,
        "vm_ip": None,
        "copied_paths": [TEAM_AGENT_GUIDANCE_PATH, TEAM_AGENT_GUIDANCE_PATH],
    }
    assert result["dispatches"][0]["governance_mcp_tools"] == {
        "session_id": "session-portfolio-manager",
        "requested_tools": list(GOVERNANCE_TEAM_MCP_TOOL_NAMES),
        "already_allowed_tools": [],
        "granted_tools": list(GOVERNANCE_TEAM_MCP_TOOL_NAMES),
        "removed_tools": [],
        "error": None,
    }
    assert session_store.sessions["session-portfolio-manager"].allowed_tools == list(
        GOVERNANCE_TEAM_MCP_TOOL_NAMES
    )
    usage_context = ModelUsageStore(tmp_path / "model-usage.db").get_correlation_context(
        result["dispatches"][0]["dispatch_id"]
    )
    assert usage_context is not None
    assert usage_context.team_id is None
    assert usage_context.project_id is None
    assert usage_context.agent_id == "portfolio-manager"
    team_metadata = session_store.calls[0]["metadata"]["team_addon"]
    assert team_metadata["governance_agent_guidance"] == {
        "seeded": True,
        "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        "agent_id": "portfolio-manager",
        "display_name": None,
        "copied_paths": [TEAM_AGENT_GUIDANCE_PATH, TEAM_AGENT_GUIDANCE_PATH],
    }
    assert team_metadata["agent_guidance"] == {
        "seeded": True,
        "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
        "agent_id": "portfolio-manager",
        "display_name": None,
        "inactive": True,
        "inactive_reason": "no_team_assignment",
        "copied_paths": [TEAM_AGENT_GUIDANCE_PATH, TEAM_AGENT_GUIDANCE_PATH],
    }
    assert repo.read().governance_runtimes["portfolio"].active_dispatch_id is None


def test_build_prompt_allows_root_tasks_for_create_root_task_permission(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    lease["agent"] = {**lease["agent"], "effective_role_permissions": ["create_root_task"]}

    prompt = _build_prompt(lease)

    assert "effective permissions include create_root_task" in prompt
    assert "You may create top-level tasks with team_create_task or team_create_tasks" in prompt
    assert "You may NOT create top-level tasks" not in prompt


def test_build_prompt_includes_subordinate_role_context(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        description="Review code for correctness; do not own implementation work.",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-reviewer",
        display_name="Reviewer",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _add_pending_inbox(repo, item_id="lead-inbox", recipient_agent_id="lead")

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "lead")
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)

    assert context["subordinate_ids"] == ["backend", "reviewer"]
    subordinates = {item["id"]: item for item in context["subordinates"]}
    assert subordinates["backend"]["role_name"] == "Backend"
    assert subordinates["backend"]["project_ids"] == ["project"]
    assert subordinates["reviewer"]["display_name"] == "Reviewer"
    assert subordinates["reviewer"]["role_name"] == "Reviewer"
    assert (
        subordinates["reviewer"]["role_description"]
        == "Review code for correctness; do not own implementation work."
    )
    assert "Role-aware delegation" in prompt
    assert "Reviewer roles should generally receive team_request_review calls" in prompt


def test_build_prompt_includes_full_waking_agent_role_description(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    full_description = (
        "Own the backend implementation role boundary. "
        + ("Detailed role responsibility. " * 20)
        + "UNTRUNCATED_ROLE_DESCRIPTION_SENTINEL"
    )
    _team_admin_service(repo).update_role(
        role_id="project__backend",
        description=full_description,
        set_description=True,
        expected_revision=repo.read().revision,
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)

    assert context["agent"]["role_description"] == full_description
    assert "UNTRUNCATED_ROLE_DESCRIPTION_SENTINEL" in prompt


def test_build_prompt_includes_release_package_recovery_context(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)

    def _add_blocked_release(state: TeamState) -> TeamState:
        state.release_packages["rel-1"] = ReleasePackage(
            id="rel-1",
            project_id="project",
            deliverable_ids=[],
            release_branch="team/team/releases/rel-1",
            git_promotion_record_ids=["promotion-release"],
            state=TeamRecordState.BLOCKED,
            error="merge conflict while merging feature into team/team/releases/rel-1",
        )
        state.git_promotions["promotion-release"] = GitPromotionRecord(
            id="promotion-release",
            project_id="project",
            release_package_id="rel-1",
            actor_agent_id="lead",
            source_branch="team/team/features/task",
            target_branch="team/team/releases/rel-1",
            promotion_kind="release",
            state="failed",
            error="merge conflict",
        )
        return state

    repo.update(_add_blocked_release)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")

    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)

    assert set(context["release_packages"]) == {"rel-1"}
    assert context["release_packages"]["rel-1"]["next_action"] == ("resolve_blocked_release")
    assert (
        "do not create another git_release_requests"
        in context["release_packages"]["rel-1"]["next_action_hint"]
    )
    assert "git_release_recoveries" in context["release_packages"]["rel-1"]["next_action_hint"]
    assert any(
        row["kind"] == "blocked_release_package"
        and row["release_package_id"] == "rel-1"
        and "promotion target branch team/team/releases/rel-1" in row["next_action_hint"]
        for row in context["runtime_attention"]
    )


def test_manager_fallback_prompt_includes_team_context_for_handled_review_stall(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_git_work_context(repo)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-stalled",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                    "artifact_refs": ["artifact-space:/teams/team/projects/project/backend.tar.gz"],
                    "branch_context_id": "ctx-task",
                    "git_refs": {
                        "agent_branch": "agents/session-backend/review",
                        "commit_sha": "abc123",
                    },
                }
            ],
        },
    )
    assert result["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-manager",
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "manager",
                    "instructions": "Review the stalled backend slice.",
                }
            ],
        },
    )
    assert result["success"] is True
    assert state.review_requests["review-manager"].state.value == "pending"

    def _handle_all_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_all_inbox)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    lease = next(lease for lease in leases if lease["agent"]["id"] == "manager")

    assert lease["reason"] == "manager_fallback"
    assert lease["tasks"] == []
    assert lease["inbox"] == []

    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]

    assert "Manager fallback wake" in prompt
    assert (
        "Do not return a no-op merely because your own tasks and inbox arrays are empty" in prompt
    )
    assert "reviewer_without_assigned_review_work gaps" in prompt
    assert "never reuse the existing review request id" in prompt
    assert "override_routing_gap_reason" in prompt
    assert "manager_fallback_context" not in context
    assert "runtime_operations" not in fallback_summary
    assert "action_records" not in fallback_summary
    assert fallback_summary["summary_counts"]["open_task_count"] == 1
    assert fallback_summary["summary_counts"]["pending_review_request_count"] == 1
    work = {item["id"]: item for item in fallback_summary["open_work"]}
    assert work["task"]["state"] == "waiting_for_review"
    assert work["task"]["deliverable_ids"] == ["deliverable-stalled"]
    deliverables = {item["id"]: item for item in fallback_summary["deliverables"]}
    assert deliverables["deliverable-stalled"]["task_id"] == "task"
    assert deliverables["deliverable-stalled"]["artifact_refs"] == [
        "artifact-space:/teams/team/projects/project/backend.tar.gz"
    ]
    assert deliverables["deliverable-stalled"]["git_refs"] == {
        "branch_context_id": "ctx-task",
        "agent_branch": "agents/session-backend/review",
        "commit_sha": "abc123",
    }
    reviews = {item["id"]: item for item in fallback_summary["review_requests"]}
    assert reviews["review-manager"]["reviewer_id"] == "manager"
    assert reviews["review-manager"]["state"] == "pending"
    agents = {item["id"]: item for item in fallback_summary["agents"]}
    assert agents["luke"]["display_name"] == "Luke"
    assert agents["luke"]["active_task_count"] == 0
    assert agents["luke"]["pending_review_request_count"] == 0
    assert agents["luke"]["pending_inbox_count"] == 0
    assert fallback_summary["pending_inbox"] == []
    assert any(
        item["kind"] == "reviewer_without_assigned_review_work"
        and item["agent"] == "luke"
        and item["deliverable_ids"] == ["deliverable-stalled"]
        and item["existing_reviewer_ids"] == ["manager"]
        for item in fallback_summary["routing_gaps"]
    )
    assert len(prompt.encode("utf-8")) < 32_000


def test_manager_fallback_prompt_includes_pending_deliverable_on_terminal_task(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_git_work_context(
        repo,
        agent_branch="agents/session-backend/feature",
        commit_sha="feedface",
    )
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-feature",
                    "task_id": "task",
                    "summary": "Feature branch is ready for release review.",
                    "branch_context_id": "ctx-task",
                    "git_refs": {
                        "feature_branch": "team/team/features/task-backend",
                        "commit_sha": "feedface",
                    },
                }
            ],
        },
    )
    assert result["success"] is True

    def _make_task_terminal_without_review(state: TeamState) -> TeamState:
        state.tasks["task"].state = TeamTaskState.ACCEPTED
        state.deliverables[
            "deliverable-feature"
        ].git_refs.feature_branch = "team/team/features/task-backend"
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_make_task_terminal_without_review)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    lease = leases[0]
    assert lease["reason"] == "manager_fallback"

    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]
    assert fallback_summary["summary_counts"]["open_task_count"] == 1
    assert fallback_summary["summary_counts"]["active_deliverable_count"] == 1
    work = {item["id"]: item for item in fallback_summary["open_work"]}
    assert work["task"]["state"] == "accepted"
    deliverables = {item["id"]: item for item in fallback_summary["deliverables"]}
    assert deliverables["deliverable-feature"]["state"] == "pending"
    assert deliverables["deliverable-feature"]["git_refs"] == {
        "branch_context_id": "ctx-task",
        "agent_branch": "agents/session-backend/feature",
        "feature_branch": "team/team/features/task-backend",
        "commit_sha": "feedface",
    }
    assert any(
        item["kind"] == "reviewer_without_assigned_review_work"
        and item["agent"] == "luke"
        and item["deliverable_ids"] == ["deliverable-feature"]
        for item in fallback_summary["routing_gaps"]
    )
    assert "deliverable-feature" in prompt


@pytest.mark.parametrize("blocker", ["paused", "stand_down"])
def test_manager_fallback_routing_gaps_skip_unwakeable_reviewers(
    tmp_path: Path,
    blocker: str,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-stalled",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-manager",
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "manager",
                }
            ],
        },
    )
    assert result["success"] is True

    def _handle_all_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_all_inbox)
    if blocker == "paused":
        _team_admin_service(repo).update_agent(
            agent_id="luke",
            status=AgentStatus.PAUSED,
            expected_revision=repo.read().revision,
        )
    else:
        _team_runtime_service(repo).stand_down_agent(
            agent_id="luke",
            reason="Reviewer temporarily unavailable.",
            set_by="operator:test",
            expected_revision=repo.read().revision,
        )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "manager")
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]

    assert "luke" not in fallback_summary["reviewer_candidate_ids"]
    assert not any(item["agent"] == "luke" for item in fallback_summary["routing_gaps"])


def test_manager_fallback_not_leased_while_reviewer_is_awake(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    def _lease_luke(state: TeamState) -> TeamState:
        state.agent_runtimes["luke"] = AgentRuntime(
            agent_id="luke",
            active_dispatch_id="team-dispatch-luke",
            wake_leased_until=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        )
        return state

    repo.update(_lease_luke)

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert not any(lease["agent"]["id"] == "manager" for lease in leases)


def test_manager_fallback_requires_review_profile_for_reviewer_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo, with_review_profile=False)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]

    assert "luke" not in fallback_summary["reviewer_candidate_ids"]
    assert not any(item["agent"] == "luke" for item in fallback_summary["routing_gaps"])


def test_manager_fallback_blank_review_profile_filter_allows_reviewer_candidate(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo, with_review_profile=False)
    _team_admin_service(repo).create_review_profile(
        profile_id="project-review-profile",
        team_id="team",
        project_id="project",
        name="Project review",
        description="Default review checks for project deliverables.",
        applicable_role_ids=[],
        expected_revision=repo.read().revision,
    )
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]

    assert "luke" in fallback_summary["reviewer_candidate_ids"]
    assert any(
        item["kind"] == "reviewer_without_assigned_review_work"
        and item["agent"] == "luke"
        and item["deliverable_ids"] == ["deliverable-stalled"]
        for item in fallback_summary["routing_gaps"]
    )


def test_manager_fallback_blocks_self_review_when_idle_profile_reviewer_exists(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": lease["dispatch_id"],
            "review_decisions": [
                {
                    "review_request_id": "review-manager",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert "idle profile-eligible reviewer Luke (luke)" in result["errors"][0]
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.PENDING


def test_manager_fallback_blocks_self_review_for_terminal_task_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    def _mark_task_terminal(state: TeamState) -> TeamState:
        state.tasks["task"].state = TeamTaskState.ACCEPTED
        return state

    repo.update(_mark_task_terminal)
    dispatch_id = _set_manager_fallback_runtime(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": dispatch_id,
            "review_decisions": [
                {
                    "review_request_id": "review-manager",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert "idle profile-eligible reviewer Luke (luke)" in result["errors"][0]
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.PENDING


def test_manager_fallback_allows_self_review_when_reviewer_busy_elsewhere(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="luke-review-task",
        owner_agent_id="luke",
        project_id="project",
        priority=10,
        title="Independent review task",
        description="Keep Luke busy on unrelated review work.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    dispatch_id = _set_manager_fallback_runtime(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": dispatch_id,
            "review_decisions": [
                {
                    "review_request_id": "review-manager",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is True
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.ACCEPTED


def test_manager_fallback_allows_self_review_when_reviewer_already_reviewed_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-luke",
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )
    assert result["success"] is True
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "luke",
            "review_decisions": [
                {
                    "review_request_id": "review-luke",
                    "state": "accepted",
                }
            ],
        },
    )
    assert result["success"] is True

    def _handle_luke_review_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_luke_review_inbox)

    dispatch_id = _set_manager_fallback_runtime(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": dispatch_id,
            "review_decisions": [
                {
                    "review_request_id": "review-manager",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is True
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.ACCEPTED
    assert reread.review_requests["review-luke"].state == TeamRecordState.ACCEPTED


def test_manager_fallback_allows_self_review_with_override_reason(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": lease["dispatch_id"],
            "review_decisions": [
                {
                    "review_request_id": "review-manager",
                    "state": "accepted",
                    "override_routing_gap_reason": "Urgent manager review required.",
                }
            ],
        },
    )

    assert result["success"] is True
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.ACCEPTED


def test_manager_fallback_creates_fresh_review_for_idle_profile_reviewer(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": lease["dispatch_id"],
            "review_requests": [
                {
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "luke",
                    "instructions": "Review the stalled backend slice.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_review_request_ids"]) == 1
    review_id = result["created_review_request_ids"][0]
    reread = repo.read()
    assert reread.review_requests[review_id].reviewer_agent_id == "luke"
    assert any(
        item.recipient_agent_id == "luke" and item.payload.get("review_request_id") == review_id
        for item in reread.inbox.values()
    )


def test_review_handoff_duplicate_id_feedback_is_not_prompted(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": lease["dispatch_id"],
            "review_requests": [
                {
                    "id": "review-manager",
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "luke",
                }
            ],
        },
    )

    assert result["success"] is False
    assert "review request review-manager already exists" in result["errors"][0]

    next_lease = _lease_manager_fallback(
        repo,
        now=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
    )
    prompt = _build_prompt(next_lease)

    assert "Most recent Team action feedback" not in prompt
    assert "Do not repeat these mistakes" not in prompt
    assert "review request review-manager already exists" not in prompt


def test_manager_fallback_prompt_caps_large_history(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-stalled",
                    "task_id": "task",
                    "summary": "Backend slice ready for review.",
                }
            ],
        },
    )
    assert result["success"] is True
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-manager",
                    "deliverable_id": "deliverable-stalled",
                    "reviewer_agent_id": "manager",
                }
            ],
        },
    )
    assert result["success"] is True

    def _add_large_history(state: TeamState) -> TeamState:
        history_start = datetime(2026, 1, 1, tzinfo=UTC)
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        for index in range(80):
            state.inbox[f"handled-{index:03d}"] = AgentInboxItem(
                id=f"handled-{index:03d}",
                recipient_agent_id="manager",
                sender_agent_id="lead",
                task_id="task",
                item_type=TeamInboxItemType.PROGRESS_REPORT,
                payload={"summary": "handled history " + ("x" * 4000)},
                state=TeamInboxItemState.HANDLED,
                created_at=history_start + timedelta(minutes=index),
            )
            state.action_records[f"failed-{index:03d}"] = AgentActionRecord(
                id=f"failed-{index:03d}",
                agent_id="backend",
                dispatch_id=f"dispatch-{index:03d}",
                success=False,
                error="validation failed " + ("y" * 4000),
                created_at=history_start + timedelta(minutes=index),
            )
        return state

    repo.update(_add_large_history)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "manager")
    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)
    fallback_summary = context["manager_fallback_summary"]

    assert len(prompt.encode("utf-8")) < 32_000
    assert "handled-079" not in prompt
    assert fallback_summary["summary_counts"]["failed_action_count"] == 80
    assert "latest_failed_actions" not in fallback_summary
    assert "validation failed" not in prompt
    assert fallback_summary["omitted_counts"]["latest_failed_actions"] == 77


def test_wake_due_agents_provider_includes_expired_session_health_in_prompt(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_pending_inbox(repo, item_id="lead-inbox", recipient_agent_id="lead")
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].expires_at = datetime(
        2025,
        12,
        31,
        tzinfo=UTC,
    )
    session_store.sessions["session-lead"] = SimpleNamespace(
        session_id="session-lead",
        vm_id="vm-lead",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="lead",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    prompt = str(resumer.calls[0]["prompt"])
    context = _team_context_from_prompt(prompt)
    assert context["session_health"] == [
        {
            "agent": "backend",
            "team_id": "team",
            "project_ids": ["project"],
            "lead_project_ids": [],
            "role_id": "project__backend",
            "session_id": "session-backend",
            "session_present": True,
            "expires_at": "2025-12-31T00:00:00+00:00",
            "is_expired": True,
            "status": "expired",
        },
        {
            "agent": "lead",
            "team_id": "team",
            "project_ids": ["project"],
            "lead_project_ids": ["project"],
            "role_id": "project__project-lead",
            "session_id": "session-lead",
            "session_present": True,
            "expires_at": None,
            "is_expired": False,
            "status": "unknown",
        },
    ]


def test_wake_due_agents_provider_does_not_prompt_latest_action_feedback(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    failed_dispatch_id = "failed-dispatch"
    _add_action_record(
        repo,
        action_id="action-feedback",
        dispatch_id=failed_dispatch_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        success=False,
        error="agent backend cannot deliver task other-task",
        action={"deliverables": [{"task_id": "other-task"}]},
    )
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    prompt = str(resumer.calls[0]["prompt"])
    assert "Most recent Team action feedback" not in prompt
    assert "previous Team dispatch had validation errors" not in prompt
    assert "action-feedback" not in prompt
    assert "Only the task owner can create deliverables for that task." not in prompt
    reread = TeamStateRepository(tmp_path).read()
    record = reread.action_records["action-feedback"]
    assert record.feedback_delivered_at is None
    assert record.feedback_delivered_by_dispatch_id is None


def test_wake_due_agents_provider_rotates_context_when_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="controlled rotation",
    )
    resumer = FakeResumer(result={"thread_id": "thread-rotated"})
    session_store = FakeSessionStore()
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    prompt = str(resumer.calls[0]["prompt"])
    assert "Prior handoff summary from Team state" in prompt
    assert "controlled rotation" in prompt
    assert result["dispatches"][0]["context_rotation"] == {
        "rotated": True,
        "reason": "manual",
    }
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.last_context_rotated_at is not None
    assert runtime.last_thread_id == "thread-rotated"
    assert (
        session_store.sessions["session-backend"].metadata["team_addon"]["context_rotation_pending"]
        is False
    )
    assert session_store.sessions["session-backend"].metadata["team_addon"][
        "last_context_rotated_at"
    ]


def test_wake_due_agents_provider_rotates_every_wake_policy(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    resumer = FakeResumer(result={"thread_id": "thread-every-wake"})
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    assert result["dispatches"][0]["context_rotation"] == {
        "rotated": True,
        "reason": "every_wake",
    }
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
    assert runtime.context_rotation_pending is False
    assert runtime.last_thread_id == "thread-every-wake"
    assert (
        session_store.sessions["session-backend"].metadata["team_addon"]["context_clearing_policy"]
        == "every_wake"
    )


def test_wake_due_agents_provider_rotates_idle_agent_when_only_rotation_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="idle controlled rotation",
    )
    resumer = FakeResumer(result={"thread_id": "thread-idle"})
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    assert "Active tasks: none." in str(resumer.calls[0]["prompt"])
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.last_thread_id == "thread-idle"


def test_status_report_only_idle_rotation_does_not_rearm_auto_context_clearing(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    runtime = repo.read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _state, leases = _team_runtime_service(repo).lease_due_agents(now=base_time)
    dispatch_id = str(leases[0]["dispatch_id"])
    _team_runtime_service(repo).record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="thread-idle",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "status_reports": [
                {
                    "summary": "No active work",
                    "message": "No active tasks, inbox items, or reviews.",
                    "recipient_agent_id": "lead",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["applied"] is True
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.last_progress_at is None
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=base_time + timedelta(minutes=20)
    )
    assert "backend" not in _leased_agent_ids(leases)


def test_substantive_idle_rotation_action_can_rearm_auto_context_clearing(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.ACCEPTED)

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    dispatch_id = str(leases[0]["dispatch_id"])
    _team_runtime_service(repo).record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="thread-idle",
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "questions": [
                {
                    "question": "Need another assignment?",
                    "recipient_agent_id": "lead",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["applied"] is True
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"
    assert runtime.last_progress_at is not None


def test_disabling_auto_context_clearing_clears_pending_idle_rotation(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    assert repo.read().agent_runtimes["backend"].context_rotation_pending is True

    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=False,
        set_auto_context_clearing_enabled=True,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is False
    assert runtime.context_rotation_reason is None
    assert runtime.context_rotation_requested_at is None
    assert runtime.next_wake_at is None
    assert runtime.last_handoff_summary is None


def test_disabling_auto_context_clearing_preserves_manual_rotation(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="manual refresh",
    )

    state = _team_admin_service(repo).update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=False,
        set_auto_context_clearing_enabled=True,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "manual"
    assert runtime.context_rotation_requested_by == "operator:test"
    assert runtime.last_handoff_summary is not None


def test_wake_due_agents_provider_keeps_rotation_pending_when_resume_fails(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend", requested_by="operator:test"
    )
    resumer = FakeResumer(result={"success": False, "message": "resume failed"})
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    assert resumer.calls[0]["resume_mode"] == "new_context"
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.last_context_rotated_at is None


def test_wake_due_agents_provider_skips_team_when_cost_limit_reached(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd=0,
        set_max_cost_usd=True,
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 0
    assert result["dispatches"] == []
    assert resumer.calls == []
    assert result["cost_guard_blocked_team_ids"] == ["team"]
    assert result["cost_guard_blocked_teams"][0]["team_id"] == "team"
    assert result["cost_guard_blocked_teams"][0]["max_cost_usd"] == 0.0
    assert result["cost_guard_blocked_teams"][0]["estimated_cost"] == 0.0


def test_wake_due_agents_provider_cost_guard_blocks_queued_chat(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_team(
        team_id="team",
        max_cost_usd=0,
        set_max_cost_usd=True,
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer after the cap is raised.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        reason="agent backend already has active Team dispatch team-dispatch-active",
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
            session_metadata_updater=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 0
    assert result["leases"] == []
    assert result["dispatches"] == []
    assert result["chat_dispatches"] == []
    assert resumer.calls == []
    assert result["cost_guard_blocked_team_ids"] == ["team"]
    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversation_messages[message.id].status == "queued"
    assert reread.conversation_messages[message.id].dispatch_id == dispatch_id
    runtime = reread.agent_runtimes.get("backend")
    assert runtime is None or runtime.active_dispatch_id is None
    assert runtime is None or runtime.wake_leased_until is None


def test_wake_due_agents_provider_prepares_project_repo_when_configured(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    repo_cloner = FakeRepoCloner()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=repo_cloner,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert repo_cloner.calls == [
        {
            "vm_name": "vm-backend",
            "session_id": "session-backend",
            "repo_source": "/opt/repos/Truman",
            "clone_branch": "main",
            "vm_user": "agent",
            "directory_path": "root",
            "git_author_name": None,
            "git_author_email": None,
            "git_committer_name": None,
            "git_committer_email": None,
        }
    ]
    project_repo = result["dispatches"][0]["project_repo"]
    assert project_repo["target_dir"] == "/home/agent/Truman"
    assert project_repo["base_branch"] == "main"
    assert project_repo["branch_context_id"]
    assert project_repo["agent_branch"] == "agents/session-backend/work-session-backend"
    git_context = project_repo["git_work_contexts"][0]
    assert git_context == {
        "id": project_repo["branch_context_id"],
        "branch_context_id": project_repo["branch_context_id"],
        "agent_id": "backend",
        "task_id": "task",
        "agent_branch": "agents/session-backend/work-session-backend",
        "base_branch": "main",
        "dispatch_id": result["dispatches"][0]["dispatch_id"],
        "worktree_path": "/home/agent/Truman",
        "state": "active",
    }
    assert project_repo["branch_contexts_by_task_id"] == {"task": git_context}
    assert result["git_work_contexts"] == [git_context]
    assert result["prepared_project_repos"] == [
        {
            "agent_id": "backend",
            "session_id": "session-backend",
            "project_ids": ["project"],
            "dispatch_id": result["dispatches"][0]["dispatch_id"],
            "project_repo": project_repo,
            "target_dir": "/home/agent/Truman",
        }
    ]
    assert result["failed_project_repo_preparations"] == []
    prompt = str(resumer.calls[0]["prompt"])
    assert '"project_repo"' in prompt
    assert project_repo["branch_context_id"] in prompt
    assert "agents/session-backend/work-session-backend" in prompt
    assert "/home/agent/Truman" in prompt
    assert "The project host checkout is not mounted in the guest" in prompt
    assert "project_repo.git_work_contexts" in prompt
    assert session_store.calls[1]["metadata"]["team_addon"]["project_repo"] == project_repo
    assert session_store.calls[2]["metadata"]["team_addon"]["agent_id"] == "backend"
    reread = TeamStateRepository(tmp_path).read()
    assert set(reread.git_work_contexts) == {project_repo["branch_context_id"]}
    assert reread.git_work_contexts[project_repo["branch_context_id"]].dispatch_id == (
        result["dispatches"][0]["dispatch_id"]
    )


def test_wake_due_agents_refreshes_state_after_repo_clone_revision_change(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
    repo_cloner = FakeRepoCloner(on_clone=lambda: repo.update(lambda state: state))
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=repo_cloner,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(result["dispatches"]) == 1
    assert len(repo_cloner.calls) == 1
    assert len(resumer.calls) == 1
    assert result["transient_conflicts"] == []
    assert len(repo.read().git_work_contexts) == 1


def test_wake_due_agents_retries_git_context_commit_conflict_without_recloning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
    original = TeamAdminService.create_git_work_context_with_id
    calls = 0

    def _race_once(self: TeamAdminService, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            repo.update(lambda state: state)
        return original(self, **kwargs)

    monkeypatch.setattr(TeamAdminService, "create_git_work_context_with_id", _race_once)
    repo_cloner = FakeRepoCloner()
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=repo_cloner,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(result["dispatches"]) == 1
    assert calls == 2
    assert len(repo_cloner.calls) == 1
    assert len(repo.read().git_work_contexts) == 1
    assert result["transient_conflicts"] == [
        {
            "agent_id": "backend",
            "session_id": "session-backend",
            "dispatch_id": result["dispatches"][0]["dispatch_id"],
            "phase": "git_work_context_commit",
            "attempt_count": 2,
            "conflict_count": 1,
            "resolved": True,
            "retry_scheduled": False,
        }
    ]


def test_wake_due_agents_reschedules_exhausted_state_conflict_without_agent_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
    calls = 0

    def _always_conflict(self: TeamAdminService, **kwargs: object):
        del self
        nonlocal calls
        calls += 1
        expected_revision = int(kwargs["expected_revision"])
        raise TeamStateConflictError(
            expected_revision=expected_revision,
            current_revision=expected_revision + 1,
        )

    monkeypatch.setattr(
        TeamAdminService,
        "create_git_work_context_with_id",
        _always_conflict,
    )
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["dispatches"] == []
    assert calls == 3
    assert result["retry_scheduled_count"] == 1
    assert len(result["retry_event_ids"]) == 1
    assert result["transient_conflicts"][0]["resolved"] is False
    assert result["transient_conflicts"][0]["retry_scheduled"] is True
    assert result["transient_conflicts"][0]["retry_event_ids"] == result["retry_event_ids"]
    assert result["failed_project_repo_preparations"] == []
    assert resumer.calls == []
    reread = repo.read()
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert runtime.next_wake_at is None
    assert runtime.no_progress_count == 0
    assert reread.action_records == {}
    scheduled = json.loads(
        scheduled_internal_event_store_path(tmp_path).read_text(encoding="utf-8")
    )
    retries = [
        record
        for record in scheduled.values()
        if record["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
        and record["payload"]["agent_id"] == "backend"
        and record["payload"]["reason"] == "state_conflict_retry"
    ]
    assert len(retries) == 1


def test_wake_due_agents_provider_passes_project_bare_repo_to_clone_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
            canonical_origin_url="https://example.test/org/Truman.git",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
    captured_effects: list[object] = []

    class CapturingCloneProvider:
        def initialize(self, effect: object, context: object) -> None:
            del context
            captured_effects.append(effect)

        def execute(self) -> dict[str, object]:
            return {
                "success": True,
                "message": "repo prepared",
                "session_id": "session-backend",
                "target_dir": "/home/agent/Truman",
                "guest_repo_name": "Truman",
                "remote_url": "ssh://evander-git-host/git-remotes/Truman.git",
                "pr_notes_ref": "refs/notes/agent-pr/test",
                "execution_backend": "container_worker_vm",
                "vm_name": None,
            }

    monkeypatch.setattr(
        "evander_team_addon.providers.AgentCloneRepoEffectProvider",
        CapturingCloneProvider,
    )
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
            session_metadata_updater=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(captured_effects) == 1
    clone_effect = captured_effects[0]
    assert getattr(clone_effect, "repo_source") == "/opt/repos/Truman"
    assert getattr(clone_effect, "canonical_origin_url") == "https://example.test/org/Truman.git"
    assert getattr(clone_effect, "bare_repo_name") == "Truman.git"
    assert getattr(clone_effect, "bare_repo_path") is None


def test_wake_due_agents_provider_ignores_stale_action_contract_metadata(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "last_known_vm_ip": "192.0.2.44",
        "team_addon": {
            "action_contract": {"seeded": True, "legacy": True},
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
                "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                "agent_id": "backend",
                "display_name": None,
            },
        },
    }
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert "action_contract" not in result["dispatches"][0]
    assert result["dispatches"][0]["team_mcp_tools"]["requested_tools"] == list(
        ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES
    )
    assert file_copier.calls == []
    assert repo.read().agent_runtimes["backend"].active_dispatch_id is not None


def test_wake_due_agents_provider_reseeds_stale_agent_guidance(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {"seeded": True, "legacy": True},
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION - 1,
                "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                "agent_id": "backend",
                "display_name": None,
            },
        }
    }
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert "action_contract" not in result["dispatches"][0]
    assert result["dispatches"][0]["agent_guidance"]["seeded"] is True
    assert len(file_copier.calls) == 1
    copied_file = file_copier.calls[0]["files"][0]
    assert copied_file["destination_path"] == TEAM_AGENT_GUIDANCE_PATH
    assert copied_file["merge_strategy"] == "managed_section"
    assert copied_file["section_id"] == TEAM_AGENT_GUIDANCE_SECTION_ID
    assert copied_file["remove_section_ids"] == list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS)
    assert "a superior may set state: finished on a subordinate non-terminal task" in str(
        copied_file["content"]
    )
    assert "your own full role_description is included in the wake prompt" in str(
        copied_file["content"]
    )
    assert "Use team_get_my_identity when exact identity" in str(copied_file["content"])
    assert (
        session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"]["guidance_version"]
        == TEAM_AGENT_GUIDANCE_VERSION
    )


def test_wake_due_agents_provider_clears_inactive_agent_guidance_metadata(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
                "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                "agent_id": "backend",
                "display_name": None,
                "inactive": True,
                "inactive_reason": "no_team_assignment",
            },
        }
    }
    file_copier = FakeFileCopier()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["dispatches"][0]["agent_guidance"]["seeded"] is True
    assert len(file_copier.calls) == 1
    metadata = session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"]
    assert metadata["inactive"] is False
    assert metadata["inactive_reason"] is None


def test_wake_due_agents_provider_reseeds_agent_guidance_after_display_name_change(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        display_name="Beatrice",
        set_display_name=True,
        expected_revision=repo.read().revision,
    )
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
                "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                "agent_id": "backend",
                "display_name": "Old Backend",
            },
        }
    }
    file_copier = FakeFileCopier()
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert "action_contract" not in result["dispatches"][0]
    assert result["dispatches"][0]["agent_guidance"]["seeded"] is True
    assert result["dispatches"][0]["agent_guidance"]["display_name"] == "Beatrice"
    assert len(file_copier.calls) == 1
    copied_file = file_copier.calls[0]["files"][0]
    assert copied_file["destination_path"] == TEAM_AGENT_GUIDANCE_PATH
    assert copied_file["merge_strategy"] == "managed_section"
    assert copied_file["section_id"] == TEAM_AGENT_GUIDANCE_SECTION_ID
    assert copied_file["remove_section_ids"] == list(LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS)
    assert 'Current display name: "Beatrice"' in str(copied_file["content"])
    prompt = str(resumer.calls[0]["prompt"])
    assert "Beatrice" not in prompt
    assert '"display_name"' not in prompt
    assert (
        session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"]["display_name"]
        == "Beatrice"
    )


def test_wake_due_agents_provider_releases_lease_when_resume_reports_failure(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    resumer = FakeResumer({"success": False, "message": "vm not ready"})
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["leased_count"] == 1
    assert result["dispatches"] == []
    assert result["errors"] == ["backend: vm not ready"]
    reread = TeamStateRepository(tmp_path).read()
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert runtime.no_progress_count == 1
    assert [record.error for record in reread.action_records.values()] == ["vm not ready"]
    assert len(session_store.calls) == 1
    assert "action_contract" not in session_store.calls[0]["metadata"]["team_addon"]
    assert "agent_guidance" in session_store.calls[0]["metadata"]["team_addon"]


def test_wake_due_agents_provider_releases_lease_when_project_repo_prepare_fails(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
        )
        return state

    repo.update(_set_git)
    resumer = FakeResumer()
    session_store = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(success=False, message="repo missing"),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["dispatches"] == []
    assert result["prepared_project_repos"] == []
    reread = TeamStateRepository(tmp_path).read()
    failed_repo_preparation = result["failed_project_repo_preparations"][0]
    assert {
        key: value for key, value in failed_repo_preparation.items() if key != "dispatch_id"
    } == {
        "agent_id": "backend",
        "session_id": "session-backend",
        "project_ids": ["project"],
        "error": "repo missing",
    }
    assert str(failed_repo_preparation["dispatch_id"]).startswith("team-dispatch-")
    assert result["errors"] == ["backend: repo missing"]
    assert resumer.calls == []
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert [record.error for record in reread.action_records.values()] == ["repo missing"]


def test_finalize_agent_dispatch_provider_closes_from_activity_and_publishes_chat_retry(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    dispatch_id = leases[0]["dispatch_id"]
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-finalize-progress",
            tool_name="team_update_task",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id=dispatch_id,
            request_id="request-progress",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="progress",
        )
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please handle this after the current wake.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    queued_dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=queued_dispatch_id,
        reason="agent backend already has an active dispatch",
    )
    session_store = FakeSessionStore()
    provider = TeamFinalizeAgentDispatchEffectProvider()
    provider.initialize(
        TeamFinalizeAgentDispatchEffectDefinition(
            id="finalize",
            display_name="Finalize",
            type="effect:team:finalize-agent-dispatch",
            session_id="session-backend",
            dispatch_id=dispatch_id,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["finalized"] is True
    assert result["progress_made"] is True
    assert result["published_event_ids"]
    assert session_store.calls[-1]["target"] == "session-backend"
    assert session_store.sessions["session-backend"].metadata["team_addon"]["agent_id"] == "backend"
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agent_runtimes["backend"].active_dispatch_id is None
    assert reread.agent_runtimes["backend"].no_progress_count == 0
    assert reread.conversation_messages[message.id].status == "queued"


def test_agent_prompt_completion_hook_finalizes_standalone_team_dispatch(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    dispatch_id = leases[0]["dispatch_id"]
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-finalize-progress",
            tool_name="team_update_task",
            caller_session_id="session-backend",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id=dispatch_id,
            request_id="request-progress",
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="progress",
        )
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please handle this after the current wake.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    queued_dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=queued_dispatch_id,
        reason="agent backend already has an active dispatch",
    )
    session_store = FakeSessionStore()
    record = SimpleNamespace(
        execution_id="exec-standalone-team-prompt",
        effect_type="effect:agent:enqueue-prompt",
        status="succeeded",
        finished_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        outputs={
            "dispatch_id": dispatch_id,
            "session_id": "session-backend",
            "success": True,
        },
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
        provider_factory=SimpleNamespace(admin_service=session_store),
    )

    hook = TeamAgentPromptCompletionFinalizeHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 1
    reread = repo.read()
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert runtime.no_progress_count == 0
    assert session_store.calls
    assert session_store.sessions["session-backend"].metadata["team_addon"]["agent_id"] == "backend"
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    chat_events = [event for event in events if event.event_type == CHAT_MESSAGE_QUEUED_EVENT_TYPE]
    assert len(chat_events) == 1
    assert chat_events[0].payload["dispatch_id"] == queued_dispatch_id
    assert chat_events[0].payload["message_id"] == message.id
    assert hook.on_post_completion_dispatch(worker=worker) == 0


def test_agent_prompt_completion_hook_ignores_queued_retry_marker(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    dispatch_id = leases[0]["dispatch_id"]
    record = SimpleNamespace(
        execution_id="exec-queued-team-prompt-failure",
        effect_type="effect:agent:enqueue-prompt",
        status="failed",
        finished_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        outputs={
            "dispatch_id": dispatch_id,
            "session_id": "session-backend",
            "success": False,
            "queued": True,
            "failed_phase": "dispatch",
            "queue_key": "vm-backend",
        },
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
        queued_agent_prompt_store=SimpleNamespace(read_all=lambda: {}),
        provider_factory=SimpleNamespace(admin_service=FakeSessionStore()),
    )

    hook = TeamAgentPromptCompletionFinalizeHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    runtime = repo.read().agent_runtimes["backend"]
    assert runtime.active_dispatch_id == dispatch_id
    assert runtime.wake_leased_until is not None


def test_deliverable_artifact_publish_reuses_existing_key_without_duplicate_version(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_key = "team/team/project/deliverable-1/dispatch-1/bundle"
    PublishedArtifactStore(tmp_path).publish_single_file(
        key=artifact_key,
        session_id="session-backend",
        filename="report.txt",
        artifact_path="reports/report.txt",
        content=b"report\n",
    )

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("existing Team deliverable artifact should be reused")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    context = EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)})

    first = _publish_deliverable_artifact_bundle(
        context,
        agent={"id": "backend", "session_id": "session-backend"},
        team_id="team",
        project_id="project",
        deliverable_id="deliverable-1",
        dispatch_id="dispatch-1",
        source_paths=["reports/report.txt"],
    )
    second = _publish_deliverable_artifact_bundle(
        context,
        agent={"id": "backend", "session_id": "session-backend"},
        team_id="team",
        project_id="project",
        deliverable_id="deliverable-1",
        dispatch_id="dispatch-1",
        source_paths=["reports/report.txt"],
    )

    assert first["publish_result"]["reused"] is True
    assert second["publish_result"]["reused"] is True
    versions = (
        ArtifactSpaceStore(tmp_path)
        .read()
        .versions_for_space(
            "/teams/team/projects/project/deliverables/deliverable-1",
            artifact_id="bundle",
        )
    )
    assert len(versions) == 1
    assert versions[0].artifact_key == artifact_key


def test_create_operator_directive_effect_wakes_manager(tmp_path: Path) -> None:
    _seed_team(tmp_path)
    provider = TeamCreateOperatorDirectiveEffectProvider()
    provider.initialize(
        TeamCreateOperatorDirectiveEffectDefinition(
            id="directive",
            display_name="Create operator directive",
            type="effect:team:create-operator-directive",
            team_id="team",
            project_id="project",
            message="Ask the lead for a status report.",
            title="Status report",
            source="discord",
            source_message_id="discord-msg-1",
            created_by="operator:jonathan",
            requesting_session_id="session-manager",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["recipient_agent_id"] == "manager"
    assert result["team_id"] == "team"
    assert result["project_id"] == "project"
    assert result["error"] is None
    reread = TeamStateRepository(tmp_path).read()
    item = reread.inbox[result["inbox_item_id"]]
    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.payload["message"] == "Ask the lead for a status report."
    assert reread.agent_runtimes["manager"].next_wake_at is None


def test_create_task_effect_creates_operator_authored_task(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_case(
        case_id="case",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        expected_revision=repo.read().revision,
    )
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="create-task",
            display_name="Create task",
            type="effect:team:create-task",
            team_id="team",
            project_id="project",
            owner_agent_id="backend",
            priority=2,
            title="Investigate ticket",
            description="Inspect the customer ticket and summarize next steps.",
            task_id="workflow-task",
            case_id="case",
            state="pending",
            parent_task_id="task",
            dependency_task_ids=["task"],
            created_by="workflow:intake",
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created"] is True
    assert result["task_id"] == "workflow-task"
    assert result["team_id"] == "team"
    assert result["project_id"] == "project"
    assert result["owner_agent_id"] == "backend"
    assert result["task"]["created_by_actor"] == "workflow:intake"
    reread = TeamStateRepository(tmp_path).read()
    task = reread.tasks["workflow-task"]
    assert task.case_id == "case"
    assert task.parent_task_id == "task"
    assert task.dependency_task_ids == ["task"]
    assert reread.cases["case"].task_ids == ["workflow-task"]


def test_create_task_effect_reports_generated_id_from_atomic_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_team(tmp_path)

    class RacingTaskAdminService(TeamAdminService):
        injected = False

        def create_or_reprioritize_task_with_result(self, **kwargs) -> tuple[TeamState, str, bool]:
            if not self.__class__.injected:
                self.__class__.injected = True
                super().create_or_reprioritize_task_with_result(
                    task_id=None,
                    owner_agent_id="backend",
                    project_id="project",
                    priority=1,
                    title="AAA competing task",
                    description="Created after the provider pre-read.",
                    created_by_actor="workflow:competitor",
                )
            return super().create_or_reprioritize_task_with_result(**kwargs)

    monkeypatch.setattr(
        "evander_team_addon.providers.TeamAdminService",
        RacingTaskAdminService,
    )
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="create-task",
            display_name="Create task",
            type="effect:team:create-task",
            team_id="team",
            project_id="project",
            owner_agent_id="backend",
            priority=2,
            title="ZZZ workflow task",
            description="Task created by the provider under test.",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created"] is True
    assert result["task"]["title"] == "ZZZ workflow task"
    reread = TeamStateRepository(tmp_path).read()
    competing_ids = [
        task.id for task in reread.tasks.values() if task.title == "AAA competing task"
    ]
    workflow_ids = [task.id for task in reread.tasks.values() if task.title == "ZZZ workflow task"]
    assert competing_ids
    assert workflow_ids == [result["task_id"]]
    assert result["task_id"] != competing_ids[0]


def test_create_task_effect_updates_existing_task(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="create-task",
            display_name="Create task",
            type="effect:team:create-task",
            team_id="team",
            project_id="project",
            owner_agent_id="backend",
            priority=7,
            title="Updated backend task",
            description="Updated workflow instructions.",
            task_id="task",
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created"] is False
    assert result["task_id"] == "task"
    reread = TeamStateRepository(tmp_path).read()
    task = reread.tasks["task"]
    assert task.priority == 7
    assert task.title == "Updated backend task"
    assert task.description == "Updated workflow instructions."
    assert task.owner_agent_id == "backend"
    assert task.project_id == "project"


def test_create_task_effect_rejects_project_team_mismatch(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="other-team",
        name="Other team",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="create-task",
            display_name="Create task",
            type="effect:team:create-task",
            team_id="other-team",
            project_id="project",
            owner_agent_id="backend",
            priority=1,
            title="Invalid task",
            description="Should not be created.",
            task_id="invalid-task",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is False
    assert "project project does not belong to team other-team" in result["error"]
    assert "invalid-task" not in TeamStateRepository(tmp_path).read().tasks


def test_create_task_effect_returns_revision_conflict(tmp_path: Path) -> None:
    _seed_team(tmp_path)
    provider = TeamCreateTaskEffectProvider()
    provider.initialize(
        TeamCreateTaskEffectDefinition(
            id="create-task",
            display_name="Create task",
            type="effect:team:create-task",
            team_id="team",
            project_id="project",
            owner_agent_id="backend",
            priority=1,
            title="Conflicting task",
            description="Should not be created.",
            task_id="conflicting-task",
            expected_revision=0,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["task_id"] == "conflicting-task"
    assert result["expected_revision"] == 0
    assert result["current_revision"] == TeamStateRepository(tmp_path).read().revision
    assert "conflicting-task" not in TeamStateRepository(tmp_path).read().tasks


def test_create_case_effect_links_existing_tasks(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    provider = TeamCreateCaseEffectProvider()
    provider.initialize(
        TeamCreateCaseEffectDefinition(
            id="create-case",
            display_name="Create case",
            type="effect:team:create-case",
            case_id="case",
            team_id="team",
            project_id="project",
            title="Customer ticket",
            description="Investigate the accounting ticket.",
            status="triage",
            priority="high",
            owner_agent_id="backend",
            source="zendesk",
            source_ref="ZD-42",
            tags=["accounting", "vat"],
            metadata={"external_url": "https://tickets.example/ZD-42"},
            task_ids=["task"],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["case_id"] == "case"
    assert result["team_id"] == "team"
    assert result["project_id"] == "project"
    assert result["task_ids"] == ["task"]
    assert result["case"]["source_ref"] == "ZD-42"
    reread = TeamStateRepository(tmp_path).read()
    case = reread.cases["case"]
    assert case.status == TeamCaseStatus.TRIAGE
    assert case.priority == TeamInboxPriority.HIGH
    assert case.task_ids == ["task"]
    assert reread.tasks["task"].case_id == "case"


def test_create_case_effect_returns_generated_id_when_other_case_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_team(tmp_path)

    class RacingCaseAdminService(TeamAdminService):
        injected = False

        def create_case_with_id(self, **kwargs) -> tuple[TeamState, str]:
            if not self.__class__.injected:
                self.__class__.injected = True
                super().create_case(
                    case_id="external-case",
                    team_id="team",
                    title="External intake",
                )
            return super().create_case_with_id(**kwargs)

    monkeypatch.setattr(
        "evander_team_addon.providers.TeamAdminService",
        RacingCaseAdminService,
    )
    provider = TeamCreateCaseEffectProvider()
    provider.initialize(
        TeamCreateCaseEffectDefinition(
            id="create-case",
            display_name="Create case",
            type="effect:team:create-case",
            team_id="team",
            title="Generated intake",
            source="email",
            source_ref="message-42",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["case_id"]
    assert result["case_id"] != "external-case"
    assert result["case"]["title"] == "Generated intake"
    reread = TeamStateRepository(tmp_path).read()
    assert "external-case" in reread.cases
    assert result["case_id"] in reread.cases


def test_update_case_effect_clears_optional_fields_and_task_links(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_case(
        case_id="case",
        team_id="team",
        project_id="project",
        title="Customer ticket",
        description="Initial details",
        owner_agent_id="backend",
        source="zendesk",
        source_ref="ZD-42",
        tags=["accounting"],
        metadata={"external_url": "https://tickets.example/ZD-42"},
        task_ids=["task"],
        expected_revision=repo.read().revision,
    )
    provider = TeamUpdateCaseEffectProvider()
    provider.initialize(
        TeamUpdateCaseEffectDefinition(
            id="update-case",
            display_name="Update case",
            type="effect:team:update-case",
            case_id="case",
            title="Accounting ticket",
            description="",
            project_id=None,
            status="waiting",
            priority="low",
            owner_agent_id=None,
            source_ref=None,
            tags=[],
            metadata={},
            task_ids=[],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["case_id"] == "case"
    assert result["project_id"] is None
    assert result["task_ids"] == []
    reread = TeamStateRepository(tmp_path).read()
    case = reread.cases["case"]
    assert case.title == "Accounting ticket"
    assert case.description == ""
    assert case.project_id is None
    assert case.status == TeamCaseStatus.WAITING
    assert case.priority == TeamInboxPriority.LOW
    assert case.owner_agent_id is None
    assert case.source == "zendesk"
    assert case.source_ref is None
    assert case.tags == []
    assert case.metadata == {}
    assert case.task_ids == []
    assert reread.tasks["task"].case_id is None


def test_session_routing_ignores_non_team_dispatch_for_persistent_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    team_dispatch_id = leases[0]["dispatch_id"]

    routing = TeamSessionRoutingQueryProvider()
    routing.initialize(
        TeamSessionRoutingQueryDefinition(
            id="route",
            display_name="Route",
            type="query:team:session-routing",
            session_id="session-backend",
            dispatch_id="wake-123-session-backend",
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = routing.execute()

    assert result["success"] is True
    assert result["kind"] == "ignored"
    assert result["persistent"] is False
    assert result["ignored"] is True
    assert result["reason"] == "dispatch_id does not match active team runtime lease"
    assert result["expected_dispatch_id"] == team_dispatch_id


def test_session_routing_ignores_missing_dispatch_for_active_team_lease(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    team_dispatch_id = leases[0]["dispatch_id"]

    routing = TeamSessionRoutingQueryProvider()
    routing.initialize(
        TeamSessionRoutingQueryDefinition(
            id="route",
            display_name="Route",
            type="query:team:session-routing",
            session_id="session-backend",
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = routing.execute()

    assert result["success"] is True
    assert result["kind"] == "ignored"
    assert result["dispatch_id"] is None
    assert result["persistent"] is False
    assert result["ignored"] is True
    assert result["reason"] == "dispatch_id does not match active team runtime lease"
    assert result["expected_dispatch_id"] == team_dispatch_id


def test_session_routing_accepts_active_team_dispatch_for_persistent_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    team_dispatch_id = leases[0]["dispatch_id"]

    routing = TeamSessionRoutingQueryProvider()
    routing.initialize(
        TeamSessionRoutingQueryDefinition(
            id="route",
            display_name="Route",
            type="query:team:session-routing",
            session_id="session-backend",
            dispatch_id=team_dispatch_id,
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = routing.execute()

    assert result["success"] is True
    assert result["kind"] == "persistent"
    assert result["persistent"] is True
    assert result["ignored"] is False
    assert result["expected_dispatch_id"] == team_dispatch_id


def test_ephemeral_lifecycle_collects_action_routes_parent_and_cleans_up(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)
    state, request_result = _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "title": "Draft implementation",
                "prompt": "Implement a narrow backend slice.",
            }
        ],
    )
    assignment = state.ephemeral_assignments[request_result["created_ephemeral_assignment_ids"][0]]
    assert assignment.execution_backend == "container_worker_vm"
    assert request_result["events"][0]["payload"]["execution_backend"] == "container_worker_vm"

    mark_spawned = TeamMarkEphemeralSpawnedEffectProvider()
    mark_spawned.initialize(
        TeamMarkEphemeralSpawnedEffectDefinition(
            id="spawned",
            display_name="Spawned",
            type="effect:team:mark-ephemeral-spawned",
            assignment_id=assignment.id,
            session_id=assignment.session_id,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    assert mark_spawned.execute()["state"] == "running"

    routing = TeamSessionRoutingQueryProvider()
    routing.initialize(
        TeamSessionRoutingQueryDefinition(
            id="route",
            display_name="Route",
            type="query:team:session-routing",
            session_id=assignment.session_id,
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    assert routing.execute()["assignment_id"] == assignment.id

    _state, result = _command_service(repo).report_ephemeral_result_command(
        session_id=assignment.session_id,
        assignment_id=assignment.id,
        summary="Ephemeral work is ready.",
        artifact_refs=["artifact-space:/teams/team/ephemeral/result"],
        request_id="request-ephemeral-result",
    )
    assert result["success"] is True
    assert result["state"] == "completed"

    reread = TeamStateRepository(tmp_path).read()
    assert reread.ephemeral_assignments[assignment.id].summary == "Ephemeral work is ready."
    inbox_items = list(reread.inbox.values())
    assert inbox_items[-1].recipient_agent_id == "backend"
    assert inbox_items[-1].payload["ephemeral_assignment_id"] == assignment.id
    completed_outbox = repo.list_outbox_records(
        intent_type="team.ephemeral.completed",
        aggregate_type="ephemeral_assignment",
        aggregate_id=assignment.id,
    )
    assert len(completed_outbox) == 1

    cleanup = TeamMarkEphemeralCleanedUpEffectProvider()
    cleanup.initialize(
        TeamMarkEphemeralCleanedUpEffectDefinition(
            id="cleanup",
            display_name="Cleanup",
            type="effect:team:mark-ephemeral-cleaned-up",
            assignment_id=assignment.id,
            session_id=assignment.session_id,
            destroy_result={"success": True},
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    assert cleanup.execute()["state"] == "cleaned_up"

    _state, parent_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        now=datetime(2099, 1, 1, tzinfo=UTC),
    )
    assert parent_leases
    _state, acceptance = _command_service(repo).accept_ephemeral_result_command(
        session_id="session-backend",
        assignment_id=assignment.id,
        deliverable_id="deliverable-ephemeral",
        request_id="request-ephemeral-accept",
    )
    assert acceptance["success"] is True
    assert acceptance["assignment_id"] == assignment.id
    final = TeamStateRepository(tmp_path).read()
    assert final.deliverables["deliverable-ephemeral"].producer_agent_id == "backend"


def test_mark_ephemeral_spawned_records_create_failure(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)
    state, request_result = _team_ephemeral_service(repo).request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "title": "Draft implementation",
                "prompt": "Implement a scoped backend slice.",
            }
        ],
    )
    assignment = state.ephemeral_assignments[request_result["created_ephemeral_assignment_ids"][0]]
    mark_spawned = TeamMarkEphemeralSpawnedEffectProvider()
    mark_spawned.initialize(
        TeamMarkEphemeralSpawnedEffectDefinition(
            id="spawned",
            display_name="Spawned",
            type="effect:team:mark-ephemeral-spawned",
            assignment_id=assignment.id,
            session_id=assignment.session_id,
            create_result={"success": False, "message": "create failed"},
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = mark_spawned.execute()

    assert result["success"] is False
    assert result["state"] == "failed"
    assert result["error"] == "create failed"
    reread = TeamStateRepository(tmp_path).read()
    assert reread.ephemeral_assignments[assignment.id].state.value == "failed"
    assert reread.ephemeral_assignments[assignment.id].error == "create failed"


def test_team_state_query_filters_scoped_dependent_records(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    state = _multi_team_state()
    state.agents["alpha-dev"].voice_profile = AgentVoiceProfile(
        voice="cedar",
        speaking_style="agent-visible state should not include this",
    )
    state.cases["alpha-case"] = TeamCase(
        id="alpha-case",
        team_id="alpha-team",
        project_id="alpha-project",
        title="Alpha case",
        owner_agent_id="alpha-dev",
        source="github",
        source_ref="issue-1",
        task_ids=["alpha-task"],
    )
    state.tasks["alpha-task"].case_id = "alpha-case"
    state.cases["beta-case"] = TeamCase(
        id="beta-case",
        team_id="beta-team",
        project_id="beta-project",
        title="Beta case",
        owner_agent_id="beta-dev",
        task_ids=["beta-task"],
    )
    state.tasks["beta-task"].case_id = "beta-case"
    repo.store.write_object(state.model_dump(mode="json"))
    session_lookup = SimpleNamespace(
        read_all_sessions=lambda: {
            "session-alpha-dev": SimpleNamespace(
                session_id="session-alpha-dev",
                expires_at=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            "session-beta-dev": SimpleNamespace(
                session_id="session-beta-dev",
                expires_at=datetime(2025, 12, 31, tzinfo=UTC),
            ),
        }
    )
    project_query = TeamStateQueryProvider()
    project_query.initialize(
        TeamStateQueryDefinition(
            id="state",
            display_name="State",
            type="query:team:state",
            project_id="alpha-project",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_lookup=session_lookup,
        ),
    )

    project_state = project_query.execute()["state"]

    assert set(project_state["teams"]) == {"alpha-team"}
    assert set(project_state["projects"]) == {"alpha-project"}
    assert set(project_state["agents"]) == {"alpha-lead", "alpha-dev", "alpha-reviewer"}
    assert "voice_profile" not in project_state["agents"]["alpha-dev"]
    assert set(project_state["agent_runtimes"]) == {"alpha-dev"}
    assert set(project_state["cases"]) == {"alpha-case"}
    assert set(project_state["tasks"]) == {"alpha-task", "alpha-helper"}
    assert project_state["tasks"]["alpha-task"]["case_id"] == "alpha-case"
    # Parent/dependency stay intact when both tasks are within scope.
    assert project_state["tasks"]["alpha-task"]["parent_task_id"] == "alpha-helper"
    assert project_state["tasks"]["alpha-task"]["dependency_task_ids"] == ["alpha-helper"]
    assert project_state["tasks"]["alpha-helper"]["child_task_ids"] == ["alpha-task"]
    assert set(project_state["deliverables"]) == {"alpha-deliverable"}
    assert set(project_state["review_requests"]) == {"alpha-review"}
    assert set(project_state["inbox"]) == {"alpha-inbox"}
    assert set(project_state["action_records"]) == {"alpha-action"}
    assert set(project_state["conversations"]) == {"conversation-alpha"}
    assert set(project_state["conversation_messages"]) == {"message-alpha"}
    assert set(project_state["release_packages"]) == {"alpha-release"}
    assert set(project_state["git_promotions"]) == {
        "alpha-promotion",
        "alpha-release-promotion",
    }
    assert set(project_state["git_work_contexts"]) == {"alpha-context"}
    assert project_state["git_work_contexts"]["alpha-context"]["worktree_path"] == (
        "/worktrees/alpha"
    )
    assert project_state["release_packages"]["alpha-release"]["git_promotion_record_ids"] == [
        "alpha-release-promotion"
    ]
    assert set(project_state["ephemeral_assignments"]) == {"alpha-ephemeral"}
    project_runtime_operations = project_state["runtime_operations"]
    assert {row["id"] for row in project_runtime_operations["agents"]} == {
        "alpha-dev",
        "alpha-lead",
        "alpha-reviewer",
    }
    assert {
        row["inbox_item_id"]
        for row in project_runtime_operations["attention"]
        if row.get("inbox_item_id")
    } == {"alpha-inbox"}
    assert {
        row["agent_id"]
        for row in project_runtime_operations["attention"]
        if row.get("kind") == "expired_agent_session"
    } == {"alpha-dev"}
    assert project_runtime_operations["session_health"] == [
        {
            "agent_id": "alpha-dev",
            "team_id": "alpha-team",
            "project_ids": ["alpha-project"],
            "lead_project_ids": [],
            "role_id": "alpha-project__developer",
            "session_id": "session-alpha-dev",
            "session_present": True,
            "expires_at": "2025-12-31T00:00:00+00:00",
            "is_expired": True,
            "status": "expired",
        },
        {
            "agent_id": "alpha-lead",
            "team_id": "alpha-team",
            "project_ids": ["alpha-project"],
            "lead_project_ids": ["alpha-project"],
            "role_id": "alpha-project__project-lead",
            "session_id": "session-alpha-lead",
            "session_present": False,
            "expires_at": None,
            "is_expired": False,
            "status": "missing",
        },
        {
            "agent_id": "alpha-reviewer",
            "team_id": "alpha-team",
            "project_ids": ["alpha-project"],
            "lead_project_ids": [],
            "role_id": "alpha-project__reviewer",
            "session_id": "session-alpha-reviewer",
            "session_present": False,
            "expires_at": None,
            "is_expired": False,
            "status": "missing",
        },
    ]
    assert all(
        "beta-" not in json.dumps(row)
        for row in (
            project_runtime_operations["agents"]
            + project_runtime_operations["attention"]
            + project_runtime_operations["session_health"]
        )
    )

    agent_query = TeamStateQueryProvider()
    agent_query.initialize(
        TeamStateQueryDefinition(
            id="state-agent",
            display_name="State Agent",
            type="query:team:state",
            agent_id="alpha-dev",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_lookup=session_lookup,
        ),
    )

    agent_state = agent_query.execute()["state"]

    assert set(agent_state["teams"]) == {"alpha-team"}
    assert set(agent_state["projects"]) == {"alpha-project"}
    assert set(agent_state["agents"]) == {"alpha-dev"}
    assert "voice_profile" not in agent_state["agents"]["alpha-dev"]
    assert set(agent_state["agent_runtimes"]) == {"alpha-dev"}
    assert set(agent_state["cases"]) == {"alpha-case"}
    assert set(agent_state["tasks"]) == {"alpha-task"}
    # alpha-helper is out of this agent's scope, so its edges are filtered out,
    # but dependency_status still reflects global truth (alpha-helper is pending).
    scoped_task = agent_state["tasks"]["alpha-task"]
    assert scoped_task["case_id"] == "alpha-case"
    assert scoped_task["parent_task_id"] is None
    assert scoped_task["dependency_task_ids"] == []
    assert scoped_task["blocked_by_task_ids"] == []
    assert scoped_task["child_task_ids"] == []
    assert scoped_task["dependency_status"] == "waiting"
    assert set(agent_state["deliverables"]) == {"alpha-deliverable"}
    assert set(agent_state["review_requests"]) == set()
    assert set(agent_state["inbox"]) == {"alpha-inbox"}
    assert set(agent_state["action_records"]) == {"alpha-action"}
    assert set(agent_state["conversations"]) == {"conversation-alpha"}
    assert set(agent_state["conversation_messages"]) == {"message-alpha"}
    assert set(agent_state["release_packages"]) == {"alpha-release"}
    assert set(agent_state["git_promotions"]) == {
        "alpha-promotion",
        "alpha-release-promotion",
    }
    assert set(agent_state["git_work_contexts"]) == {"alpha-context"}
    assert agent_state["git_work_contexts"]["alpha-context"]["agent_branch"] == (
        "agents/session-alpha-dev/work-session-alpha-dev"
    )
    assert agent_state["release_packages"]["alpha-release"]["git_promotion_record_ids"] == [
        "alpha-release-promotion"
    ]
    assert set(agent_state["ephemeral_assignments"]) == {"alpha-ephemeral"}
    agent_runtime_operations = agent_state["runtime_operations"]
    assert {row["id"] for row in agent_runtime_operations["agents"]} == {"alpha-dev"}
    assert {
        row["inbox_item_id"]
        for row in agent_runtime_operations["attention"]
        if row.get("inbox_item_id")
    } == {"alpha-inbox"}
    assert {
        row["agent_id"]
        for row in agent_runtime_operations["attention"]
        if row.get("kind") == "expired_agent_session"
    } == {"alpha-dev"}
    assert [row["agent_id"] for row in agent_runtime_operations["session_health"]] == ["alpha-dev"]
    assert all(
        "beta-" not in json.dumps(row)
        for row in (
            agent_runtime_operations["agents"]
            + agent_runtime_operations["attention"]
            + agent_runtime_operations["session_health"]
        )
    )


def test_team_state_query_keeps_multi_project_expired_session_attention(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    state = _multi_team_state()
    state.teams["alpha-team"].project_ids.append("alpha-maintenance")
    state.projects["alpha-maintenance"] = Project(
        id="alpha-maintenance",
        team_id="alpha-team",
        name="Alpha Maintenance",
        lead_agent_id="alpha-lead",
    )
    state.roles["alpha-maintenance__project-lead"] = TeamRole(
        id="alpha-maintenance__project-lead",
        team_id="alpha-team",
        project_id="alpha-maintenance",
        name="Project Lead",
    )
    state.agents["alpha-lead"].role_id = "alpha-maintenance__project-lead"
    state.project_assignments["alpha-maintenance:alpha-dev"] = AgentProjectAssignment(
        id="alpha-maintenance:alpha-dev",
        team_id="alpha-team",
        project_id="alpha-maintenance",
        agent_id="alpha-dev",
    )
    repo.store.write_object(state.model_dump(mode="json"))
    session_lookup = SimpleNamespace(
        read_all_sessions=lambda: {
            "session-alpha-dev": SimpleNamespace(
                session_id="session-alpha-dev",
                expires_at=datetime(2025, 12, 31, tzinfo=UTC),
            ),
        }
    )
    query = TeamStateQueryProvider()
    query.initialize(
        TeamStateQueryDefinition(
            id="state-maintenance",
            display_name="State Maintenance",
            type="query:team:state",
            project_id="alpha-maintenance",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_lookup=session_lookup,
        ),
    )

    scoped_state = query.execute()["state"]

    runtime_operations = scoped_state["runtime_operations"]
    expired_rows = [
        row for row in runtime_operations["attention"] if row.get("kind") == "expired_agent_session"
    ]
    assert [row["agent_id"] for row in expired_rows] == ["alpha-dev"]
    assert expired_rows[0]["project_id"] is None
    assert set(expired_rows[0]["project_ids"]) == {
        "alpha-maintenance",
        "alpha-project",
    }
    assert any(
        row["agent_id"] == "alpha-dev"
        and set(row["project_ids"]) == {"alpha-maintenance", "alpha-project"}
        for row in runtime_operations["session_health"]
    )


def test_team_runtime_pack_wires_ephemeral_event_flow() -> None:
    pack = _team_runtime_sections()
    triggers = {node["id"]: node for node in pack["triggers"]}
    effects = {node["id"]: node for node in pack["effects"]}
    queries = {node["id"]: node for node in pack["queries"]}
    gates = {node["id"]: node for node in pack["gates"]}
    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}

    assert triggers["team-ephemeral-requested"]["type"] == "trigger:event"
    assert triggers["team-ephemeral-requested"]["event_type"] == "team.ephemeral.requested"
    assert effects["team-create-ephemeral-agent"]["type"] == "effect:agent:create"
    assert effects["team-create-ephemeral-agent"]["execution_backend"] == (
        "${{team-ephemeral-requested.payload.execution_backend}}"
    )
    assert effects["team-create-ephemeral-agent"]["vm_name"] == (
        "${{team-ephemeral-requested.payload.vm_name ?? "
        "team-ephemeral-requested.payload.session_id}}"
    )
    assert effects["team-mark-ephemeral-spawned"]["create_result"]["success"] == (
        "${{team-create-ephemeral-agent.success}}"
    )
    assert effects["team-prompt-ephemeral-agent"]["type"] == "effect:agent:prompt"
    assert effects["team-destroy-ephemeral-agent"]["type"] == "effect:agent:destroy"
    assert effects["team-destroy-ephemeral-agent"]["session_id"] == (
        "${{team-route-completed-session.session_id}}"
    )
    assert effects["team-mark-ephemeral-cleaned-up"]["type"] == (
        "effect:team:mark-ephemeral-cleaned-up"
    )
    assert effects["team-mark-ephemeral-cleaned-up"]["session_id"] == (
        "${{team-route-completed-session.session_id}}"
    )
    assert "team-read-ephemeral-action" not in queries
    assert "team-apply-ephemeral-action" not in effects
    assert queries["team-route-completed-session"]["type"] == "query:team:session-routing"
    assert queries["team-route-completed-session"]["dispatch_id"] == (
        "${{team-agent-run-completed.dispatch_id ?? null}}"
    )
    assert gates["team-completed-ephemeral"]["when"]["all"][0] == {
        "equals": True,
        "left": "${{team-route-completed-session.ephemeral}}",
    }
    assert gates["team-ephemeral-spawned"]["when"]["all"][0] == {
        "equals": True,
        "left": "${{team-mark-ephemeral-spawned.success}}",
    }
    assert ("team-ephemeral-requested", "team-create-ephemeral-agent") in edges
    assert ("team-create-ephemeral-agent", "team-mark-ephemeral-spawned") in edges
    assert ("team-mark-ephemeral-spawned", "team-ephemeral-spawned") in edges
    assert ("team-ephemeral-spawned", "team-prompt-ephemeral-agent") in edges
    assert ("team-mark-ephemeral-spawned", "team-prompt-ephemeral-agent") not in edges
    assert ("team-completed-ephemeral", "team-destroy-ephemeral-agent") in edges
    assert ("team-read-ephemeral-action", "team-apply-ephemeral-action") not in edges
    assert ("team-destroy-ephemeral-agent", "team-mark-ephemeral-cleaned-up") in edges


def test_team_runtime_pack_flushes_outbox_after_persistent_dispatch() -> None:
    pack = _team_runtime_sections()
    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}

    assert ("team-cron", "team-publish-outbox-events") in edges
    assert (
        "team-finalize-persistent-dispatch",
        "team-publish-outbox-after-dispatch",
    ) in edges


def test_team_runtime_pack_routes_operator_escalation_to_communication() -> None:
    pack = _team_runtime_sections()
    triggers = {node["id"]: node for node in pack["triggers"]}
    effects = {node["id"]: node for node in pack["effects"]}
    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}

    assert triggers["team-operator-escalation"]["type"] == "trigger:event"
    assert triggers["team-operator-escalation"]["event_type"] == (
        "team.operator.escalation_requested"
    )
    assert triggers["team-chat-message-queued"]["type"] == "trigger:event"
    assert triggers["team-chat-message-queued"]["event_type"] == ("team.chat.message_queued")
    assert triggers["team-agent-wake-requested"]["type"] == "trigger:event"
    assert triggers["team-agent-wake-requested"]["event_type"] == ("team.agent.wake_requested")
    assert effects["team-wake-queued-chat-message"]["type"] == ("effect:team:wake-due-agents")
    assert effects["team-wake-queued-chat-message"]["agent_id"] == (
        "${{team-chat-message-queued.payload.agent_id}}"
    )
    assert effects["team-wake-queued-chat-message"]["limit"] == 1
    assert effects["team-wake-requested-agent"]["type"] == ("effect:team:wake-due-agents")
    assert effects["team-wake-requested-agent"]["agent_id"] == (
        "${{team-agent-wake-requested.payload.agent_id}}"
    )
    assert effects["team-wake-requested-agent"]["limit"] == 1
    assert effects["team-wake-requested-agent"]["respect_wake_interval"] is False

    notify = effects["team-notify-operator"]
    assert notify["type"] == "effect:communication:send-message"
    assert notify["communication_target_id"] == (
        "${{team-operator-escalation.payload.communication_target_id}}"
    )
    assert "${{team-operator-escalation.payload.title}}" in notify["message"]
    assert "${{team-operator-escalation.payload.message}}" in notify["message"]
    assert "${{team-operator-escalation.payload.urgency}}" in notify["message"]
    assert ("team-operator-escalation", "team-notify-operator") in edges
    assert ("team-chat-message-queued", "team-wake-queued-chat-message") in edges
    assert ("team-agent-wake-requested", "team-wake-requested-agent") in edges


def test_team_event_publisher_schedules_delayed_wake_events(tmp_path: Path) -> None:
    context = EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)})

    published_ids = _publish_team_events(
        context,
        [
            {
                "event_type": AGENT_WAKE_REQUESTED_EVENT_TYPE,
                "correlation_key": "team.agent.wake_requested:backend",
                "idempotency_key": "team-addon:team.agent.wake_requested:backend:1",
                "delay_seconds": 10,
                "payload": {
                    "team_id": "team",
                    "agent_id": "backend",
                    "session_id": "session-backend",
                    "reason": "inbox",
                },
            }
        ],
    )

    assert published_ids and published_ids[0].startswith("sched-")
    assert InternalEventBus(internal_event_store_path(tmp_path)).list_events() == []
    scheduled = json.loads(
        scheduled_internal_event_store_path(tmp_path).read_text(encoding="utf-8")
    )
    assert len(scheduled) == 1
    [record] = scheduled.values()
    assert record["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    assert record["status"] == "pending"
    assert record["payload"]["agent_id"] == "backend"


def test_team_runtime_pack_routes_git_promotion_kinds() -> None:
    pack = _team_runtime_sections()
    triggers = {node["id"]: node for node in pack["triggers"]}
    effects = {node["id"]: node for node in pack["effects"]}
    gates = {node["id"]: node for node in pack["gates"]}
    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}

    assert triggers["team-git-promotion-requested"]["type"] == "trigger:event"
    assert triggers["team-git-promotion-requested"]["event_type"] == (
        "team.git.promotion_requested"
    )

    # Each promotion_kind is split off the trigger with its own filter gate.
    kind_gates = {
        "review": "team-git-promotion-review",
        "feature": "team-git-promotion-feature",
        "release": "team-git-promotion-release",
        "canonical_push": "team-git-promotion-canonical",
    }
    for kind, gate_id in kind_gates.items():
        assert gates[gate_id]["type"] == "gate:filter"
        assert gates[gate_id]["when"]["all"][0] == {
            "equals": kind,
            "left": "${{team-git-promotion-requested.payload.promotion_kind}}",
        }
        assert ("team-git-promotion-requested", gate_id) in edges

    # review -> publish-review-branch -> record-git-promotion
    promote = effects["team-git-promote-review-branch"]
    assert promote["type"] == "effect:team:publish-review-branch"
    assert promote["source_branch"] == (
        "${{team-git-promotion-requested.payload.source_branch ?? null}}"
    )
    assert promote["commit_sha"] == "${{team-git-promotion-requested.payload.commit_sha ?? null}}"
    assert promote["target_branch"] == "${{team-git-promotion-requested.payload.target_branch}}"
    assert effects["team-record-review-promotion"]["type"] == "effect:team:record-git-promotion"
    assert effects["team-record-review-promotion"]["promotion_id"] == (
        "${{team-git-promotion-requested.payload.promotion_id}}"
    )
    assert effects["team-record-review-promotion"]["success"] == (
        "${{team-git-promote-review-branch.success}}"
    )
    assert ("team-git-promotion-review", "team-git-promote-review-branch") in edges
    assert ("team-git-promote-review-branch", "team-record-review-promotion") in edges

    # feature -> consolidate -> record-git-promotion
    assert effects["team-consolidate-feature-branches"]["type"] == (
        "effect:team:consolidate-git-branches"
    )
    assert effects["team-consolidate-feature-branches"]["source_branches"] == (
        "${{team-git-promotion-requested.payload.source_branches}}"
    )
    assert effects["team-consolidate-feature-branches"]["bare_repo_path"] == (
        "${{team-git-promotion-requested.payload.bare_repo_path ?? null}}"
    )
    assert effects["team-consolidate-feature-branches"]["bare_repo_name"] == (
        "${{team-git-promotion-requested.payload.bare_repo_name ?? null}}"
    )
    assert effects["team-record-feature-promotion"]["type"] == ("effect:team:record-git-promotion")
    assert ("team-git-promotion-feature", "team-consolidate-feature-branches") in edges
    assert ("team-consolidate-feature-branches", "team-record-feature-promotion") in edges

    # release -> consolidate -> record-git-promotion
    assert effects["team-consolidate-release-branches"]["type"] == (
        "effect:team:consolidate-git-branches"
    )
    assert effects["team-consolidate-release-branches"]["bare_repo_path"] == (
        "${{team-git-promotion-requested.payload.bare_repo_path ?? null}}"
    )
    assert effects["team-consolidate-release-branches"]["bare_repo_name"] == (
        "${{team-git-promotion-requested.payload.bare_repo_name ?? null}}"
    )
    assert effects["team-record-release-promotion"]["type"] == ("effect:team:record-git-promotion")
    assert ("team-git-promotion-release", "team-consolidate-release-branches") in edges
    assert ("team-consolidate-release-branches", "team-record-release-promotion") in edges

    # release recovery -> promote agent ref -> record release promotion
    recovery_promote = effects["team-promote-release-recovery-branch"]
    assert recovery_promote["type"] == "effect:git:promote-agent-branch"
    assert recovery_promote["agent_branch"] == (
        "${{team-git-release-recovery-requested.payload.agent_branch}}"
    )
    assert recovery_promote["target_branch"] == (
        "${{team-git-release-recovery-requested.payload.target_branch}}"
    )
    assert recovery_promote["expected_commit_sha"] == (
        "${{team-git-release-recovery-requested.payload.expected_commit_sha ?? null}}"
    )
    recovery_record = effects["team-record-release-recovery-promotion"]
    assert recovery_record["type"] == "effect:team:record-git-promotion"
    assert recovery_record["promotion_id"] == (
        "${{team-git-release-recovery-requested.payload.promotion_id}}"
    )
    assert recovery_record["success"] == ("${{team-promote-release-recovery-branch.success}}")
    assert ("team-git-release-recovery-requested", "team-promote-release-recovery-branch") in edges
    assert (
        "team-promote-release-recovery-branch",
        "team-record-release-recovery-promotion",
    ) in edges

    # canonical_push -> push-branch -> record-git-promotion -> mark-release-published
    push = effects["team-push-canonical-branch"]
    assert push["type"] == "effect:git:push-branch"
    assert push["bare_repo_path"] == (
        "${{team-git-promotion-requested.payload.bare_repo_path ?? null}}"
    )
    assert push["bare_repo_name"] == (
        "${{team-git-promotion-requested.payload.bare_repo_name ?? null}}"
    )
    assert push["local_branch"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert push["origin_branch"] == "${{team-git-promotion-requested.payload.target_branch}}"
    record_canonical = effects["team-record-canonical-promotion"]
    assert record_canonical["type"] == "effect:team:record-git-promotion"
    assert record_canonical["promotion_id"] == (
        "${{team-git-promotion-requested.payload.promotion_id}}"
    )
    assert record_canonical["success"] == "${{team-push-canonical-branch.success}}"
    assert record_canonical["commit_sha"] == (
        "${{team-push-canonical-branch.local_commit ?? null}}"
    )
    assert effects["team-mark-release-published"]["type"] == ("effect:team:mark-release-published")
    assert effects["team-mark-release-published"]["release_package_id"] == (
        "${{team-git-promotion-requested.payload.release_package_id}}"
    )
    assert effects["team-mark-release-published"]["published_commit_sha"] == (
        "${{team-push-canonical-branch.local_commit ?? null}}"
    )
    assert ("team-git-promotion-canonical", "team-push-canonical-branch") in edges
    assert ("team-push-canonical-branch", "team-record-canonical-promotion") in edges
    assert (
        "team-record-canonical-promotion",
        "team-mark-release-published",
    ) in edges


def test_team_consolidation_schema_advertises_failure_reason_code() -> None:
    from evander_team_addon.provider_schemas import TEAM_OUTPUT_SCHEMAS

    outputs = TEAM_OUTPUT_SCHEMAS["effect:team:consolidate-git-branches"]

    assert outputs["error_code"] == {"type": ["string", "null"]}


def test_team_runtime_pack_only_pushes_release_branch_to_canonical() -> None:
    pack = _team_runtime_sections()
    push_nodes = [node for node in pack["effects"] if node["type"] == "effect:git:push-branch"]
    # The release/canonical path is the ONLY route allowed to push to a canonical remote;
    # ephemeral/review/feature branches must never reach effect:git:push-branch.
    assert [node["id"] for node in push_nodes] == ["team-push-canonical-branch"]
    push = push_nodes[0]
    assert push["bare_repo_path"] == (
        "${{team-git-promotion-requested.payload.bare_repo_path ?? null}}"
    )
    assert push["bare_repo_name"] == (
        "${{team-git-promotion-requested.payload.bare_repo_name ?? null}}"
    )
    assert push["local_branch"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert push["origin_branch"] == "${{team-git-promotion-requested.payload.target_branch}}"

    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}
    assert ("team-git-promotion-canonical", "team-push-canonical-branch") in edges
    assert ("team-push-canonical-branch", "team-record-canonical-promotion") in edges
    # No other promotion gate feeds the push-branch node.
    for source in (
        "team-git-promotion-review",
        "team-git-promotion-feature",
        "team-git-promotion-release",
    ):
        assert (source, "team-push-canonical-branch") not in edges


def _multi_team_state() -> TeamState:
    return TeamState(
        revision=7,
        teams={
            "alpha-team": Team(
                id="alpha-team",
                name="Alpha",
                manager_agent_id="alpha-manager",
                project_ids=["alpha-project"],
            ),
            "beta-team": Team(
                id="beta-team",
                name="Beta",
                manager_agent_id="beta-manager",
                project_ids=["beta-project"],
            ),
        },
        projects={
            "alpha-project": Project(
                id="alpha-project",
                team_id="alpha-team",
                name="Alpha Project",
                lead_agent_id="alpha-lead",
            ),
            "beta-project": Project(
                id="beta-project",
                team_id="beta-team",
                name="Beta Project",
                lead_agent_id="beta-lead",
            ),
        },
        roles={
            "alpha-team__manager": TeamRole(
                id="alpha-team__manager",
                team_id="alpha-team",
                name="Manager",
            ),
            "alpha-project__project-lead": TeamRole(
                id="alpha-project__project-lead",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Project Lead",
            ),
            "alpha-project__developer": TeamRole(
                id="alpha-project__developer",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Developer",
            ),
            "alpha-project__reviewer": TeamRole(
                id="alpha-project__reviewer",
                team_id="alpha-team",
                project_id="alpha-project",
                name="Reviewer",
            ),
            "beta-team__manager": TeamRole(
                id="beta-team__manager",
                team_id="beta-team",
                name="Manager",
            ),
            "beta-project__project-lead": TeamRole(
                id="beta-project__project-lead",
                team_id="beta-team",
                project_id="beta-project",
                name="Project Lead",
            ),
            "beta-project__developer": TeamRole(
                id="beta-project__developer",
                team_id="beta-team",
                project_id="beta-project",
                name="Developer",
            ),
            "beta-project__reviewer": TeamRole(
                id="beta-project__reviewer",
                team_id="beta-team",
                project_id="beta-project",
                name="Reviewer",
            ),
        },
        agents={
            "alpha-manager": PersistentAgent(
                id="alpha-manager",
                team_id="alpha-team",
                role_id="alpha-team__manager",
                session_id="session-alpha-manager",
                subordinate_agent_ids=["alpha-lead"],
            ),
            "alpha-lead": PersistentAgent(
                id="alpha-lead",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__project-lead",
                session_id="session-alpha-lead",
                reports_to_agent_id="alpha-manager",
                subordinate_agent_ids=["alpha-dev", "alpha-reviewer"],
            ),
            "alpha-dev": PersistentAgent(
                id="alpha-dev",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__developer",
                session_id="session-alpha-dev",
                reports_to_agent_id="alpha-lead",
            ),
            "alpha-reviewer": PersistentAgent(
                id="alpha-reviewer",
                team_id="alpha-team",
                project_id="alpha-project",
                role_id="alpha-project__reviewer",
                session_id="session-alpha-reviewer",
                reports_to_agent_id="alpha-lead",
            ),
            "beta-manager": PersistentAgent(
                id="beta-manager",
                team_id="beta-team",
                role_id="beta-team__manager",
                session_id="session-beta-manager",
                subordinate_agent_ids=["beta-lead"],
            ),
            "beta-lead": PersistentAgent(
                id="beta-lead",
                team_id="beta-team",
                project_id="beta-project",
                role_id="beta-project__project-lead",
                session_id="session-beta-lead",
                reports_to_agent_id="beta-manager",
                subordinate_agent_ids=["beta-dev", "beta-reviewer"],
            ),
            "beta-dev": PersistentAgent(
                id="beta-dev",
                team_id="beta-team",
                project_id="beta-project",
                role_id="beta-project__developer",
                session_id="session-beta-dev",
                reports_to_agent_id="beta-lead",
            ),
            "beta-reviewer": PersistentAgent(
                id="beta-reviewer",
                team_id="beta-team",
                project_id="beta-project",
                role_id="beta-project__reviewer",
                session_id="session-beta-reviewer",
                reports_to_agent_id="beta-lead",
            ),
        },
        agent_runtimes={
            "alpha-dev": AgentRuntime(agent_id="alpha-dev", active_dispatch_id="alpha-dispatch"),
            "beta-dev": AgentRuntime(agent_id="beta-dev", active_dispatch_id="beta-dispatch"),
        },
        tasks={
            "alpha-helper": AgentTask(
                id="alpha-helper",
                owner_agent_id="alpha-lead",
                project_id="alpha-project",
                priority=2,
                title="Alpha helper",
                description="Alpha groundwork",
                created_by_agent_id="alpha-lead",
            ),
            "alpha-task": AgentTask(
                id="alpha-task",
                owner_agent_id="alpha-dev",
                project_id="alpha-project",
                priority=1,
                title="Alpha task",
                description="Alpha work",
                created_by_agent_id="alpha-lead",
                deliverable_ids=["alpha-deliverable"],
                parent_task_id="alpha-helper",
                dependency_task_ids=["alpha-helper"],
            ),
            "beta-task": AgentTask(
                id="beta-task",
                owner_agent_id="beta-dev",
                project_id="beta-project",
                priority=1,
                title="Beta task",
                description="Beta work",
                created_by_agent_id="beta-lead",
                deliverable_ids=["beta-deliverable"],
            ),
        },
        deliverables={
            "alpha-deliverable": Deliverable(
                id="alpha-deliverable",
                task_id="alpha-task",
                producer_agent_id="alpha-dev",
                summary="Alpha deliverable",
                review_request_ids=["alpha-review"],
            ),
            "beta-deliverable": Deliverable(
                id="beta-deliverable",
                task_id="beta-task",
                producer_agent_id="beta-dev",
                summary="Beta deliverable",
                review_request_ids=["beta-review"],
            ),
        },
        review_requests={
            "alpha-review": ReviewRequest(
                id="alpha-review",
                deliverable_id="alpha-deliverable",
                reviewer_agent_id="alpha-reviewer",
            ),
            "beta-review": ReviewRequest(
                id="beta-review",
                deliverable_id="beta-deliverable",
                reviewer_agent_id="beta-reviewer",
            ),
        },
        inbox={
            "alpha-inbox": AgentInboxItem(
                id="alpha-inbox",
                recipient_agent_id="alpha-dev",
                sender_agent_id="alpha-lead",
                task_id="alpha-task",
                item_type=TeamInboxItemType.QUESTION,
                payload={"text": "alpha"},
            ),
            "beta-inbox": AgentInboxItem(
                id="beta-inbox",
                recipient_agent_id="beta-dev",
                sender_agent_id="beta-lead",
                task_id="beta-task",
                item_type=TeamInboxItemType.QUESTION,
                payload={"text": "beta"},
            ),
        },
        action_records={
            "alpha-action": AgentActionRecord(
                id="alpha-action",
                agent_id="alpha-dev",
                dispatch_id="alpha-dispatch",
                action={"notes": "alpha"},
            ),
            "beta-action": AgentActionRecord(
                id="beta-action",
                agent_id="beta-dev",
                dispatch_id="beta-dispatch",
                action={"notes": "beta"},
            ),
        },
        conversations={
            "conversation-alpha": TeamConversation(
                id="conversation-alpha",
                agent_id="alpha-dev",
                operator_user_id="alice@example.com",
                operator_display_name="Alice",
            ),
            "conversation-beta": TeamConversation(
                id="conversation-beta",
                agent_id="beta-dev",
                operator_user_id="bob@example.com",
                operator_display_name="Bob",
            ),
        },
        conversation_messages={
            "message-alpha": TeamConversationMessage(
                id="message-alpha",
                conversation_id="conversation-alpha",
                sender="operator",
                body="Alpha private chat",
            ),
            "message-beta": TeamConversationMessage(
                id="message-beta",
                conversation_id="conversation-beta",
                sender="operator",
                body="Beta private chat",
            ),
        },
        release_packages={
            "alpha-release": ReleasePackage(
                id="alpha-release",
                project_id="alpha-project",
                deliverable_ids=["alpha-deliverable"],
                git_promotion_record_ids=["alpha-release-promotion"],
            ),
            "beta-release": ReleasePackage(
                id="beta-release",
                project_id="beta-project",
                deliverable_ids=["beta-deliverable"],
                git_promotion_record_ids=["beta-release-promotion"],
            ),
        },
        git_promotions={
            "alpha-promotion": GitPromotionRecord(
                id="alpha-promotion",
                project_id="alpha-project",
                task_id="alpha-task",
                deliverable_id="alpha-deliverable",
                actor_agent_id="alpha-dev",
                source_branch="agents/alpha/review",
                target_branch="team/alpha/reviews/alpha-task",
                promotion_kind="review",
            ),
            "alpha-release-promotion": GitPromotionRecord(
                id="alpha-release-promotion",
                project_id="alpha-project",
                release_package_id="alpha-release",
                actor_agent_id="alpha-lead",
                source_branch="team/alpha/features/alpha-task",
                target_branch="team/alpha/releases/alpha-release",
                promotion_kind="release",
            ),
            "beta-promotion": GitPromotionRecord(
                id="beta-promotion",
                project_id="beta-project",
                task_id="beta-task",
                deliverable_id="beta-deliverable",
                actor_agent_id="beta-dev",
                source_branch="agents/beta/review",
                target_branch="team/beta/reviews/beta-task",
                promotion_kind="review",
            ),
            "beta-release-promotion": GitPromotionRecord(
                id="beta-release-promotion",
                project_id="beta-project",
                release_package_id="beta-release",
                actor_agent_id="beta-lead",
                source_branch="team/beta/features/beta-task",
                target_branch="team/beta/releases/beta-release",
                promotion_kind="release",
            ),
        },
        git_work_contexts={
            "alpha-context": GitWorkContext(
                id="alpha-context",
                agent_id="alpha-dev",
                task_id="alpha-task",
                dispatch_id="alpha-dispatch",
                agent_branch="agents/session-alpha-dev/work-session-alpha-dev",
                base_branch="main",
                worktree_path="/worktrees/alpha",
            ),
            "beta-context": GitWorkContext(
                id="beta-context",
                agent_id="beta-dev",
                task_id="beta-task",
                dispatch_id="beta-dispatch",
                agent_branch="agents/session-beta-dev/work-session-beta-dev",
                base_branch="main",
                worktree_path="/worktrees/beta",
            ),
        },
        ephemeral_assignments={
            "alpha-ephemeral": EphemeralAssignment(
                id="alpha-ephemeral",
                parent_agent_id="alpha-dev",
                project_id="alpha-project",
                task_id="alpha-task",
                session_id="session-alpha-ephemeral",
                title="Alpha ephemeral",
                prompt="Alpha prompt",
            ),
            "beta-ephemeral": EphemeralAssignment(
                id="beta-ephemeral",
                parent_agent_id="beta-dev",
                project_id="beta-project",
                task_id="beta-task",
                session_id="session-beta-ephemeral",
                title="Beta ephemeral",
                prompt="Beta prompt",
            ),
        },
    )
