from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.composition import build_admin_runtime
from evander.config import GatewayConfig
from evander.directories import DirectoryStore
from evander.artifact_spaces import ArtifactSpaceStore
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.events.scheduled import scheduled_internal_event_store_path
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.execution import ExecutionStatusKind
from evander.model_usage.store import ModelUsageStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.published_artifacts import PublishedArtifactStore
from evander.queries.providers.base import QueryRuntimeContext
from evander.workflow.packs import WorkflowPack
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from evander_team_addon.chat_attachments import TeamChatAttachmentStorage
from evander_team_addon.mcp import (
    GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    ORDINARY_TEAM_MCP_TOOL_NAMES,
    READ_TEAM_MCP_TOOL_NAMES,
    TEAM_CREATE_CASE_TOOL,
    TEAM_DECIDE_DELIVERABLE_TOOL,
    TEAM_DECIDE_REVIEW_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
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
    DeliverableGitRefs,
    EphemeralAgentPolicy,
    EphemeralAssignment,
    GitWorkContext,
    GitWorkContextState,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseVerificationRun,
    ReviewRequest,
    Team,
    TeamCase,
    TeamCaseStatus,
    TeamConversation,
    TeamConversationMessage,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchStatus,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamOutboxRecord,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskState,
    TaskCancellation,
    _runtime_agent_payload,
)
from evander_team_addon.provider_schemas import (
    TeamCreateCaseEffectDefinition,
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamCreateTaskEffectDefinition,
    TeamEnqueueOperatorChatMessageEffectDefinition,
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
    TeamEnqueueOperatorChatMessageEffectProvider,
    TeamFinalizeAgentDispatchEffectProvider,
    TeamAgentPromptCompletionFinalizeHook,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamSessionRoutingQueryProvider,
    TeamStateQueryProvider,
    TeamUpdateCaseEffectProvider,
    TeamWakeDueAgentsEffectProvider,
    TeamWakeGovernanceManagersEffectProvider,
    _artifact_space_path_segment,
    _build_prompt,
    _build_team_chat_prompt,
    _review_artifact_destination_path,
    _publish_deliverable_artifact_bundle,
    _publish_team_events,
    _prepare_project_repo_task_worktree,
    _project_repo_task_dependency_git_sources,
    _record_dependency_git_preparation_blocker,
    _record_wake_failure,
)
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from evander_team_addon.services.domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
    _case_payloads_for_agent,
    _case_payloads_for_team,
)
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.ephemeral_service import TeamEphemeralService
from evander_team_addon.services.runtime_service import (
    TeamRuntimeService,
    _has_prior_successful_ordinary_wake,
)
from evander_team_addon.services.worktree_cleanup_service import (
    TeamWorktreeCleanupService,
)
from evander_team_addon.services.commands import (
    TeamEphemeralCommandService,
    TeamReviewCommandService,
    TeamTaskUpdateCommandService,
)
from evander_team_addon.services.domain.manager_fallback import (
    _manager_fallback_review_routing_authority_error,
)
from evander_team_addon.services.wake_prompt_context import (
    build_compact_wake_context,
    wake_uses_fresh_context,
)
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


def test_team_project_managed_repository_bindings_follow_state_and_rollback(
    tmp_path: Path,
) -> None:
    first_id = "mr_11111111111111111111"
    second_id = "mr_22222222222222222222"

    class FakeManagedGit:
        def __init__(self) -> None:
            self.bindings: dict[str, set[tuple[str, str]]] = {
                first_id: set(),
                second_id: set(),
            }

        def get_repository(self, managed_repo_id: str) -> dict[str, object]:
            return {
                "managed_repo_id": managed_repo_id,
                "bindings": [
                    {"kind": kind, "binding_id": binding_id}
                    for kind, binding_id in sorted(self.bindings[managed_repo_id])
                ],
            }

        def bind(self, managed_repo_id: str, *, kind: str, binding_id: str) -> None:
            self.bindings[managed_repo_id].add((kind, binding_id))

        def unbind(self, managed_repo_id: str, *, kind: str, binding_id: str) -> None:
            self.bindings[managed_repo_id].discard((kind, binding_id))

    repository = TeamStateRepository(tmp_path)
    _team_admin_service(repository).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    managed_git = FakeManagedGit()
    admin = TeamAdminService(repository, managed_git_admin=managed_git)
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        git=ProjectGitConfig(managed_repo_id=first_id),
        expected_revision=1,
    )
    assert managed_git.bindings[first_id] == {("team_projects", "project")}

    with pytest.raises(TeamStateConflictError):
        admin.update_project(
            project_id="project",
            git=ProjectGitConfig(managed_repo_id=second_id),
            set_git=True,
            expected_revision=1,
        )
    assert repository.read().projects["project"].git == ProjectGitConfig(managed_repo_id=first_id)
    assert managed_git.bindings[first_id] == {("team_projects", "project")}
    assert managed_git.bindings[second_id] == set()

    admin.update_project(
        project_id="project",
        git=ProjectGitConfig(managed_repo_id=second_id),
        set_git=True,
        expected_revision=2,
    )
    assert managed_git.bindings[first_id] == set()
    assert managed_git.bindings[second_id] == {("team_projects", "project")}

    admin.update_project(
        project_id="project",
        git=None,
        set_git=True,
        expected_revision=3,
    )
    assert repository.read().projects["project"].git is None
    assert managed_git.bindings[first_id] == set()
    assert managed_git.bindings[second_id] == set()


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
        "team-external-chat-reply-requested",
        "team-agent-wake-requested",
        "team-governance-wake-requested",
        "team-ephemeral-requested",
        "team-operator-escalation",
        "team-git-promotion-requested",
        "team-git-release-recovery-requested",
        "team-release-verification-requested",
        "system.chat_assistant",
    ]
    assert "team-wake-due-agents" in _node_ids(config.effects)
    assert "team-wake-queued-chat-message" in _node_ids(config.effects)
    assert "team-send-external-chat-reply" in _node_ids(config.effects)
    assert "team-record-external-chat-delivery" in _node_ids(config.effects)
    assert "team-notify-operator" in _node_ids(config.effects)
    assert "team-git-promote-review-branch" in _node_ids(config.effects)
    assert "team-promote-release-recovery-branch" in _node_ids(config.effects)
    assert "team-verify-release-candidate" in _node_ids(config.effects)

    service = build_admin_runtime(tmp_path / "runtime")
    document = service.workflow_config_admin.read_workflow_editor_document()
    assert (
        service.workflow_config_admin.read_workflow_editor_document().revision == document.revision
    )
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
    assert document.output_schemas["team-record-external-chat-delivery"]["status"] == {
        "type": "string"
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
    assert document.output_schemas["team-wake-due-agents"]["retry_event_ids"] == {"type": "array"}
    assert document.output_schemas["team-wake-due-agents"]["retry_scheduled_count"] == {
        "type": "integer"
    }
    assert document.output_schemas["team-wake-due-agents"]["git_work_contexts"] == {"type": "array"}
    assert document.output_schemas["team-wake-due-agents"]["review_artifact_imports"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["review_artifact_import_errors"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"]["preparation_blocker_ids"] == {
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
    assert document.output_schemas["team-verify-release-candidate"]["verification_state"] == {
        "type": "string"
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
        "managed_repo_id": "${{team-git-promote-review-branch.managed_repo_id ?? null}}",
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
    return [
        str(getattr(node, "id"))
        for node in nodes
        if not str(getattr(node, "id")).startswith("system.code_consultation.")
    ]


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
        )


class FakeVmExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def vm_exec(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "message": "task worktree prepared",
        }


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


def test_operator_stand_down_revocation_immediately_wakes_newly_eligible_agents(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="lead-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Integrate backend work",
        description="Integrate the backend task after it is ready.",
        created_by_agent_id="manager",
        expected_revision=repo.read().revision,
    )
    runtime = _team_runtime_service(repo)
    state = runtime.stand_down_agent(
        agent_id="lead",
        reason="Pause the project subtree.",
        set_by="operator:test",
        scope="subtree",
        expected_revision=repo.read().revision,
    )
    subtree_stand_down_id = next(
        item.id
        for item in state.stand_downs.values()
        if item.agent_id == "lead" and item.active
    )
    state = runtime.stand_down_agent(
        agent_id="backend",
        reason="Keep the backend agent paused independently.",
        set_by="operator:test",
        scope="agent",
        expected_revision=state.revision,
    )
    backend_stand_down_id = next(
        item.id
        for item in state.stand_downs.values()
        if item.agent_id == "backend" and item.active
    )

    with pytest.raises(TeamStateConflictError):
        runtime.revoke_operator_stand_downs(
            agent_id="lead",
            revoked_by="operator:test",
            expected_revision=state.revision - 1,
        )
    assert all(item.active for item in repo.read().stand_downs.values())
    assert repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE) == []

    state = runtime.revoke_operator_stand_downs(
        agent_id="lead",
        revoked_by="operator:test",
        expected_revision=state.revision,
    )

    assert state.stand_downs[subtree_stand_down_id].active is False
    assert state.stand_downs[backend_stand_down_id].active is True
    records = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert len(records) == 1
    [lead_wake] = records
    assert lead_wake.payload == {
        "event_type": AGENT_WAKE_REQUESTED_EVENT_TYPE,
        "correlation_key": f"{AGENT_WAKE_REQUESTED_EVENT_TYPE}:lead",
        "payload": {
            "team_id": "team",
            "agent_id": "lead",
            "session_id": "session-lead",
            "reason": "task_ready",
        },
        "delay_seconds": 0.0,
    }
    assert lead_wake.idempotency_key is not None
    assert lead_wake.idempotency_key.endswith(
        f":stand-down-revocation:{subtree_stand_down_id}"
    )
    assert lead_wake.status.value == "completed"
    published = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(published) == 1
    assert published[0].payload["agent_id"] == "lead"

    state = runtime.revoke_operator_stand_downs(
        agent_id="backend",
        revoked_by="operator:test",
        expected_revision=state.revision,
    )

    assert state.stand_downs[backend_stand_down_id].active is False
    records = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert len(records) == 2
    backend_wake = next(
        record for record in records if record.payload["payload"]["agent_id"] == "backend"
    )
    assert backend_wake.payload["delay_seconds"] == 0.0
    assert backend_wake.payload["payload"]["reason"] == "task_ready"
    assert backend_wake.idempotency_key is not None
    assert backend_wake.idempotency_key.endswith(
        f":stand-down-revocation:{backend_stand_down_id}"
    )
    assert backend_wake.status.value == "completed"
    published = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(published) == 2
    assert {event.payload["agent_id"] for event in published} == {"lead", "backend"}

    with pytest.raises(ValueError, match="has no active operator stand-down"):
        runtime.revoke_operator_stand_downs(
            agent_id="backend",
            revoked_by="operator:test",
            expected_revision=state.revision,
        )
    assert (
        len(repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE))
        == 2
    )


def test_operator_stand_down_revocation_rolls_back_when_wake_outbox_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    runtime = _team_runtime_service(repo)
    stood_down = runtime.stand_down_agent(
        agent_id="backend",
        reason="Pause backend work.",
        set_by="operator:test",
        scope="agent",
        expected_revision=repo.read().revision,
    )
    stand_down_id = next(item.id for item in stood_down.stand_downs.values() if item.active)

    def _fail_outbox(_events: object) -> list[object]:
        raise RuntimeError("wake outbox unavailable")

    monkeypatch.setattr(
        "evander_team_addon.services.runtime_service._team_event_outbox_records",
        _fail_outbox,
    )

    with pytest.raises(RuntimeError, match="wake outbox unavailable"):
        runtime.revoke_operator_stand_downs(
            agent_id="backend",
            revoked_by="operator:test",
            expected_revision=stood_down.revision,
        )

    state = repo.read()
    assert state.revision == stood_down.revision
    assert state.stand_downs[stand_down_id].active is True
    assert repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE) == []
    assert InternalEventBus(internal_event_store_path(tmp_path)).list_events() == []


def test_operator_stand_down_revocation_keeps_outbox_pending_when_fast_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    runtime = _team_runtime_service(repo)
    stood_down = runtime.stand_down_agent(
        agent_id="backend",
        reason="Pause backend work.",
        set_by="operator:test",
        scope="agent",
        expected_revision=repo.read().revision,
    )
    stand_down_id = next(item.id for item in stood_down.stand_downs.values() if item.active)

    def _fail_publish(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("internal event bus unavailable")

    monkeypatch.setattr(
        "evander_team_addon.services.runtime_service.InternalEventBus.publish",
        _fail_publish,
    )

    state = runtime.revoke_operator_stand_downs(
        agent_id="backend",
        revoked_by="operator:test",
        expected_revision=stood_down.revision,
    )

    assert state.stand_downs[stand_down_id].active is False
    records = repo.list_outbox_records(intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE)
    assert len(records) == 1
    assert records[0].status.value == "pending"


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


def test_first_team_wake_is_full_then_same_context_wake_is_delta_and_session_bound(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    _state, first_leases = _team_runtime_service(repo).lease_due_agents(
        now=now,
        respect_wake_interval=False,
    )
    first = first_leases[0]
    assert first["has_prior_successful_wake"] is False
    assert wake_uses_fresh_context(first, effective_resume_mode="same_context")

    def _complete_first(state: TeamState) -> TeamState:
        dispatch = state.dispatches[first["dispatch_id"]]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = now
        runtime = state.agent_runtimes["backend"]
        runtime.active_dispatch_id = None
        runtime.wake_leased_until = None
        runtime.next_wake_at = None
        return state

    repo.update(_complete_first)
    _state, second_leases = _team_runtime_service(repo).lease_due_agents(
        now=now + timedelta(minutes=1),
        respect_wake_interval=False,
    )
    second = second_leases[0]
    assert second["has_prior_successful_wake"] is True
    assert not wake_uses_fresh_context(second, effective_resume_mode="same_context")

    def _replace_session(state: TeamState) -> TeamState:
        dispatch = state.dispatches[second["dispatch_id"]]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = now + timedelta(minutes=1)
        state.agents["backend"].session_id = "session-backend-replaced"
        runtime = state.agent_runtimes["backend"]
        runtime.active_dispatch_id = None
        runtime.wake_leased_until = None
        runtime.next_wake_at = None
        return state

    repo.update(_replace_session)
    _state, replacement_leases = _team_runtime_service(repo).lease_due_agents(
        now=now + timedelta(minutes=2),
        respect_wake_interval=False,
    )
    replacement = replacement_leases[0]
    assert replacement["agent"]["session_id"] == "session-backend-replaced"
    assert replacement["has_prior_successful_wake"] is False
    assert wake_uses_fresh_context(
        replacement,
        effective_resume_mode="same_context",
    )


def test_wake_prompt_pruning_is_lifecycle_based_deduplicated_and_bounded() -> None:
    worktree_path = "/home/agent/worktrees/project/task-integration"
    repo_context = {
        "prepared": True,
        "project_id": "project",
        "managed_repo_id": "repo",
        "target_dir": "/home/agent/project-control",
        "git_work_contexts": [
            {
                "id": "branch-context",
                "branch_context_id": "branch-context",
                "task_id": "task-integration",
                "agent_branch": "agents/session/tasks/task-integration",
                "worktree_path": worktree_path,
                "dispatch_id": "dispatch-current",
                "state": "active",
            }
        ],
    }
    lease = {
        "agent": {
            "id": "ordinary-agent",
            "role_name": "Integrator",
            "role_description": "Integrate accepted contributions.",
            "effective_role_permissions": [],
        },
        "runtime": {
            "context_clearing_policy": "off",
            "context_rotation_pending": False,
        },
        "dispatch_id": "dispatch-current",
        "has_prior_successful_wake": False,
        "reason": "scheduled",
        "dispatch": {
            "id": "dispatch-current",
            "cause_kind": "scheduled",
            "cause_id": "cron-42",
            "task_ids": ["task-integration"],
            "project_ids": ["project"],
        },
        "tasks": [
            {
                "id": "task-integration",
                "title": "Integrate contributions",
                "state": "in_progress",
                "priority": 1,
                "project_id": "project",
                "owner_agent_id": "ordinary-agent",
                "integration_boundary": True,
                "deliverable_contract": {
                    "require_git_provenance": True,
                    "required_contribution_task_ids": ["task-a", "task-b"],
                },
            }
        ],
        "projects": [{"id": "project", "name": "Project", "status": "active"}],
        "outcome_kinds": [
            {
                "id": "kind",
                "kind": "prepared_action",
                "name": "Prepared action",
                "effective_project_id": "project",
            }
        ],
        "subordinates": [
            {
                "id": "subordinate",
                "role_name": "Reviewer",
                "role_description": "Review the integration.",
            }
        ],
        "operator_chat_participants": [
            {
                "operator_user_id": f"operator-{index}",
                "conversation_ids": [f"conversation-{index}"],
            }
            for index in range(15)
        ],
        "runtime_attention": [
            {
                "id": "scoped",
                "kind": "blocked_task",
                "severity": "warning",
                "task_id": "task-integration",
            },
            {
                "id": "unrelated",
                "kind": "blocked_task",
                "severity": "warning",
                "task_id": "other-task",
                "detail": "unrelated " + ("x" * 4000),
            },
        ],
        "project_repo": repo_context,
        "project_repos": [repo_context],
    }

    fresh_prompt = _build_prompt(lease, effective_resume_mode="same_context")
    fresh = _team_context_from_prompt(fresh_prompt)
    assert fresh["context_mode"] == "fresh_full"
    assert fresh["current_dispatch"]["cause_id"] == "cron-42"
    assert fresh["tasks"][0]["integration_boundary"] is True
    assert fresh["tasks"][0]["deliverable_contract"] == {
        "require_git_provenance": True,
        "required_contribution_task_ids": ["task-a", "task-b"],
    }
    assert len(fresh["operator_chat_participants"]) == 15
    assert [row["id"] for row in fresh["runtime_attention"]] == ["scoped"]
    assert "project_repo" not in fresh
    assert len(fresh["project_repos"]) == 1
    assert fresh_prompt.count(worktree_path) == 1
    assert len(fresh_prompt) <= 16_000

    retained_lease = {**lease, "has_prior_successful_wake": True}
    retained_prompt = _build_prompt(
        retained_lease,
        effective_resume_mode="same_context",
    )
    retained = _team_context_from_prompt(retained_prompt)
    assert retained["context_mode"] == "retained_delta"
    assert set(retained["agent"]) == {"id"}
    assert "projects" not in retained
    assert "outcome_kinds" not in retained
    assert "subordinates" not in retained
    assert retained["tasks"][0]["deliverable_contract"] == (
        fresh["tasks"][0]["deliverable_contract"]
    )
    assert "Task creation mandate for this wake" not in retained_prompt
    assert len(retained_prompt) < len(fresh_prompt)

    rotated_lease = {
        **retained_lease,
        "runtime": {
            "context_clearing_policy": "off",
            "context_rotation_pending": True,
            "context_rotation_reason": "manual",
            "last_handoff_summary": "Operator note: preserve this exact decision.",
        },
    }
    rotated_prompt = _build_prompt(
        rotated_lease,
        effective_resume_mode="new_context",
    )
    assert _team_context_from_prompt(rotated_prompt)["context_mode"] == "fresh_full"
    assert rotated_prompt.count("preserve this exact decision") == 1


def test_legacy_agent_action_enforces_and_persists_integration_sources(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_or_reprioritize_task(
        task_id="contribution",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Contribution",
        description="Produce the required contribution.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    admin.record_deliverable(
        deliverable_id="deliverable-contribution",
        task_id="contribution",
        producer_agent_id="backend",
        summary="Accepted contribution.",
        artifact_refs=["artifact:contribution"],
        state=TeamRecordState.ACCEPTED,
        expected_revision=repo.read().revision,
    )
    admin.update_task(
        task_id="contribution",
        state=TeamTaskState.FINISHED,
        expected_revision=repo.read().revision,
    )
    admin.update_task(
        task_id="task",
        dependency_task_ids=["contribution"],
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": False,
            "required_contribution_task_ids": ["contribution"],
        },
        expected_revision=repo.read().revision,
    )

    state, missing = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-missing-source",
                    "task_id": "task",
                    "summary": "Incomplete integration.",
                }
            ]
        },
    )
    assert missing["success"] is False
    assert "required contributions" in missing["errors"][0]
    assert "deliverable-missing-source" not in state.deliverables

    state, submitted = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-integrated",
                    "task_id": "task",
                    "summary": "Integrated contribution.",
                    "source_deliverable_ids": ["deliverable-contribution"],
                }
            ]
        },
    )
    assert submitted["success"] is True
    assert state.deliverables[
        "deliverable-integrated"
    ].source_deliverable_ids == ["deliverable-contribution"]


def test_legacy_agent_action_rejects_artifact_only_git_integration(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_task(
        task_id="task",
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": True,
            "required_contribution_task_ids": [],
        },
        expected_revision=repo.read().revision,
    )

    state, result = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "deliverable-artifact-only",
                    "task_id": "task",
                    "summary": "Artifact without pinned Git.",
                    "artifact_refs": ["artifact:integration"],
                }
            ]
        },
    )

    assert result["success"] is False
    assert "authoritative pinned Git provenance" in result["errors"][0]
    assert "deliverable-artifact-only" not in state.deliverables


def test_admin_task_upsert_allows_unchanged_contract_echo_after_submission(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    state, submitted = _team_agent_action_service(repo).apply_agent_action(
        session_id="session-backend",
        action={
            "deliverables": [
                {
                    "id": "ordinary-deliverable",
                    "task_id": "task",
                    "summary": "Ordinary deliverable.",
                }
            ]
        },
    )
    assert submitted["success"] is True, submitted

    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=state.tasks["task"].priority,
        title="Build backend safely",
        description=state.tasks["task"].description,
        created_by_agent_id="lead",
        integration_boundary=False,
        deliverable_contract=None,
        set_deliverable_contract=True,
        expected_revision=repo.read().revision,
    )

    task = repo.read().tasks["task"]
    assert task.title == "Build backend safely"
    assert task.integration_boundary is False
    assert task.deliverable_contract is None


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


def _add_backend_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    priority: int,
) -> None:
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id="backend",
        project_id="project",
        priority=priority,
        title=f"Build backend slice {task_id}",
        description=f"Implement backend work for {task_id}.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )


def _seed_manager_review_stall(
    repo: TeamStateRepository,
    *,
    task_id: str = "task",
    deliverable_id: str = "deliverable-stalled",
    review_id: str = "review-manager",
    make_review_non_actionable: bool = False,
) -> None:
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": deliverable_id,
                    "task_id": task_id,
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
    if make_review_non_actionable:
        _block_review_for_manager_fallback(repo, review_id=review_id)


def _block_review_for_manager_fallback(
    repo: TeamStateRepository,
    *,
    review_id: str = "review-manager",
) -> None:
    def _block_review(state: TeamState) -> TeamState:
        review = state.review_requests[review_id]
        review.state = TeamRecordState.BLOCKED
        review.findings = ["The original review route is no longer actionable."]
        return state

    repo.update(_block_review)


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
        leased_at = datetime(2026, 1, 1, tzinfo=UTC)
        review_request_ids = sorted(
            review.id
            for review in state.review_requests.values()
            if review.reviewer_agent_id == "manager" and review.state == TeamRecordState.PENDING
        )
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id="manager",
            session_id="session-manager",
            cause_kind="manager_fallback",
            review_request_ids=review_request_ids,
            project_ids=["project"],
            status=TeamDispatchStatus.LEASED,
            created_at=leased_at,
            prepared_at=leased_at,
            leased_at=leased_at,
            lease_expires_at=leased_at + timedelta(minutes=15),
        )
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


def test_agent_lease_and_wake_context_include_effective_outcome_catalog(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_outcome_kind_definition(
        definition_id="outcome-kind-prepared-action",
        team_id="team",
        project_id=None,
        kind="prepared_action",
        name="Prepared action",
        description="A durable action ready for an external approval step.",
        reference_requirements="Reference the prepared action ID.",
        evidence_requirements="Cite the validation evidence.",
        expected_revision=repo.read().revision,
    )

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert len(leases) == 1
    assert leases[0]["outcome_kinds"] == [
        {
            "id": "outcome-kind-prepared-action",
            "team_id": "team",
            "project_id": None,
            "kind": "prepared_action",
            "name": "Prepared action",
            "description": "A durable action ready for an external approval step.",
            "reference_requirements": "Reference the prepared action ID.",
            "evidence_requirements": "Cite the validation evidence.",
            "archived": False,
            "created_at": leases[0]["outcome_kinds"][0]["created_at"],
            "updated_at": leases[0]["outcome_kinds"][0]["updated_at"],
            "inherited": True,
            "effective_project_id": "project",
        }
    ]
    context = build_compact_wake_context(leases[0])
    assert context["outcome_kinds"] == [
        {
            "id": "outcome-kind-prepared-action",
            "team_id": "team",
            "project_id": None,
            "effective_project_id": "project",
            "kind": "prepared_action",
            "name": "Prepared action",
            "description": "A durable action ready for an external approval step.",
            "reference_requirements": "Reference the prepared action ID.",
            "evidence_requirements": "Cite the validation evidence.",
            "inherited": True,
        }
    ]


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
    _add_task(repo, task_id="dep", owner_agent_id="lead", state=TeamTaskState.FINISHED)
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])

    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert "backend" in _leased_agent_ids(leases)


def test_parent_owner_with_open_child_waits_for_supervision_transition(
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
    assert "backend" not in _leased_agent_ids(leases)

    def _queue_supervision_transition(current: TeamState) -> TeamState:
        current.inbox["parent-supervision-transition"] = AgentInboxItem(
            id="parent-supervision-transition",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={
                "kind": "parent_task_transition",
                "transition": "blocker",
                "parent_task_id": "task",
                "child_task_ids": ["child"],
            },
            state=TeamInboxItemState.PENDING,
        )
        return current

    repo.update(_queue_supervision_transition)
    _state, transition_leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC),
        agent_id="backend",
    )
    assert len(transition_leases) == 1
    assert transition_leases[0]["reason"] == "inbox"
    assert transition_leases[0]["tasks"] == []
    assert transition_leases[0]["inbox"][0]["id"] == ("parent-supervision-transition")


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
    assert backend_lease["projects"][0]["goals"] == [
        {
            "id": "goal",
            "description": "Ship work",
            "state": "pending",
        }
    ]
    assert backend_lease["projects"][0]["blocking_sources"] == []
    assert task_payload["case_id"] == "case"
    assert task_payload["dependency_status"] == "satisfied"
    assert task_payload["child_task_ids"] == []
    assert task_payload["blocked_by_task_ids"] == []
    assert task_payload["subtask_counts"]["total"] == 0


def test_wake_case_payloads_include_only_active_case_statuses(tmp_path: Path) -> None:
    state = _seed_team(tmp_path).read()
    for status in TeamCaseStatus:
        state.cases[f"case-{status.value}"] = TeamCase(
            id=f"case-{status.value}",
            team_id="team",
            project_id="project",
            title=f"{status.value} case",
            status=status,
            owner_agent_id="backend",
        )

    expected_case_ids = {
        "case-open",
        "case-triage",
        "case-in_progress",
        "case-waiting",
    }

    assert {case["id"] for case in _case_payloads_for_agent(state, "backend")} == expected_case_ids
    assert {case["id"] for case in _case_payloads_for_team(state, "team")} == expected_case_ids


def test_compact_wake_context_omits_resolved_and_closed_cases() -> None:
    context = build_compact_wake_context(
        {
            "agent": {"id": "backend"},
            "cases": [
                {
                    "id": f"case-{status.value}",
                    "status": status.value,
                    "title": f"{status.value} case",
                }
                for status in TeamCaseStatus
            ],
        }
    )

    assert {case["id"] for case in context["cases"]} == {
        "case-open",
        "case-triage",
        "case-in_progress",
        "case-waiting",
    }
    assert context["omitted_counts"]["cases_non_current"] == 2


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


@pytest.mark.parametrize(
    ("configured_dry_run", "expected_dry_run"),
    [
        ("omitted", None),
        (True, True),
        (False, False),
    ],
)
def test_wake_due_agents_cleanup_mode_distinguishes_omitted_from_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_dry_run: object,
    expected_dry_run: bool | None,
) -> None:
    observed: list[bool | None] = []

    def _capture_reconcile(
        _service: TeamWorktreeCleanupService,
        **kwargs: object,
    ) -> SimpleNamespace:
        observed.append(kwargs["dry_run"])  # type: ignore[arg-type]
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr(TeamWorktreeCleanupService, "reconcile", _capture_reconcile)
    definition_kwargs: dict[str, object] = {}
    if configured_dry_run != "omitted":
        definition_kwargs["worktree_cleanup_dry_run"] = configured_dry_run
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            **definition_kwargs,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert observed == [expected_dry_run]


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
    assert "administrative, delegation, coordination, acknowledgement" in guidance
    assert "do not manufacture a routing report deliverable" in guidance
    assert "needs acceptance by your direct superior" in guidance
    assert "This always moves the task to waiting_for_review" in guidance
    assert "use team_decide_deliverable to accept or reject it" in guidance
    assert "Rejection requires a concrete reason" in guidance
    assert "Do not accept or reject someone else's task" in guidance
    assert "idle profile-eligible reviewer" in guidance
    assert "override_routing_gap_reason" in guidance
    assert "Release requests stand alone" in guidance
    assert "git_refs.review_branch or git_refs.agent_branch" in guidance
    assert "failed feature or release promotion" in guidance
    assert "allowed agents/... ref" in guidance
    assert "Review decisions and findings are durable history" in guidance
    assert "lifecycle actions never waive findings" in guidance
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
    assert "git_refs.commit_sha (not commit)" in guidance
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
    assert "Git release sequencing" not in prompt
    assert "Valid owned task_id values for deliverable tools are: task" in prompt
    assert "/artifacts/team-deliverables/task/report.md" in prompt
    assert "team-deliverables/task/report.md" in prompt
    assert TEAM_AGENT_GUIDANCE_PATH in prompt
    assert TEAM_AGENT_GUIDANCE_SECTION_ID in prompt
    assert "Task creation mandate for this wake" in prompt
    assert "do not have create_root_task" in prompt
    assert "You may NOT create top-level tasks" in prompt
    assert "Every team_create_task or team_create_tasks call must set parent_task_id" in prompt
    assert "Identity detail: the full role_description is in" in prompt
    assert "Use team_get_my_identity" in prompt
    assert "A previous Team operation had validation errors" not in prompt
    for field_name in (
        "dependency_status",
        "child_task_ids",
        "subtask_counts",
    ):
        assert field_name in prompt
    assert "team_recover_release" not in prompt
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


def test_artifact_import_path_segments_are_bounded_and_stable() -> None:
    long_id = "deliverable-" + ("very-long-human-readable-id-" * 20)

    first = _artifact_space_path_segment(long_id, "deliverable_id")
    second = _artifact_space_path_segment(long_id, "deliverable_id")
    other = _artifact_space_path_segment(long_id + "other", "deliverable_id")

    assert first == second
    assert first != other
    assert first.startswith("seg_deliverable-")
    assert len(first.encode("utf-8")) <= 96
    assert _artifact_space_path_segment("short-safe-id", "deliverable_id") == "short-safe-id"


def test_review_artifact_destination_bounds_every_identifier_component() -> None:
    destination = _review_artifact_destination_path(
        review_request_id="review-" + ("x" * 500),
        deliverable_id="deliverable-" + ("y" * 500),
        artifact_space_path="team/project/deliverable",
        artifact_id="artifact-" + ("z" * 500),
        artifact_space_version_id="version-" + ("v" * 500),
    )

    components = destination.split("/")
    assert components[0] == "team-review-inputs"
    assert all(len(component.encode("utf-8")) <= 96 for component in components)
    assert len(set(components[1:4])) == 3


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

    assert result["success"] is False
    assert result["review_artifact_imports"] == []
    assert len(result["review_artifact_import_errors"]) == 1
    import_error = result["review_artifact_import_errors"][0]
    assert import_error["review_request_id"] == "review-luke"
    assert import_error["artifact_ref"] == artifact_ref
    assert import_error["artifact_space_path"] == artifact_space_path
    assert "team-agent:luke cannot read artifact space" in import_error["error"]
    assert "artifact_key" not in import_error
    assert resumer.calls == []
    reread = repo.read()
    assert reread.review_requests["review-luke"].state == TeamRecordState.BLOCKED
    assert any(
        blocker.source_id == "review-luke"
        and blocker.reason_code == "review_preparation_failed"
        for blocker in reread.blockers.values()
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

    assert result["success"] is False
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
    assert resumer.calls == []
    reread = repo.read()
    assert reread.review_requests["review-luke"].state == TeamRecordState.BLOCKED
    assert any(
        blocker.source_id == "review-luke"
        and blocker.reason_code == "review_preparation_failed"
        for blocker in reread.blockers.values()
    )


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
    assert "call team_import_artifact with that ref" in str(guidance_files[-1]["content"])
    assert "Never assume another agent's /home/agent or /artifacts path" in str(
        guidance_files[-1]["content"]
    )
    assert "do not pass local artifact paths through communication tools" in str(
        guidance_files[-1]["content"]
    )


def test_team_chat_prompt_deduplicates_current_body_and_caps_history() -> None:
    historical_body = "h" * 5000
    current_body = "current-" + ("c" * 5000)
    historical = {
        "id": "message-old",
        "conversation_id": "conversation",
        "sender": "operator",
        "body": historical_body,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    current = {
        "id": "message-current",
        "conversation_id": "conversation",
        "sender": "operator",
        "body": current_body,
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    prompt = _build_team_chat_prompt(
        state={
            "agents": {"agent": {"id": "agent", "display_name": "Agent"}},
            "conversation_messages": {
                historical["id"]: historical,
                current["id"]: current,
            },
        },
        conversation={
            "id": "conversation",
            "agent_id": "agent",
            "operator_user_id": "operator",
            "operator_display_name": "Operator",
        },
        message=current,
        mutation_allowed=False,
    )

    assert prompt.count(current_body) == 1
    assert historical_body not in prompt
    assert ("h" * 1199) + "…" in prompt


def test_wake_due_agents_provider_retries_queued_chat_attachment_from_relative_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime_dir = Path("runtime")
    repo = _seed_team(runtime_dir)
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
    prepared_attachment = TeamChatAttachmentStorage(runtime_dir).prepare_upload(
        content=b"runtime attachment",
        filename="operator-notes.txt",
        mime_type="text/plain",
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer this after your current wake finishes.",
        attachments=[prepared_attachment],
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
            resolved_config={"runtime_dir": str(runtime_dir)},
            agent_resumer=resumer,
            vm_file_copier=file_copier,
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True, result["errors"]
    assert result["leased_count"] == 0
    assert result["leases"] == []
    assert result["dispatches"] == []
    assert len(result["chat_dispatches"]) == 1
    chat_dispatch = result["chat_dispatches"][0]
    assert chat_dispatch["dispatch_id"] == dispatch_id
    assert chat_dispatch["message_id"] == message.id
    assert chat_dispatch["team_chat_mcp_tools"] == {
        "session_id": "session-backend",
        "requested_tools": [
            TEAM_REPLY_TO_CHAT_TOOL,
            TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
            *[
                tool
                for tool in ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES
                if tool != TEAM_STAGE_CHAT_ATTACHMENT_TOOL
            ],
        ],
        "already_allowed_tools": [],
        "granted_tools": [
            TEAM_REPLY_TO_CHAT_TOOL,
            TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
            *[
                tool
                for tool in ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES
                if tool != TEAM_STAGE_CHAT_ATTACHMENT_TOOL
            ],
        ],
        "removed_tools": [],
        "error": None,
    }
    assert session_store.sessions["session-backend"].allowed_tools == [
        TEAM_REPLY_TO_CHAT_TOOL,
        TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
        *[
            tool
            for tool in ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES
            if tool != TEAM_STAGE_CHAT_ATTACHMENT_TOOL
        ],
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
    assert "Attachments copied into your workspace" in prompt
    assert "operator-notes.txt" in prompt
    assert "untrusted operator-provided input" in prompt
    attachment_copy = next(
        file
        for call in file_copier.calls
        for file in call.get("files") or []
        if file.get("destination_path", "").endswith("/01-operator-notes.txt")
    )
    assert Path(attachment_copy["source_path"]).is_absolute()
    assert attachment_copy["source_path"].endswith(prepared_attachment.id)
    assert attachment_copy["destination_path"].startswith(
        f"/home/agent/team-chat-attachments/{message.id}/"
    )
    reread = TeamStateRepository(runtime_dir).read()
    assert reread.conversation_messages[message.id].status == "running"
    assert reread.agent_runtimes["backend"].active_dispatch_id == dispatch_id
    assert reread.agent_runtimes["backend"].wake_reason == "team_chat"


def test_reply_only_chat_does_not_consume_manual_rotation_baseline(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="preserve baseline reset",
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Reply without changing Team state.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def _reply_only(state: TeamState) -> TeamState:
        state.conversation_messages[message.id].metadata["allow_team_mutations"] = False
        return state

    repo.update(_reply_only)
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    resumer = FakeResumer(result={"thread_id": "reply-only-thread"})
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
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    assert "preserve baseline reset" in str(resumer.calls[0]["prompt"])
    reread = repo.read()
    assert reread.agent_runtimes["backend"].context_rotation_pending is True
    assert (
        reread.conversation_messages[message.id].metadata.get(
            "ordinary_context_seeded"
        )
        is not True
    )

    def _complete_reply_only(state: TeamState) -> TeamState:
        dispatch = state.dispatches[f"team-chat:{message.id}"]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = datetime(2026, 1, 1, tzinfo=UTC)
        runtime = state.agent_runtimes["backend"]
        runtime.active_dispatch_id = None
        runtime.wake_leased_until = None
        runtime.next_wake_at = None
        return state

    repo.update(_complete_reply_only)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["has_prior_successful_wake"] is False
    assert wake_uses_fresh_context(
        leases[0],
        effective_resume_mode="same_context",
    )


def test_every_wake_policy_applies_to_mutation_enabled_chat_and_seeds_context(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Create whatever Team state is needed.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    resumer = FakeResumer(result={"thread_id": "mutation-chat-thread"})
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
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    prompt = str(resumer.calls[0]["prompt"])
    assert "compact complete ordinary working set" in prompt
    assert '"context_mode": "fresh_full"' in prompt
    reread = repo.read()
    assert reread.agent_runtimes["backend"].context_rotation_pending is False
    assert reread.agent_runtimes["backend"].last_thread_id == "mutation-chat-thread"
    assert (
        reread.conversation_messages[message.id].metadata[
            "ordinary_context_seeded"
        ]
        is True
    )
    assert not _has_prior_successful_ordinary_wake(
        reread,
        reread.agents["backend"],
    )

    def _complete_seeded_chat(state: TeamState) -> TeamState:
        dispatch = state.dispatches[f"team-chat:{message.id}"]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = datetime(2026, 1, 1, tzinfo=UTC)
        return state

    completed = repo.update(_complete_seeded_chat)
    assert _has_prior_successful_ordinary_wake(
        completed,
        completed.agents["backend"],
    )


def test_every_wake_reply_only_chat_keeps_ordinary_rotation_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Reply only.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def _disable_mutation(state: TeamState) -> TeamState:
        state.conversation_messages[message.id].metadata["allow_team_mutations"] = False
        return state

    repo.update(_disable_mutation)
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    resumer = FakeResumer(result={"thread_id": "reply-only-every-wake"})
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
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[0]["resume_mode"] == "new_context"
    assert "Team context JSON:" not in str(resumer.calls[0]["prompt"])
    reread = repo.read()
    assert reread.agent_runtimes["backend"].context_rotation_pending is True
    assert "ordinary_context_seeded" not in reread.conversation_messages[
        message.id
    ].metadata


def test_mutation_chat_does_not_prepare_git_or_review_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)

    def _seed_git_and_review(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        state.deliverables["chat-review-deliverable"] = Deliverable(
            id="chat-review-deliverable",
            task_id="task",
            producer_agent_id="lead",
            summary="Review from chat context.",
            artifact_refs=["artifact-space:/missing/chat-review"],
        )
        state.review_requests["chat-review"] = ReviewRequest(
            id="chat-review",
            deliverable_id="chat-review-deliverable",
            reviewer_agent_id="backend",
        )
        state.deliverables["chat-review-deliverable"].review_request_ids = [
            "chat-review"
        ]
        return state

    repo.update(_seed_git_and_review)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Change Team state, but do not start repository work.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("chat must not prepare repositories or review artifacts")

    monkeypatch.setattr(
        "evander_team_addon.providers._prepare_project_repos",
        _unexpected,
    )
    monkeypatch.setattr(
        "evander_team_addon.providers._prepare_review_artifacts_for_lease",
        _unexpected,
    )
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
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    prompt = str(resumer.calls[0]["prompt"])
    assert '"project_repos"' not in prompt
    assert '"review_artifact_imports"' not in prompt


def test_explicit_new_context_forces_fresh_mutation_chat_after_retained_baseline(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _seed_baseline(state: TeamState) -> TeamState:
        state.dispatches["prior-ordinary"] = TeamDispatch(
            id="prior-ordinary",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="scheduled",
            status=TeamDispatchStatus.COMPLETED,
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return state

    repo.update(_seed_baseline)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Use the explicitly fresh mutation context.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            resume_mode="new_context",
            limit=1,
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
    prompt = str(resumer.calls[0]["prompt"])
    assert "compact complete ordinary working set" in prompt
    assert '"context_mode": "fresh_full"' in prompt


def test_explicit_new_context_forces_fresh_reply_only_chat_without_ordinary_json(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _seed_baseline(state: TeamState) -> TeamState:
        state.dispatches["prior-ordinary"] = TeamDispatch(
            id="prior-ordinary",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="scheduled",
            status=TeamDispatchStatus.COMPLETED,
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return state

    repo.update(_seed_baseline)
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        expected_revision=repo.read().revision,
    )
    state, message = _team_chat_service(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Use the explicitly fresh reply-only context.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def _reply_only(state: TeamState) -> TeamState:
        state.conversation_messages[message.id].metadata["allow_team_mutations"] = False
        return state

    repo.update(_reply_only)
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            resume_mode="new_context",
            limit=1,
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
    prompt = str(resumer.calls[0]["prompt"])
    assert "This reply-only chat wake starts a fresh context." in prompt
    assert "Team context JSON:" not in prompt


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
    assert chat_tools["requested_tools"] == [
        TEAM_REPLY_TO_CHAT_TOOL,
        TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    ]
    assert chat_tools["already_allowed_tools"] == [TEAM_STAGE_CHAT_ATTACHMENT_TOOL]
    assert chat_tools["granted_tools"] == [TEAM_REPLY_TO_CHAT_TOOL]
    assert chat_tools["removed_tools"] == [
        tool for tool in ORDINARY_TEAM_MCP_TOOL_NAMES if tool != TEAM_STAGE_CHAT_ATTACHMENT_TOOL
    ]
    assert session_store.sessions["session-backend"].allowed_tools == [
        TEAM_REPLY_TO_CHAT_TOOL,
        TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
    ]
    prompt = str(resumer.calls[0]["prompt"])
    assert "reply-only" in prompt
    assert "no Team state change was performed in this dispatch" in prompt
    assert "Do not promise to make the change later" in prompt
    assert "Current dispatch Team state mutation: allowed" not in prompt
    assert "ordinary Team MCP tools granted to this chat wake" not in prompt


def test_wake_due_agents_leaves_reply_only_chat_queued_while_agent_prompt_is_active(
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
        body="Please just reply when the current work is finished.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def _disable_mutation(state: TeamState) -> TeamState:
        state.conversation_messages[message.id].metadata["allow_team_mutations"] = False
        return state

    repo.update(_disable_mutation)
    dispatch_id = f"team-chat:{message.id}"
    _team_chat_service(repo).queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
    )
    session_store = FakeSessionStore()
    ordinary_tools = list(ORDINARY_TEAM_WITH_READ_MCP_TOOL_NAMES)
    session_store.sessions["session-backend"].allowed_tools = ordinary_tools
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="backend",
            limit=1,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            agent_prompt_activity=SimpleNamespace(has_active_enqueue_prompt=lambda **_kwargs: True),
            vm_file_copier=FakeFileCopier(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["chat_dispatches"] == []
    assert result["leased_count"] == 0
    assert result["leases"] == []
    assert result["dispatches"] == []
    assert resumer.calls == []
    assert session_store.sessions["session-backend"].allowed_tools == ordinary_tools
    reread = TeamStateRepository(tmp_path).read()
    queued_message = reread.conversation_messages[message.id]
    assert queued_message.status == "queued"
    assert queued_message.dispatch_id == dispatch_id
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None


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


def test_build_prompt_instructs_superior_to_decide_direct_deliverable_notice(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    state = repo.read()
    notice = AgentInboxItem(
        id="inbox-direct-deliverable",
        recipient_agent_id="lead",
        sender_agent_id="backend",
        task_id="task",
        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
        response_requested=True,
        priority=TeamInboxPriority.LOW,
        payload={
            "kind": "superior_acceptance_requested",
            "deliverable_id": "deliverable-direct",
            "task_id": "task",
            "summary": "Backend work is ready.",
            "available_decisions": ["accepted", "rejected"],
            "available_alternatives": ["request_review", "report_blocker"],
        },
    )
    lease = {
        "agent": state.agents["lead"].model_dump(mode="json"),
        "reason": "inbox",
        "superior_agent_id": state.agents["lead"].reports_to_agent_id,
        "subordinate_agent_ids": ["backend"],
        "subordinates": [state.agents["backend"].model_dump(mode="json")],
        "tasks": [],
        "projects": [state.projects["project"].model_dump(mode="json")],
        "inbox": [notice.model_dump(mode="json")],
        "release_packages": [],
    }

    prompt = _build_prompt(lease)
    context = _team_context_from_prompt(prompt)

    assert "Superior deliverable decisions for this wake:" in prompt
    assert "deliverable-direct (notice inbox-direct-deliverable, task task)" in prompt
    assert TEAM_DECIDE_DELIVERABLE_TOOL in prompt
    assert "decision accepted or rejected" in prompt
    assert "A rejection must state a concrete reason" in prompt
    assert "If specialist review is required" in prompt
    assert "If evidence or a dependency prevents any decision" in prompt
    assert "do not call team_decide_deliverable" in prompt
    payload = context["inbox"][0]["payload"]
    assert payload["available_decisions"] == ["accepted", "rejected"]
    assert payload["available_alternatives"] == ["request_review", "report_blocker"]


def test_build_prompt_instructs_assigned_reviewer_to_read_leaf_or_composite_spec(
    tmp_path: Path,
) -> None:
    state = _seed_team(tmp_path).read()
    lease = {
        "agent": state.agents["lead"].model_dump(mode="json"),
        "reason": "review",
        "dispatch": {
            "id": "dispatch-review",
            "cause_kind": "review",
            "cause_id": "review-integration",
            "review_request_ids": ["review-integration"],
            "project_ids": ["project"],
        },
        "tasks": [],
        "projects": [state.projects["project"].model_dump(mode="json")],
        "inbox": [],
        "reviews": [
            {
                "id": "review-integration",
                "deliverable_id": "deliverable-integration",
                "reviewer_agent_id": "lead",
                "state": "pending",
                "instructions": "Review the consolidated implementation.",
            }
        ],
        "release_packages": [],
    }

    prompt = _build_prompt(lease)

    assert "Assigned deliverable review procedure for this wake:" in prompt
    assert "review-integration (deliverable deliverable-integration)" in prompt
    assert (
        "call team_get_deliverable for the complete deliverable, resolve its task_id, "
        "and call team_get_task"
    ) in prompt
    assert "Leaf review path:" in prompt
    assert "Composite review path:" in prompt
    assert "deliverable_contract.required_contribution_task_ids" in prompt
    assert "for a legacy non-integration parent, use its direct child_task_ids" in prompt
    assert (
        "assessment.contribution_coverage.required_source_deliverable_ids"
    ) in prompt
    assert "repeat this procedure recursively" in prompt
    assert "coverage alone is not proof of correct integration" in prompt
    assert "decide blocked and name the missing record IDs" in prompt
    assert TEAM_DECIDE_REVIEW_TOOL in prompt

    non_review_prompt = _build_prompt(
        {
            **lease,
            "reason": "scheduled",
            "dispatch": {
                **lease["dispatch"],
                "cause_kind": "scheduled",
                "review_request_ids": [],
            },
            "reviews": [],
        }
    )
    assert "Assigned deliverable review procedure for this wake:" not in non_review_prompt


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


def test_build_prompt_includes_typed_release_gate_retry_context(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)

    def _add_blocked_release(state: TeamState) -> TeamState:
        state.release_packages["rel-retry"] = ReleasePackage(
            id="rel-retry",
            project_id="project",
            candidate_commit_sha="candidate-sha",
            state=TeamRecordState.BLOCKED,
            error="verification worker disconnected",
            gate_failure=ReleaseGateFailure(
                stage="verification",
                kind="infrastructure",
                source_id="verification-attempt-1",
                message="verification worker disconnected",
            ),
        )
        return state

    repo.update(_add_blocked_release)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")

    context = _team_context_from_prompt(_build_prompt(lease))

    release = context["release_packages"]["rel-retry"]
    assert release["candidate_commit_sha"] == "candidate-sha"
    assert release["next_action"] == "retry_release_verification"
    assert release["blocked_reason_code"] == "release_verification_infrastructure"
    assert release["gate_failure"]["source_id"] == "verification-attempt-1"
    assert "concrete evidence" in release["next_action_hint"]
    assert "escalate the blocker instead of retrying" in release["next_action_hint"]


def test_build_prompt_escalates_after_agent_requested_verification_retry_fails(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _add_exhausted_agent_retry(state: TeamState) -> TeamState:
        run = ReleaseVerificationRun(
            id="verification-attempt-2",
            release_package_id="rel-retry",
            candidate_commit_sha="candidate-sha",
            session_id="verification-session-2",
            state="failed",
            attempt_number=2,
            failure_kind="infrastructure",
            retry_reason="The transient worker was replaced.",
            retry_requested_by="agent:backend",
        )
        state.release_verification_runs[run.id] = run
        state.release_packages["rel-retry"] = ReleasePackage(
            id="rel-retry",
            project_id="project",
            candidate_commit_sha="candidate-sha",
            verification_run_ids=[run.id],
            state=TeamRecordState.BLOCKED,
            error="replacement verification worker disconnected",
            gate_failure=ReleaseGateFailure(
                stage="verification",
                kind="infrastructure",
                source_id=run.id,
                message="replacement verification worker disconnected",
            ),
        )
        return state

    repo.update(_add_exhausted_agent_retry)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")

    release = _team_context_from_prompt(_build_prompt(lease))["release_packages"][
        "rel-retry"
    ]
    assert release["next_action"] == "escalate_release_verification_recovery"
    assert "Do not retry" in release["next_action_hint"]
    assert "concrete infrastructure recovery requirement" in release["next_action_hint"]


def test_manager_fallback_prompt_and_dispatch_authorize_handled_review_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    _block_review_for_manager_fallback(repo)
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    _state, leases = _team_runtime_service(repo).lease_due_agents(now=leased_at)
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
    assert reviews["review-manager"]["state"] == "blocked"
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
    assert lease["dispatch"]["review_routing_authorities"] == [
        {
            "kind": "reviewer_without_assigned_review_work",
            "reviewer_agent_id": "luke",
            "project_id": "project",
            "task_id": "task",
            "deliverable_id": "deliverable-stalled",
            "consumed_at": None,
            "consumed_by_review_request_id": None,
        }
    ]
    assert len(prompt.encode("utf-8")) < 32_000

    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: leased_at + timedelta(minutes=1),
    )
    _state, denied = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="backend",
        id="review-routed-outside-authority",
        instructions="Review the stalled backend slice.",
        request_id="manager-fallback-route-review-outside-authority",
    )

    assert denied["success"] is False
    assert denied["errors"][0]["code"] == "dispatch_scope_forbidden"

    state, requested = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-routed-to-luke",
        instructions="Review the stalled backend slice.",
        request_id="manager-fallback-route-review",
    )

    assert requested["success"] is True, json.dumps(requested, indent=2, sort_keys=True)
    assert state.review_requests["review-routed-to-luke"].reviewer_agent_id == "luke"
    authority = state.dispatches[lease["dispatch_id"]].review_routing_authorities[0]
    assert authority.consumed_by_review_request_id == "review-routed-to-luke"
    assert authority.consumed_at is not None
    persisted_authority = (
        TeamStateRepository(tmp_path)
        .read()
        .dispatches[lease["dispatch_id"]]
        .review_routing_authorities[0]
    )
    assert persisted_authority.consumed_by_review_request_id == "review-routed-to-luke"
    assert persisted_authority.consumed_at == authority.consumed_at

    replay_state, replayed = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-routed-to-luke",
        instructions="Review the stalled backend slice.",
        request_id="manager-fallback-route-review",
    )

    assert replayed == requested
    assert set(replay_state.review_requests) == set(state.review_requests)

    def _accept_routed_review(state: TeamState) -> TeamState:
        state.review_requests["review-routed-to-luke"].state = TeamRecordState.ACCEPTED
        state.deliverables["deliverable-stalled"].state = TeamRecordState.ACCEPTED
        state.tasks["task"].state = TeamTaskState.ACCEPTED
        for item in state.inbox.values():
            if item.payload.get("review_request_id") == "review-routed-to-luke":
                item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_accept_routed_review)
    state, repeated = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-routed-to-luke-again",
        instructions="Review the already accepted backend slice again.",
        request_id="manager-fallback-route-review-again",
    )

    assert repeated["success"] is False
    assert repeated["errors"][0]["code"] == "dispatch_scope_stale"
    assert "already consumed" in repeated["errors"][0]["message"]
    assert "review-routed-to-luke-again" not in state.review_requests
    assert state.deliverables["deliverable-stalled"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_manager_fallback_can_consume_multiple_authorities_for_same_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(
        repo,
        deliverable_id="deliverable-a",
        review_id="review-manager-a",
        make_review_non_actionable=True,
    )
    _add_backend_task(repo, task_id="task-b", priority=2)
    _seed_manager_review_stall(
        repo,
        task_id="task-b",
        deliverable_id="deliverable-b",
        review_id="review-manager-b",
        make_review_non_actionable=True,
    )
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    lease = _lease_manager_fallback(repo, now=leased_at)
    assert {
        (authority["reviewer_agent_id"], authority["deliverable_id"])
        for authority in lease["dispatch"]["review_routing_authorities"]
    } == {
        ("luke", "deliverable-a"),
        ("luke", "deliverable-b"),
    }
    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: leased_at + timedelta(minutes=1),
    )

    for suffix in ("a", "b"):
        _state, result = TeamReviewCommandService(repo).request_review_command(
            session_id="session-manager",
            deliverable_id=f"deliverable-{suffix}",
            reviewer_agent_id="luke",
            id=f"review-luke-{suffix}",
            instructions=f"Review backend slice {suffix}.",
            request_id=f"manager-fallback-route-{suffix}",
        )
        assert result["success"] is True, json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )

    authorities = repo.read().dispatches[lease["dispatch_id"]].review_routing_authorities
    assert {authority.consumed_by_review_request_id for authority in authorities} == {
        "review-luke-a",
        "review-luke-b",
    }


def test_manager_fallback_authority_is_not_evicted_by_new_shortlist_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-0",
                    "task_id": "task",
                    "summary": "Earlier backend slice not yet routed for review.",
                }
            ],
        },
    )
    assert result["success"] is True

    def _prepare_earlier_deliverable(state: TeamState) -> TeamState:
        state.deliverables["deliverable-0"].created_at = datetime(
            2025,
            1,
            1,
            tzinfo=UTC,
        )
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_prepare_earlier_deliverable)
    for index in range(1, 6):
        task_id = f"task-{index}"
        _add_backend_task(repo, task_id=task_id, priority=index + 1)
        _seed_manager_review_stall(
            repo,
            task_id=task_id,
            deliverable_id=f"deliverable-{index}",
            review_id=f"review-manager-{index}",
            make_review_non_actionable=True,
        )

    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    lease = _lease_manager_fallback(repo, now=leased_at)
    assert {
        authority["deliverable_id"] for authority in lease["dispatch"]["review_routing_authorities"]
    } == {f"deliverable-{index}" for index in range(1, 6)}

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-manager-0",
                    "deliverable_id": "deliverable-0",
                    "reviewer_agent_id": "manager",
                }
            ],
        },
    )
    assert result["success"] is True
    _block_review_for_manager_fallback(repo, review_id="review-manager-0")

    def _handle_new_review_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_new_review_inbox)
    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: leased_at + timedelta(minutes=1),
    )

    state, routed = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-5",
        reviewer_agent_id="luke",
        id="review-luke-5",
        instructions="Review the fifth backend slice.",
        request_id="manager-fallback-route-5",
    )

    assert routed["success"] is True, json.dumps(routed, indent=2, sort_keys=True)
    authority = next(
        authority
        for authority in state.dispatches[lease["dispatch_id"]].review_routing_authorities
        if authority.deliverable_id == "deliverable-5"
    )
    assert authority.consumed_by_review_request_id == "review-luke-5"


def test_manager_fallback_review_routing_authority_stays_consumed_after_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo, make_review_non_actionable=True)
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    _state, leases = _team_runtime_service(repo).lease_due_agents(now=leased_at)
    lease = next(item for item in leases if item["agent"]["id"] == "manager")
    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: leased_at + timedelta(minutes=1),
    )

    state, requested = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-routed-before-retirement",
        instructions="Review the stalled backend slice.",
        request_id="manager-fallback-route-before-retirement",
    )
    assert requested["success"] is True
    assert (
        state.dispatches[lease["dispatch_id"]]
        .review_routing_authorities[0]
        .consumed_by_review_request_id
        == "review-routed-before-retirement"
    )

    retired_at = leased_at + timedelta(minutes=2)

    def _retire_routed_review(state: TeamState) -> TeamState:
        review = state.review_requests["review-routed-before-retirement"]
        review.state = TeamRecordState.BLOCKED
        review.findings = ["Reviewer became unavailable."]
        review.decision_at = retired_at
        review.retired_at = retired_at
        review.retired_by = "manager"
        review.retirement_reason = "Reviewer became unavailable."
        for item in state.inbox.values():
            if item.payload.get("review_request_id") == review.id:
                item.state = TeamInboxItemState.HANDLED
        return state

    state = repo.update(_retire_routed_review)
    consumed_authority = state.dispatches[lease["dispatch_id"]].review_routing_authorities[0]
    unconsumed_authority = consumed_authority.model_copy(
        update={
            "consumed_at": None,
            "consumed_by_review_request_id": None,
        }
    )
    assert (
        _manager_fallback_review_routing_authority_error(
            state,
            dispatch_agent_id="manager",
            authority=unconsumed_authority,
        )
        is None
    )

    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: retired_at + timedelta(seconds=1),
    )
    state, repeated = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-routed-after-retirement",
        instructions="Route the same review again.",
        request_id="manager-fallback-route-after-retirement",
    )

    assert repeated["success"] is False
    assert repeated["errors"][0]["code"] == "dispatch_scope_stale"
    assert "already consumed" in repeated["errors"][0]["message"]
    assert "review-routed-after-retirement" not in state.review_requests


def test_manager_fallback_review_routing_authority_rejects_cancelled_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo, make_review_non_actionable=True)
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    _state, leases = _team_runtime_service(repo).lease_due_agents(now=leased_at)
    lease = next(item for item in leases if item["agent"]["id"] == "manager")
    assert lease["dispatch"]["review_routing_authorities"]

    def _cancel_task(state: TeamState) -> TeamState:
        state.tasks["task"].state = TeamTaskState.CANCELLED
        state.tasks["task"].cancellation = TaskCancellation(
            reason_code="superseded",
            reason="The review work was cancelled after dispatch.",
            actor_kind="system",
            actor_id="test",
        )
        return state

    repo.update(_cancel_task)
    monkeypatch.setattr(
        "evander_team_addon.services.commands.executor.utc_now",
        lambda: leased_at + timedelta(minutes=1),
    )
    state, result = TeamReviewCommandService(repo).request_review_command(
        session_id="session-manager",
        deliverable_id="deliverable-stalled",
        reviewer_agent_id="luke",
        id="review-after-task-cancelled",
        instructions="Review the cancelled task deliverable.",
        request_id="manager-fallback-route-after-task-cancelled",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_stale"
    assert "review-after-task-cancelled" not in state.review_requests


def test_manager_fallback_does_not_generate_review_authority_for_cancelled_task(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

    def _cancel_task(state: TeamState) -> TeamState:
        state.tasks["task"].state = TeamTaskState.CANCELLED
        state.tasks["task"].cancellation = TaskCancellation(
            reason_code="superseded",
            reason="The review work was cancelled before dispatch.",
            actor_kind="system",
            actor_id="test",
        )
        return state

    repo.update(_cancel_task)
    lease = _lease_manager_fallback(
        repo,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fallback_summary = _team_context_from_prompt(_build_prompt(lease))["manager_fallback_summary"]

    assert lease["dispatch"]["review_routing_authorities"] == []
    assert not fallback_summary["routing_gaps"]


def test_manager_fallback_review_routing_authority_is_stale_before_activation(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo, make_review_non_actionable=True)
    leased_at = datetime(2026, 1, 1, tzinfo=UTC)
    runtime = _team_runtime_service(repo)
    _state, leases = runtime.lease_due_agents(
        now=leased_at,
        defer_activation=True,
    )
    lease = next(item for item in leases if item["agent"]["id"] == "manager")
    assert lease["dispatch"]["review_routing_authorities"]

    runtime.stand_down_agent(
        agent_id="luke",
        reason="Reviewer is unavailable.",
        set_by="operator",
    )

    with pytest.raises(
        RuntimeError,
        match="no longer an actionable manager-fallback routing gap",
    ):
        runtime.mark_agent_dispatch_leased(
            agent_id="manager",
            dispatch_id=lease["dispatch_id"],
            now=leased_at,
        )

    state = repo.read()
    assert state.dispatches[lease["dispatch_id"]].status == TeamDispatchStatus.FAILED
    assert state.agent_runtimes["manager"].active_dispatch_id is None


def test_manager_fallback_authority_matches_compact_prompt_routing_gaps(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = _team_admin_service(repo)
    admin.create_review_profile(
        profile_id="backend-review-profile",
        team_id="team",
        project_id="project",
        name="Backend review",
        description="Review backend deliverables.",
        applicable_role_ids=["project__backend"],
        expected_revision=repo.read().revision,
    )
    for index in range(9):
        admin.register_agent(
            agent_id=f"reviewer-{index:02d}",
            team_id="team",
            project_id="project",
            role_id="project__backend",
            session_id=f"session-reviewer-{index:02d}",
            reports_to_agent_id="lead",
            expected_revision=repo.read().revision,
        )
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

    lease = _lease_manager_fallback(
        repo,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fallback_summary = _team_context_from_prompt(_build_prompt(lease))["manager_fallback_summary"]
    prompt_pairs = {
        (gap["agent"], deliverable_id)
        for gap in fallback_summary["routing_gaps"]
        for deliverable_id in gap["deliverable_ids"]
    }
    authority_pairs = {
        (authority["reviewer_agent_id"], authority["deliverable_id"])
        for authority in lease["dispatch"]["review_routing_authorities"]
    }

    assert authority_pairs == prompt_pairs
    assert len(prompt_pairs) == 8
    assert fallback_summary["omitted_counts"]["routing_gaps"] == 1


def test_manager_fallback_does_not_route_finished_task_deliverable_for_review(
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
        state.tasks["task"].state = TeamTaskState.FINISHED
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
    assert work["task"]["state"] == "finished"
    deliverables = {item["id"]: item for item in fallback_summary["deliverables"]}
    assert deliverables["deliverable-feature"]["state"] == "pending"
    assert deliverables["deliverable-feature"]["git_refs"] == {
        "branch_context_id": "ctx-task",
        "agent_branch": "agents/session-backend/feature",
        "feature_branch": "team/team/features/task-backend",
        "commit_sha": "feedface",
    }
    assert not any(
        "deliverable-feature" in item.get("deliverable_ids", [])
        for item in fallback_summary["routing_gaps"]
    )
    assert "deliverable-feature" in prompt


def test_manager_fallback_does_not_route_superior_acceptance_for_review(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-for-acceptance",
                    "task_id": "task",
                    "summary": "Ready for the project lead to accept.",
                }
            ],
        },
    )
    assert result["success"] is True
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    def _handle_all_inbox(state: TeamState) -> TeamState:
        for item in state.inbox.values():
            item.state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_all_inbox)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    lease = next(lease for lease in leases if lease["agent"]["id"] == "manager")
    context = _team_context_from_prompt(_build_prompt(lease))
    fallback_summary = context["manager_fallback_summary"]
    task = next(item for item in fallback_summary["open_work"] if item["id"] == "task")

    assert task["waiting_reason"] == "superior_acceptance"
    assert not any(
        "deliverable-for-acceptance" in item.get("deliverable_ids", [])
        for item in fallback_summary["routing_gaps"]
    )


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
    _block_review_for_manager_fallback(repo)
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
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

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
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

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
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

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


def test_direct_review_dispatch_allows_assigned_manager_decision(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo)

    lease = _lease_manager_fallback(repo)
    assert lease["reason"] == "review"
    assert lease["dispatch"]["review_request_ids"] == ["review-manager"]
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

    assert result["success"] is True
    assert result["applied"] is True
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.ACCEPTED


def test_manager_fallback_records_review_without_reopening_terminal_task(
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

    assert result["success"] is True
    assert result["applied"] is True
    reread = repo.read()
    assert reread.review_requests["review-manager"].state == TeamRecordState.ACCEPTED
    assert reread.deliverables["deliverable-stalled"].state == TeamRecordState.PENDING
    assert reread.tasks["task"].state == TeamTaskState.ACCEPTED


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

    dispatch_id = _set_manager_fallback_runtime(repo)
    _state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "dispatch_id": dispatch_id,
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
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

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


def test_review_handoff_duplicate_id_feedback_is_included_in_corrective_wake_prompt(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    _seed_manager_review_stall(repo, make_review_non_actionable=True)

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

    context = _team_context_from_prompt(prompt)
    assert context["action_feedback"][0]["error"] == (
        "review request review-manager already exists"
    )
    assert any(
        "Do not recreate existing ids" in hint
        for hint in context["action_feedback"][0]["correction_hints"]
    )


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
    _block_review_for_manager_fallback(repo)
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


def test_wake_due_agents_provider_delivers_latest_action_feedback(
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
    context = _team_context_from_prompt(prompt)
    assert context["action_feedback"] == [
        {
            "action_record_id": "action-feedback",
            "dispatch_id": failed_dispatch_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "error": "agent backend cannot deliver task other-task",
            "action_fields": ["deliverables"],
            "correction_hints": ["Only the task owner can create deliverables for that task."],
        }
    ]
    assert result["dispatches"][0]["delivered_action_feedback_dispatch_ids"] == [failed_dispatch_id]
    reread = TeamStateRepository(tmp_path).read()
    record = reread.action_records["action-feedback"]
    assert record.feedback_delivered_at is not None
    assert record.feedback_delivered_by_dispatch_id == result["dispatches"][0]["dispatch_id"]


def test_wake_due_agents_provider_keeps_action_feedback_undelivered_when_resume_fails(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_action_record(
        repo,
        action_id="action-feedback",
        dispatch_id="failed-dispatch",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        success=False,
        error="agent backend cannot deliver task other-task",
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
            agent_resumer=FakeResumer({"success": False, "message": "resume failed"}),
            vm_file_copier=FakeFileCopier(),
            session_lookup=FakeSessionStore(),
        ),
    )

    result = provider.execute()

    assert result["success"] is False
    record = TeamStateRepository(tmp_path).read().action_records["action-feedback"]
    assert record.feedback_delivered_at is None
    assert record.feedback_delivered_by_dispatch_id is None


def test_rejected_task_is_terminal_for_wake_leasing_and_runtime_status(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.REJECTED)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    state, leases = _team_runtime_service(repo).lease_due_agents(now=now)

    assert "backend" not in _leased_agent_ids(leases)
    assert _runtime_agent_payload(state, state.agents["backend"], now)["wake_due"] is False


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
    assert resumer.calls[0]["resume_mode"] == "same_context"
    assert "team_record_context_handoff" in str(resumer.calls[0]["prompt"])
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_handoff_preparation_pending is True
    _team_runtime_service(repo).record_agent_context_handoff(
        agent_id="backend",
        dispatch_id=runtime.active_dispatch_id or "",
        summary="Continue the controlled rotation with the observed local decision.",
    )

    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[1]["resume_mode"] == "new_context"
    prompt = str(resumer.calls[1]["prompt"])
    assert "Prior handoff summary from Team state" in prompt
    assert "controlled rotation" in prompt
    assert "Agent-authored self-handoff" in prompt
    assert result["dispatches"][0]["context_rotation"] == {
        "rotated": True,
        "reason": "manual",
    }
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.context_handoff_preparation_status == "recorded"
    assert runtime.last_context_rotated_at is not None
    assert runtime.last_thread_id == "thread-rotated"
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
    assert runtime.context_handoff_preparation_pending is False
    assert runtime.last_agent_handoff_summary is None
    assert runtime.last_thread_id == "thread-every-wake"
    assert (
        session_store.sessions["session-backend"].metadata["team_addon"]["context_clearing_policy"]
        == "every_wake"
    )


def test_idle_context_rotation_uses_bounded_handoff_preparation(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.IDLE,
        set_context_clearing_policy=True,
    )
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.FINISHED)

    state, leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime.now(UTC),
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "context_handoff"
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_reason == "idle"
    assert runtime.context_handoff_preparation_pending is True


def test_context_handoff_rejects_oversize_summary_without_releasing_rotation(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
    )
    _state, leases = _team_runtime_service(repo).lease_due_agents()

    with pytest.raises(ValueError, match="at most 1200"):
        _team_runtime_service(repo).record_agent_context_handoff(
            agent_id="backend",
            dispatch_id=str(leases[0]["dispatch_id"]),
            summary="x" * 1201,
        )

    runtime = repo.read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_handoff_preparation_pending is True
    assert runtime.last_agent_handoff_summary is None


def test_context_handoff_completion_without_summary_continues_fresh_rotation(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_task(
        task_id="task",
        state=TeamTaskState.FINISHED,
    )
    _team_runtime_service(repo).request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
    )
    resumer = FakeResumer(result={"thread_id": "thread-after-missing-handoff"})
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

    first = provider.execute()
    preparation_dispatch_id = first["dispatches"][0]["dispatch_id"]
    assert resumer.calls[0]["resume_mode"] == "same_context"

    finalized_state, finalized = _team_runtime_service(
        repo
    ).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=preparation_dispatch_id,
    )

    assert finalized["success"] is True
    preparation_runtime = finalized_state.agent_runtimes["backend"]
    assert preparation_runtime.context_rotation_pending is True
    assert preparation_runtime.context_handoff_preparation_pending is False
    assert preparation_runtime.context_handoff_preparation_status == "failed"
    assert preparation_runtime.next_wake_at is not None

    second = provider.execute()
    fresh_dispatch_id = second["dispatches"][0]["dispatch_id"]
    assert resumer.calls[1]["resume_mode"] == "new_context"
    assert second["dispatches"][0]["context_rotation"] == {
        "rotated": True,
        "reason": "manual",
    }

    _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=fresh_dispatch_id,
    )
    third = provider.execute()

    assert third["leased_count"] == 0
    assert len(resumer.calls) == 2


def test_wake_due_agents_provider_rotates_idle_agent_when_only_rotation_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.FINISHED)
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
    assert resumer.calls[0]["resume_mode"] == "same_context"
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    _team_runtime_service(repo).record_agent_context_handoff(
        agent_id="backend",
        dispatch_id=runtime.active_dispatch_id or "",
        summary="No remaining active work; preserve the local conclusion.",
    )
    result = provider.execute()

    assert result["success"] is True
    assert resumer.calls[1]["resume_mode"] == "new_context"
    assert "Context continuity checkpoint" in str(resumer.calls[1]["prompt"])
    assert "Active tasks:" not in str(resumer.calls[1]["prompt"])
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
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.FINISHED)
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
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.FINISHED)

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
    _team_admin_service(repo).update_task(task_id="task", state=TeamTaskState.FINISHED)
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
    assert resumer.calls[0]["resume_mode"] == "same_context"
    runtime = TeamStateRepository(tmp_path).read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_handoff_preparation_status == "failed"
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
            managed_repo_id="mr_0123456789abcdefabcd",
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
            vm_executor=FakeVmExecutor(),
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
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "clone_branch": "main",
            "vm_user": "agent",
            "directory_path": "root",
            "git_author_name": None,
            "git_author_email": None,
            "git_committer_name": None,
            "git_committer_email": None,
            "git_access_mode": "write",
            "git_access_lifetime": "session",
        }
    ]
    project_repo = result["dispatches"][0]["project_repo"]
    assert project_repo["target_dir"] == "/home/agent/Truman"
    assert project_repo["base_branch"] == "main"
    assert project_repo["branch_context_id"]
    assert project_repo["agent_branch"] == "agents/session-backend/tasks/task-0ebb429f"
    git_context = project_repo["git_work_contexts"][0]
    assert git_context == {
        "id": project_repo["branch_context_id"],
        "branch_context_id": project_repo["branch_context_id"],
        "agent_id": "backend",
        "task_id": "task",
        "agent_branch": "agents/session-backend/tasks/task-0ebb429f",
        "base_branch": "main",
        "dispatch_id": result["dispatches"][0]["dispatch_id"],
        "worktree_path": "/home/agent/worktrees/Truman/task-0ebb429f",
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
    assert '"project_repos"' in prompt
    assert '"project_repo"' not in prompt
    assert project_repo["branch_context_id"] in prompt
    assert "agents/session-backend/tasks/task-0ebb429f" in prompt
    assert "/home/agent/worktrees/Truman/task-0ebb429f" in prompt
    assert "project_repos[].git_work_contexts" in prompt
    assert session_store.calls[1]["metadata"]["team_addon"]["project_repo"] == project_repo
    assert any(
        call["metadata"]["team_addon"].get("agent_id") == "backend" for call in session_store.calls
    )
    reread = TeamStateRepository(tmp_path).read()
    assert set(reread.git_work_contexts) == {project_repo["branch_context_id"]}
    assert (
        reread.git_work_contexts[project_repo["branch_context_id"]].dispatch_id
        == (result["dispatches"][0]["dispatch_id"])
    )


def test_successor_context_records_verified_direct_dependency_git(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    dependency_commit = "d" * 40

    def _seed_dependency(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        state.tasks["dependency"] = AgentTask(
            id="dependency",
            owner_agent_id="backend",
            project_id="project",
            priority=2,
            state=TeamTaskState.ACCEPTED,
            title="Domain and API",
            description="Accepted predecessor",
            created_by_agent_id="lead",
            deliverable_ids=["dependency-deliverable"],
        )
        state.tasks["task"].dependency_task_ids = ["dependency"]
        state.git_work_contexts["dependency-context"] = GitWorkContext(
            id="dependency-context",
            agent_id="backend",
            task_id="dependency",
            agent_branch="agents/session-backend/tasks/dependency",
            base_branch="main",
            base_commit="b" * 40,
            commit_sha=dependency_commit,
            state=GitWorkContextState.SUBMITTED,
        )
        state.deliverables["dependency-deliverable"] = Deliverable(
            id="dependency-deliverable",
            task_id="dependency",
            producer_agent_id="backend",
            summary="Accepted domain implementation",
            state=TeamRecordState.ACCEPTED,
            git_refs=DeliverableGitRefs(
                branch_context_id="dependency-context",
                agent_branch="agents/session-backend/tasks/dependency",
                commit_sha=dependency_commit,
                submitted_commit_sha=dependency_commit,
                reviewed_commit_sha=dependency_commit,
            ),
        )
        return state

    repo.update(_seed_dependency)

    class _ManagedGit:
        def resolve_branch_commit(self, *args: object, **kwargs: object) -> str:
            assert args == ("mr_0123456789abcdefabcd",)
            assert kwargs == {
                "directory_path": "root",
                "branch": "agents/session-backend/tasks/dependency",
            }
            return dependency_commit

    dependency_sources = _project_repo_task_dependency_git_sources(
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
        ),
        state=repo.read(),
        task_id="task",
        managed_repo_id="mr_0123456789abcdefabcd",
    )
    assert dependency_sources == [
        {
            "dependency_task_id": "dependency",
            "deliverable_id": "dependency-deliverable",
            "branch": "agents/session-backend/tasks/dependency",
            "commit_sha": dependency_commit,
        }
    ]
    state, context_id = _team_admin_service(repo).create_git_work_context_with_id(
        context_id="successor-context",
        agent_id="backend",
        task_id="task",
        agent_branch="agents/session-backend/tasks/task",
        base_branch="main",
        base_commit="c" * 40,
        worktree_path="/home/agent/worktrees/Truman/task-0ebb429f",
        dependency_git_sources=dependency_sources,
        expected_revision=repo.read().revision,
    )
    assert state.git_work_contexts[context_id].base_commit == "c" * 40
    durable_context = TeamStateRepository(tmp_path).read().git_work_contexts[context_id]
    assert [
        source.model_dump(mode="json") for source in durable_context.dependency_git_sources
    ] == dependency_sources

    def _make_dependency_unaccepted(state: TeamState) -> TeamState:
        state.deliverables["dependency-deliverable"].state = TeamRecordState.PENDING
        return state

    repo.update(_make_dependency_unaccepted)
    with pytest.raises(
        RuntimeError,
        match="current Git deliverables are not accepted",
    ):
        _project_repo_task_dependency_git_sources(
            EffectRuntimeContext(
                resolved_config={"runtime_dir": str(tmp_path)},
                admin_service=SimpleNamespace(managed_git_admin=_ManagedGit()),
            ),
            state=repo.read(),
            task_id="task",
            managed_repo_id="mr_0123456789abcdefabcd",
        )


def test_libvirt_dependency_git_composition_command_is_valid_shell() -> None:
    executor = FakeVmExecutor()
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": "/runtime"},
        vm_executor=executor,
        session_lookup=FakeSessionStore(),
    )

    _prepare_project_repo_task_worktree(
        context,
        project_repo={
            "guest_repo_name": "Truman",
            "target_dir": "/home/agent/Truman",
            "execution_backend": "libvirt_vm",
        },
        session_id="session-backend",
        task_id="task-ui",
        agent_branch="agents/session-backend/tasks/task-ui",
        base_ref="main",
        prior_base_commit="c" * 40,
        dependency_git_sources=[
            {
                "dependency_task_id": "task-domain",
                "deliverable_id": "deliverable-domain",
                "branch": "agents/session-backend/tasks/task-domain",
                "commit_sha": "d" * 40,
            },
            {
                "dependency_task_id": "task-client",
                "deliverable_id": "deliverable-client",
                "branch": "agents/session-backend/tasks/task-client",
                "commit_sha": "e" * 40,
            },
        ],
    )

    command = str(executor.calls[0]["command"])
    syntax = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert command.index("dependency_branch_0") < command.index("dependency_branch_1")
    assert command.index("dependency_commit_0=") < command.index(
        'if [ -n "$current_dependency_provenance" ]'
    )
    assert f"expected_prior_base={'c' * 40}" in command
    assert "all_dependencies_present=1" in command


def test_dependency_git_preparation_failure_routes_blocker_without_lease(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    assert len(leases) == 1
    lease = leases[0]

    failed = _record_wake_failure(
        repo,
        lease=lease,
        error="accepted dependency branch moved",
    )
    assert failed.dispatches[lease["dispatch_id"]].status == TeamDispatchStatus.FAILED
    assert failed.agent_runtimes["backend"].active_dispatch_id is None

    routed = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="accepted dependency branch moved",
    )

    blocker = next(
        blocker
        for blocker in routed.blockers.values()
        if blocker.reason_code == "dependency_git_preparation_failed"
    )
    assert blocker.reason_code == "dependency_git_preparation_failed"
    assert blocker.affected_task_ids == ["task"]
    assert blocker.owner_agent_id == "lead"
    assert routed.dispatches[lease["dispatch_id"]].status == TeamDispatchStatus.FAILED
    assert routed.agent_runtimes["backend"].active_dispatch_id is None

    def _mark_rework_pending(state: TeamState) -> TeamState:
        state.deliverables["deliverable-rework"] = Deliverable(
            id="deliverable-rework",
            task_id="task",
            producer_agent_id="backend",
            summary="Accepted work requiring provenance replacement",
            state=TeamRecordState.ACCEPTED,
            rework_requested_at=datetime.now(UTC),
            rework_requested_by_agent_id="lead",
            rework_reason="Accepted Git provenance is incomplete.",
        )
        state.tasks["task"].deliverable_ids.append("deliverable-rework")
        return state

    repo.update(_mark_rework_pending)
    _state, repeated_leases = _team_runtime_service(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )
    assert len(repeated_leases) == 1
    assert repo.read().blockers[blocker.id].state == TeamBlockerState.RESOLVED


def test_successive_dispatches_reuse_task_worktree_with_fresh_context_id(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        return state

    repo.update(_set_git)
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
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(),
            vm_executor=FakeVmExecutor(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    first = provider.execute()
    first_dispatch = first["dispatches"][0]
    first_context = first_dispatch["project_repo"]["git_work_contexts"][0]
    finalized_state, finalized = _team_runtime_service(repo).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=first_dispatch["dispatch_id"],
    )
    assert finalized["success"] is True
    assert finalized_state.git_work_contexts[first_context["id"]].state.value == "abandoned"

    def _make_due(state: TeamState) -> TeamState:
        state.agent_runtimes["backend"].next_wake_at = None
        return state

    repo.update(_make_due)
    second = provider.execute()
    second_dispatch = second["dispatches"][0]
    second_context = second_dispatch["project_repo"]["git_work_contexts"][0]

    assert second_dispatch["dispatch_id"] != first_dispatch["dispatch_id"]
    assert second_context["id"] != first_context["id"]
    assert second_context["dispatch_id"] == second_dispatch["dispatch_id"]
    assert second_context["worktree_path"] == first_context["worktree_path"]
    assert second_context["agent_branch"] == first_context["agent_branch"]
    reread = repo.read()
    assert reread.git_work_contexts[first_context["id"]].state.value == "abandoned"
    assert reread.git_work_contexts[second_context["id"]].state.value == "active"


def test_wake_due_agents_prepares_every_scoped_repo_before_leasing_and_resume(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    first_repo_id = "mr_11111111111111111111"
    second_repo_id = "mr_22222222222222222222"

    def _set_first_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id=first_repo_id,
            base_branch="main",
        )
        return state

    repo.update(_set_first_git)
    admin = _team_admin_service(repo)
    admin.create_project(
        project_id="project-two",
        team_id="team",
        name="Project Two",
        lead_agent_id="backend",
        lead_session_id=None,
        git=ProjectGitConfig(
            managed_repo_id=second_repo_id,
            base_branch="main",
        ),
        goals=[ProjectGoal(id="goal-two", description="Ship second work")],
        expected_revision=repo.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="task-two",
        owner_agent_id="backend",
        project_id="project-two",
        priority=1,
        title="Build second backend",
        description="Implement the second project work.",
        created_by_agent_id="backend",
        expected_revision=repo.read().revision,
    )

    events: list[str] = []

    def _on_resume(_kwargs: dict[str, object]) -> None:
        events.append("resume")
        dispatches = list(repo.read().dispatches.values())
        assert len(dispatches) == 1
        assert dispatches[0].status == TeamDispatchStatus.LEASED

    repo_cloner = FakeRepoCloner(on_clone=lambda: events.append("clone"))
    resumer = FakeResumer(on_resume=_on_resume)
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
            vm_executor=FakeVmExecutor(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert events == ["clone", "clone", "resume"]
    assert [call["managed_repo_id"] for call in repo_cloner.calls] == [
        first_repo_id,
        second_repo_id,
    ]
    dispatch = result["dispatches"][0]
    assert "project_repo" not in dispatch
    assert {row["project_id"]: row["managed_repo_id"] for row in dispatch["project_repos"]} == {
        "project": first_repo_id,
        "project-two": second_repo_id,
    }
    assert {
        context["task_id"]: project_repo["project_id"]
        for project_repo in dispatch["project_repos"]
        for context in project_repo["git_work_contexts"]
    } == {
        "task": "project",
        "task-two": "project-two",
    }
    durable = repo.read().dispatches[dispatch["dispatch_id"]]
    assert durable.status == TeamDispatchStatus.LEASED
    assert durable.project_ids == ["project", "project-two"]
    assert durable.managed_repo_ids == [first_repo_id, second_repo_id]


def test_wake_due_agents_refreshes_state_after_repo_clone_revision_change(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state: TeamState) -> TeamState:
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
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
            vm_executor=FakeVmExecutor(),
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
            managed_repo_id="mr_0123456789abcdefabcd",
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
            vm_executor=FakeVmExecutor(),
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
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        return state

    repo.update(_set_git)

    def _add_second_task(state: TeamState) -> TeamState:
        state.tasks["task-second"] = state.tasks["task"].model_copy(
            update={"id": "task-second", "title": "Build second backend slice"}
        )
        return state

    repo.update(_add_second_task)
    original = TeamAdminService.create_git_work_context_with_id
    calls = 0

    def _always_conflict(self: TeamAdminService, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(self, **kwargs)
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
            vm_executor=FakeVmExecutor(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["dispatches"] == []
    assert calls == 4
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
    assert len(reread.git_work_contexts) == 1
    assert {context.state for context in reread.git_work_contexts.values()} == {
        GitWorkContextState.ABANDONED
    }
    assert {context.dispatch_id for context in reread.git_work_contexts.values()} == {
        result["transient_conflicts"][0]["dispatch_id"]
    }
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
            managed_repo_id="mr_0123456789abcdefabcd",
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
                "pr_notes_ref": "refs/notes/agent-pr/test",
                "execution_backend": "libvirt_vm",
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
            vm_executor=FakeVmExecutor(),
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(captured_effects) == 1
    clone_effect = captured_effects[0]
    assert getattr(clone_effect, "managed_repo_id") == "mr_0123456789abcdefabcd"
    assert not hasattr(clone_effect, "repo_source")
    assert not hasattr(clone_effect, "bare_repo_path")


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
            managed_repo_id="mr_0123456789abcdefabcd",
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
            vm_executor=FakeVmExecutor(),
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
    assert len(result["preparation_blocker_ids"]) == 1
    assert resumer.calls == []
    runtime = reread.agent_runtimes["backend"]
    assert runtime.active_dispatch_id is None
    assert runtime.wake_leased_until is None
    assert reread.action_records == {}
    durable_dispatch = reread.dispatches[str(failed_repo_preparation["dispatch_id"])]
    assert durable_dispatch.status == TeamDispatchStatus.FAILED
    assert durable_dispatch.error == "repo missing"
    preparation_blocker = reread.blockers[result["preparation_blocker_ids"][0]]
    assert preparation_blocker.reason_code == "task_execution_preparation_failed"
    assert preparation_blocker.task_id == "task"


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
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-standalone-promotion",
            intent_type="team.git.promotion_requested",
            aggregate_type="git_promotion",
            aggregate_id="promotion-standalone",
            idempotency_key="team.git.promotion_requested:promotion-standalone",
            payload={
                "event_type": "team.git.promotion_requested",
                "correlation_key": "promotion-standalone",
                "payload": {"promotion_id": "promotion-standalone"},
            },
        )
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
    promotion_events = [
        event for event in events if event.event_type == "team.git.promotion_requested"
    ]
    assert len(promotion_events) == 1
    assert promotion_events[0].payload["promotion_id"] == "promotion-standalone"
    [promotion_outbox] = repo.list_outbox_records(
        intent_type="team.git.promotion_requested",
        aggregate_type="git_promotion",
        aggregate_id="promotion-standalone",
    )
    assert promotion_outbox.status.value == "completed"
    assert promotion_outbox.attempt_count == 1
    assert hook.on_post_completion_dispatch(worker=worker) == 0


def test_agent_prompt_hook_renews_live_worker_past_original_lease_and_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    original_leased_at = datetime.now(UTC) - timedelta(minutes=16)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=original_leased_at,
        lease_seconds=900,
    )
    dispatch_id = leases[0]["dispatch_id"]
    original_expires_at = repo.read().dispatches[dispatch_id].lease_expires_at
    assert original_expires_at is not None
    assert original_expires_at < datetime.now(UTC)

    worker_execution_id = "agent-session-backend-live"
    record = SimpleNamespace(
        execution_id="exec-live-team-prompt",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        finished_at=original_leased_at,
        outputs={
            "dispatch_id": dispatch_id,
            "session_id": "session-backend",
            "success": True,
            "queued": False,
            "execution_backend": "container_worker_vm",
            "execution_id": worker_execution_id,
        },
    )

    class _LiveBackend:
        def inspect(self, execution_id: str) -> SimpleNamespace:
            assert execution_id == worker_execution_id
            return SimpleNamespace(
                execution_id=execution_id,
                session_id="session-backend",
                status=ExecutionStatusKind.RUNNING,
            )

    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_container_backend_for_worker",
        classmethod(lambda cls, *, worker, session_id: _LiveBackend()),
    )
    session_store = FakeSessionStore()
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
        provider_factory=SimpleNamespace(admin_service=session_store),
    )
    hook = TeamAgentPromptCompletionFinalizeHook()

    assert hook.on_waiting_reconciliation(worker=worker) == 1
    renewed = repo.read()
    renewed_runtime = renewed.agent_runtimes["backend"]
    renewed_dispatch = renewed.dispatches[dispatch_id]
    assert renewed_runtime.active_dispatch_id == dispatch_id
    assert renewed_runtime.wake_leased_until == renewed_dispatch.lease_expires_at
    assert renewed_dispatch.lease_expires_at is not None
    assert renewed_dispatch.lease_expires_at > datetime.now(UTC)
    assert renewed_dispatch.lease_expires_at <= datetime.now(UTC) + timedelta(seconds=901)

    _state, command_result = TeamTaskUpdateCommandService(repo).update_task_command(
        session_id="session-backend",
        task_id="task",
        request_id="request-after-original-lease",
        state="in_progress",
    )
    assert command_result["success"] is True
    _state, overlapping_leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime.now(UTC),
        lease_seconds=900,
    )
    assert overlapping_leases == []
    assert repo.read().agent_runtimes["backend"].active_dispatch_id == dispatch_id

    record.status = "succeeded"
    record.finished_at = datetime.now(UTC)
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    finished = repo.read()
    assert finished.dispatches[dispatch_id].status == TeamDispatchStatus.COMPLETED
    assert finished.agent_runtimes["backend"].active_dispatch_id is None
    assert finished.agent_runtimes["backend"].wake_leased_until is None


@pytest.mark.parametrize(
    ("record_status", "worker_status", "inspect_raises", "expect_finalized"),
    [
        ("succeeded", None, False, True),
        ("waiting", ExecutionStatusKind.FAILED, False, False),
        ("waiting", None, True, False),
    ],
)
def test_agent_prompt_hook_does_not_renew_completed_or_lost_worker_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_status: str,
    worker_status: ExecutionStatusKind | None,
    inspect_raises: bool,
    expect_finalized: bool,
) -> None:
    repo = _seed_team(tmp_path)
    leased_at = datetime.now(UTC) - timedelta(minutes=16)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=leased_at,
        lease_seconds=900,
    )
    dispatch_id = leases[0]["dispatch_id"]
    original_expires_at = repo.read().dispatches[dispatch_id].lease_expires_at
    record = SimpleNamespace(
        execution_id="exec-terminal-team-prompt",
        effect_type="effect:agent:enqueue-prompt",
        status=record_status,
        finished_at=datetime.now(UTC),
        outputs={
            "dispatch_id": dispatch_id,
            "session_id": "session-backend",
            "success": record_status == "succeeded",
            "queued": False,
            "execution_backend": "container_worker_vm",
            "execution_id": "agent-session-backend-terminal",
        },
    )

    class _TerminalOrLostBackend:
        def inspect(self, execution_id: str) -> SimpleNamespace:
            if inspect_raises:
                raise RuntimeError("worker unavailable")
            return SimpleNamespace(
                execution_id=execution_id,
                session_id="session-backend",
                status=worker_status,
            )

    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_container_backend_for_worker",
        classmethod(lambda cls, *, worker, session_id: _TerminalOrLostBackend()),
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
        provider_factory=SimpleNamespace(admin_service=FakeSessionStore()),
    )
    hook = TeamAgentPromptCompletionFinalizeHook()

    assert hook.on_waiting_reconciliation(worker=worker) == 0
    assert repo.read().dispatches[dispatch_id].lease_expires_at == original_expires_at
    assert hook.on_post_completion_dispatch(worker=worker) == int(expect_finalized)
    reread = repo.read()
    if expect_finalized:
        assert reread.dispatches[dispatch_id].status == TeamDispatchStatus.COMPLETED
        assert reread.agent_runtimes["backend"].active_dispatch_id is None
    else:
        assert reread.dispatches[dispatch_id].status == TeamDispatchStatus.LEASED
        assert reread.agent_runtimes["backend"].active_dispatch_id == dispatch_id


def test_lost_worker_dispatch_still_expires_and_is_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    leased_at = datetime.now(UTC) - timedelta(minutes=16)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=leased_at,
        lease_seconds=900,
    )
    stale_dispatch_id = leases[0]["dispatch_id"]
    record = SimpleNamespace(
        execution_id="exec-lost-team-prompt",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        finished_at=leased_at,
        outputs={
            "dispatch_id": stale_dispatch_id,
            "session_id": "session-backend",
            "success": True,
            "queued": False,
            "execution_backend": "container_worker_vm",
            "execution_id": "agent-session-backend-lost",
        },
    )

    class _LostBackend:
        def inspect(self, execution_id: str) -> SimpleNamespace:
            raise RuntimeError(f"worker job {execution_id} disappeared")

    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_container_backend_for_worker",
        classmethod(lambda cls, *, worker, session_id: _LostBackend()),
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
    )
    hook = TeamAgentPromptCompletionFinalizeHook()
    assert hook.on_waiting_reconciliation(worker=worker) == 0

    _state, replacement_leases = _team_runtime_service(repo).lease_due_agents(
        now=datetime.now(UTC),
        lease_seconds=900,
    )
    assert len(replacement_leases) == 1
    replacement_dispatch_id = replacement_leases[0]["dispatch_id"]
    assert replacement_dispatch_id != stale_dispatch_id
    reread = repo.read()
    assert reread.dispatches[stale_dispatch_id].status == TeamDispatchStatus.EXPIRED
    assert reread.dispatches[replacement_dispatch_id].status == TeamDispatchStatus.LEASED
    assert reread.agent_runtimes["backend"].active_dispatch_id == replacement_dispatch_id


def test_live_probe_does_not_revive_dispatch_replaced_during_renewal_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_team(tmp_path)
    leased_at = datetime.now(UTC) - timedelta(minutes=16)
    _state, leases = _team_runtime_service(repo).lease_due_agents(
        now=leased_at,
        lease_seconds=900,
    )
    stale_dispatch_id = leases[0]["dispatch_id"]
    record = SimpleNamespace(
        execution_id="exec-racing-team-prompt",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        finished_at=leased_at,
        outputs={
            "dispatch_id": stale_dispatch_id,
            "session_id": "session-backend",
            "success": True,
            "queued": False,
            "execution_backend": "container_worker_vm",
            "execution_id": "agent-session-backend-racing",
        },
    )
    replacement_dispatch_ids: list[str] = []

    class _RacingBackend:
        def inspect(self, execution_id: str) -> SimpleNamespace:
            _state, replacement_leases = _team_runtime_service(repo).lease_due_agents(
                now=datetime.now(UTC),
                lease_seconds=900,
            )
            replacement_dispatch_ids.extend(
                str(lease["dispatch_id"]) for lease in replacement_leases
            )
            return SimpleNamespace(
                execution_id=execution_id,
                session_id="session-backend",
                status=ExecutionStatusKind.RUNNING,
            )

    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_container_backend_for_worker",
        classmethod(lambda cls, *, worker, session_id: _RacingBackend()),
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=FakeExecutionStore([record]),
    )

    assert TeamAgentPromptCompletionFinalizeHook().on_waiting_reconciliation(worker=worker) == 0
    assert len(replacement_dispatch_ids) == 1
    replacement_dispatch_id = replacement_dispatch_ids[0]
    reread = repo.read()
    assert reread.dispatches[stale_dispatch_id].status == TeamDispatchStatus.EXPIRED
    assert reread.dispatches[replacement_dispatch_id].status == TeamDispatchStatus.LEASED
    assert reread.agent_runtimes["backend"].active_dispatch_id == replacement_dispatch_id


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


def test_enqueue_operator_chat_message_effect_validates_target_and_authorization(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    config = GatewayConfig(runtime_dir=tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    SqliteCommunicationTargetsRepository(
        communication_targets_db_path(config.runtime_dir)
    ).write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "codex-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                },
                {
                    "id": "read-only-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345679",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "teams-without-reply",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["read", "write"]},
                },
            ]
        },
    )
    provider = TeamEnqueueOperatorChatMessageEffectProvider()
    definition = TeamEnqueueOperatorChatMessageEffectDefinition(
        id="enqueue-external-chat",
        display_name="Enqueue external chat",
        type="effect:team:enqueue-operator-chat-message",
        agent_id="backend",
        operator_user_id="operator-alice",
        body="Please reply through Team Chat.",
        provider="discord",
        communication_target_id="codex-discord",
        source_message_id="discord-message-1",
        source_author_id="discord-operator",
        allow_team_mutations=True,
        authorized_author_ids=["discord-operator"],
    )
    provider.initialize(
        definition,
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root", "directory_store": directory_store},
        ),
    )

    result = provider.execute()
    replay = provider.execute()

    assert result["success"] is True
    assert result["duplicate"] is False
    assert replay["duplicate"] is True
    stored = TeamStateRepository(tmp_path).read()
    message = stored.conversation_messages[result["message_id"]]
    assert message.metadata.get("allow_team_mutations") is None
    published = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(published) == 1
    assert published[0].event_type == CHAT_MESSAGE_QUEUED_EVENT_TYPE
    assert published[0].payload["message_id"] == result["message_id"]
    assert published[0].payload["dispatch_id"] == result["dispatch_id"]

    denied = TeamEnqueueOperatorChatMessageEffectProvider()
    denied.initialize(
        definition.model_copy(
            update={
                "id": "deny-external-chat",
                "source_message_id": "discord-message-2",
                "source_author_id": "other",
            }
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root", "directory_store": directory_store},
        ),
    )
    with pytest.raises(ValueError, match="not authorized"):
        denied.execute()

    read_only = TeamEnqueueOperatorChatMessageEffectProvider()
    read_only.initialize(
        definition.model_copy(
            update={
                "id": "read-only-external-chat",
                "communication_target_id": "read-only-discord",
                "source_message_id": "discord-message-3",
            }
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root", "directory_store": directory_store},
        ),
    )
    with pytest.raises(ValueError, match="does not allow write capability"):
        read_only.execute()

    threaded_without_reply = TeamEnqueueOperatorChatMessageEffectProvider()
    threaded_without_reply.initialize(
        definition.model_copy(
            update={
                "id": "threaded-external-chat",
                "provider": "teams",
                "communication_target_id": "teams-without-reply",
                "source_message_id": "teams-message-1",
                "source_thread_id": "thread-1",
            }
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root", "directory_store": directory_store},
        ),
    )
    with pytest.raises(ValueError, match="does not allow reply capability"):
        threaded_without_reply.execute()


def test_create_task_effect_creates_operator_authored_task(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _add_task(
        repo,
        task_id="workflow-dependency",
        owner_agent_id="lead",
        state=TeamTaskState.FINISHED,
    )
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
            dependency_task_ids=["workflow-dependency"],
            integration_boundary=True,
            deliverable_contract={
                "require_git_provenance": True,
                "required_contribution_task_ids": ["workflow-dependency"],
            },
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
    assert result["task"]["assessment"]["task_id"] == "workflow-task"
    assert result["task"]["readiness"] == "actionable"
    reread = TeamStateRepository(tmp_path).read()
    task = reread.tasks["workflow-task"]
    assert task.case_id == "case"
    assert task.parent_task_id == "task"
    assert task.dependency_task_ids == ["workflow-dependency"]
    assert task.integration_boundary is True
    assert task.deliverable_contract is not None
    assert task.deliverable_contract.require_git_provenance is True
    assert task.deliverable_contract.required_contribution_task_ids == [
        "workflow-dependency"
    ]
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
    send_delivery_edges = [
        edge
        for edge in pack["edges"]
        if edge["source_id"] == "team-send-external-chat-reply"
        and edge["target_id"]
        in {
            "team-record-external-chat-delivery",
            "team-record-external-chat-delivery-failure",
        }
    ]

    assert triggers["team-operator-escalation"]["type"] == "trigger:event"
    assert triggers["team-operator-escalation"]["event_type"] == (
        "team.operator.escalation_requested"
    )
    assert triggers["team-chat-message-queued"]["type"] == "trigger:event"
    assert triggers["team-chat-message-queued"]["event_type"] == ("team.chat.message_queued")
    assert triggers["team-external-chat-reply-requested"]["event_type"] == (
        "team.chat.external_reply_requested"
    )
    assert triggers["team-agent-wake-requested"]["type"] == "trigger:event"
    assert triggers["team-agent-wake-requested"]["event_type"] == ("team.agent.wake_requested")
    assert triggers["team-governance-wake-requested"]["event_type"] == (
        "team.governance.wake_requested"
    )
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
    assert effects["team-wake-requested-governance"]["type"] == (
        "effect:team:wake-governance-managers"
    )
    assert effects["team-wake-requested-governance"]["governance_group_id"] == (
        "${{team-governance-wake-requested.payload.governance_group_id}}"
    )

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
    assert (
        "team-external-chat-reply-requested",
        "team-send-external-chat-reply",
    ) in edges
    assert (
        "team-send-external-chat-reply",
        "team-record-external-chat-delivery",
    ) in edges
    assert effects["team-send-external-chat-reply"]["type"] == ("effect:communication:send-message")
    assert effects["team-record-external-chat-delivery"]["type"] == (
        "effect:team:record-external-chat-delivery"
    )
    failure_recorder = effects["team-record-external-chat-delivery-failure"]
    assert failure_recorder["type"] == "effect:team:record-external-chat-delivery"
    assert failure_recorder["success"] is False
    assert failure_recorder["error"] == ("${{team-send-external-chat-reply.error_message ?? null}}")
    assert {(edge["target_id"], edge.get("source_port")) for edge in send_delivery_edges} == {
        ("team-record-external-chat-delivery", None),
        ("team-record-external-chat-delivery-failure", "error"),
    }
    assert ("team-agent-wake-requested", "team-wake-requested-agent") in edges
    assert (
        "team-governance-wake-requested",
        "team-wake-requested-governance",
    ) in edges


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
    assert promote["source_branch"] == ("${{team-git-promotion-requested.payload.source_branch}}")
    assert promote["managed_repo_id"] == (
        "${{team-git-promotion-requested.payload.managed_repo_id}}"
    )
    assert promote["directory_path"] == (
        "${{team-git-promotion-requested.payload.directory_path ?? null}}"
    )
    assert promote["expected_commit_sha"] == (
        "${{team-git-promotion-requested.payload.expected_commit_sha}}"
    )
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
    assert effects["team-consolidate-feature-branches"]["managed_repo_id"] == (
        "${{team-git-promotion-requested.payload.managed_repo_id}}"
    )
    assert effects["team-consolidate-feature-branches"]["directory_path"] == (
        "${{team-git-promotion-requested.payload.directory_path ?? null}}"
    )
    assert effects["team-consolidate-feature-branches"]["expected_source_commits"] == (
        "${{team-git-promotion-requested.payload.expected_source_commits}}"
    )
    assert effects["team-record-feature-promotion"]["type"] == ("effect:team:record-git-promotion")
    assert ("team-git-promotion-feature", "team-consolidate-feature-branches") in edges
    assert ("team-consolidate-feature-branches", "team-record-feature-promotion") in edges

    # release -> consolidate -> record-git-promotion
    assert effects["team-consolidate-release-branches"]["type"] == (
        "effect:team:consolidate-git-branches"
    )
    assert effects["team-consolidate-release-branches"]["managed_repo_id"] == (
        "${{team-git-promotion-requested.payload.managed_repo_id}}"
    )
    assert effects["team-consolidate-release-branches"]["directory_path"] == (
        "${{team-git-promotion-requested.payload.directory_path ?? null}}"
    )
    assert effects["team-consolidate-release-branches"]["expected_source_commits"] == (
        "${{team-git-promotion-requested.payload.expected_source_commits}}"
    )
    assert effects["team-record-release-promotion"]["type"] == ("effect:team:record-git-promotion")
    assert ("team-git-promotion-release", "team-consolidate-release-branches") in edges
    assert ("team-consolidate-release-branches", "team-record-release-promotion") in edges

    # release recovery -> validate/consolidate the pinned ref -> record release promotion
    recovery_promote = effects["team-promote-release-recovery-branch"]
    assert recovery_promote["type"] == "effect:team:consolidate-git-branches"
    assert recovery_promote["source_branches"] == (
        "${{team-git-release-recovery-requested.payload.source_branches}}"
    )
    assert recovery_promote["target_branch"] == (
        "${{team-git-release-recovery-requested.payload.target_branch}}"
    )
    assert recovery_promote["expected_source_commits"] == (
        "${{team-git-release-recovery-requested.payload.expected_source_commits}}"
    )
    assert recovery_promote["allowed_changed_paths"] == (
        "${{team-git-release-recovery-requested.payload.allowed_changed_paths}}"
    )
    assert recovery_promote["directory_path"] == (
        "${{team-git-release-recovery-requested.payload.directory_path ?? null}}"
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
    assert push["managed_repo_id"] == ("${{team-git-promotion-requested.payload.managed_repo_id}}")
    assert push["directory_path"] == (
        "${{team-git-promotion-requested.payload.directory_path ?? null}}"
    )
    assert push["source_ref"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert push["expected_commit_sha"] == (
        "${{team-git-promotion-requested.payload.expected_commit_sha}}"
    )
    assert push["destination_branch"] == ("${{team-git-promotion-requested.payload.target_branch}}")
    record_canonical = effects["team-record-canonical-promotion"]
    assert record_canonical["type"] == "effect:team:record-git-promotion"
    assert record_canonical["promotion_id"] == (
        "${{team-git-promotion-requested.payload.promotion_id}}"
    )
    assert record_canonical["success"] == "${{team-push-canonical-branch.success}}"
    assert record_canonical["commit_sha"] == "${{team-push-canonical-branch.commit_sha ?? null}}"
    assert record_canonical["result_outputs"]["error_code"] == (
        "${{team-push-canonical-branch.reason ?? null}}"
    )
    assert effects["team-mark-release-published"]["type"] == ("effect:team:mark-release-published")
    assert effects["team-mark-release-published"]["release_package_id"] == (
        "${{team-git-promotion-requested.payload.release_package_id}}"
    )
    assert effects["team-mark-release-published"]["published_commit_sha"] == (
        "${{team-push-canonical-branch.commit_sha ?? null}}"
    )
    assert effects["team-mark-release-published"]["result_outputs"]["error_code"] == (
        "${{team-push-canonical-branch.reason ?? null}}"
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
    assert push["managed_repo_id"] == ("${{team-git-promotion-requested.payload.managed_repo_id}}")
    assert push["source_ref"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert push["expected_commit_sha"] == (
        "${{team-git-promotion-requested.payload.expected_commit_sha}}"
    )
    assert push["destination_branch"] == ("${{team-git-promotion-requested.payload.target_branch}}")

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
