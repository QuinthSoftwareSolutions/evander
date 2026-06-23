from __future__ import annotations

import base64
import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.admin.service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.artifact_spaces import ArtifactSpaceStore
from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.published_artifacts import PublishedArtifactStore
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.workflow.packs import WorkflowPack
from agent_sandbox.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from truman_team_addon.mcp import (
    TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
    TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
)
from truman_team_addon.models import (
    AgentActionRecord,
    AgentAvatar,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    AgentVoiceProfile,
    Deliverable,
    EphemeralAgentPolicy,
    EphemeralAssignment,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamConversation,
    TeamConversationMessage,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRole,
    TeamState,
    TeamTaskState,
)
from truman_team_addon.provider_schemas import (
    TeamAgentActionArtifactQueryDefinition,
    TeamApplyAgentActionEffectDefinition,
    TeamApplyEphemeralActionEffectDefinition,
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamStateQueryDefinition,
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeGovernanceManagersEffectDefinition,
)
from truman_team_addon.providers import (
    ACTION_CONTRACT_SCHEMA_VERSION,
    TEAM_AGENT_GUIDANCE_PATH,
    TEAM_AGENT_GUIDANCE_SECTION_ID,
    TEAM_AGENT_GUIDANCE_VERSION,
    TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
    TEAM_GOVERNANCE_GUIDANCE_VERSION,
    TeamAgentActionArtifactQueryProvider,
    TeamApplyAgentActionEffectProvider,
    TeamApplyEphemeralActionEffectProvider,
    TeamCreateOperatorDirectiveEffectProvider,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamSessionRoutingQueryProvider,
    TeamStateQueryProvider,
    TeamWakeDueAgentsEffectProvider,
    TeamWakeGovernanceManagersEffectProvider,
    _build_prompt,
    _publish_deliverable_artifact_bundle,
)
from truman_team_addon.repository import TeamStateRepository
from truman_team_addon.workflow_packs import team_runtime_v1


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
                    "modules": ["truman_team_addon"],
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
        "team-ephemeral-requested",
        "team-operator-escalation",
        "team-git-promotion-requested",
        "system.chat_assistant",
    ]
    assert "team-wake-due-agents" in _node_ids(config.effects)
    assert "team-notify-operator" in _node_ids(config.effects)
    assert "team-git-promote-review-branch" in _node_ids(config.effects)

    service = AdminService(tmp_path / "runtime")
    document = service.read_workflow_editor_document()
    assert service.read_workflow_editor_document().revision == document.revision
    assert document.output_schemas["team-route-completed-session"]["persistent"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-route-completed-session"]["ignored"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-read-persistent-action"]["valid"] == {
        "type": "boolean"
    }
    assert document.output_schemas["team-wake-due-agents"]["prepared_project_repos"] == {
        "type": "array"
    }
    assert document.output_schemas["team-wake-due-agents"][
        "failed_project_repo_preparations"
    ] == {"type": "array"}
    assert document.output_schemas["team-wake-due-agents"][
        "cost_guard_blocked_team_ids"
    ] == {"type": "array"}
    assert document.output_schemas["team-wake-due-agents"][
        "cost_guard_blocked_teams"
    ] == {"type": "array"}
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
    assert "outputs" not in effects["team-record-review-promotion"]
    assert effects["team-record-review-promotion"]["result_outputs"] == {
        "review_branch": "${{team-git-promote-review-branch.review_branch ?? null}}",
        "host_repo_path": "${{team-git-promote-review-branch.host_repo_path ?? null}}",
        "message": "${{team-git-promote-review-branch.message ?? null}}",
    }
    validation = service.validate_workflow_editor_document(
        config=flattened_config,
        layout=document.layout,
        graphs=graphs,
    )
    assert validation.validation_diagnostics == []
    legacy_validation = service.validate_workflow_editor_document(
        config=_legacy_outputs_config(flattened_config),
        layout=document.layout,
        graphs=graphs,
    )
    assert legacy_validation.validation_diagnostics == []
    current_document = service.read_workflow_editor_document()
    save = service.save_workflow_editor_document(
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
    def __init__(self, *, success: bool = True, message: str = "repo prepared") -> None:
        self.calls: list[dict[str, object]] = []
        self.success = success
        self.message = message

    def clone_repo_into_vm(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            success=self.success,
            vm_name=str(kwargs.get("vm_name") or "vm-backend"),
            message=self.message,
            target_dir="/home/agent/Truman",
            guest_repo_name="Truman",
            remote_url="ssh://agent-sandbox-git-host/git-remotes/Truman.git",
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


def _seed_team(runtime_dir: Path, *, max_ephemeral: int = 0) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=max_ephemeral),
        expected_revision=3,
    )
    repo.create_or_reprioritize_task(
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


def _add_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str,
    state: TeamTaskState = TeamTaskState.PENDING,
) -> None:
    repo.create_or_reprioritize_task(
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

    state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

    assert len(leases) == 1
    assert leases[0]["agent"]["id"] == "backend"
    dispatch_id = leases[0]["dispatch_id"]
    assert state.agent_runtimes["backend"].active_dispatch_id == dispatch_id

    state, result = repo.apply_agent_action(
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


def test_dependency_waiting_owner_not_leased(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    # A pending dependency owned by another agent keeps "task" waiting.
    _add_task(repo, task_id="dep", owner_agent_id="lead")
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

    assert "backend" not in _leased_agent_ids(leases)


def test_dependency_waiting_owner_leased_with_pending_inbox(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _add_task(repo, task_id="dep", owner_agent_id="lead")
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])
    _add_pending_inbox(repo, item_id="inbox-1", recipient_agent_id="backend")

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["reason"] == "inbox"


def test_owner_leaseable_after_dependencies_complete(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _add_task(repo, task_id="dep", owner_agent_id="lead", state=TeamTaskState.ACCEPTED)
    _set_task_graph(repo, task_id="task", dependency_task_ids=["dep"])

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

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

    state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

    assert state.dependency_status("task") == "waiting"
    assert state.subtask_counts("task")["open"] == 1
    assert "backend" in _leased_agent_ids(leases)


def test_lease_payload_tasks_include_derived_graph_fields(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    task_payload = backend_lease["tasks"][0]
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

    _state, leases = repo.lease_due_agents(now=base_time + timedelta(minutes=2))

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

    repo.mark_action_feedback_delivered(
        agent_id="backend",
        source_dispatch_id="dispatch-new",
        delivery_dispatch_id=str(backend_lease["dispatch_id"]),
    )
    state, result = repo.apply_agent_action(
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
    _state, leases = repo.lease_due_agents(now=base_time + timedelta(minutes=20))
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
            "deliverable deliverable has neither a feature branch nor artifact refs; "
            "Git release requests require feature-promoted deliverables"
        ),
        action={"git_release_requests": [{"project_id": "project"}]},
    )

    _state, leases = repo.lease_due_agents(now=base_time + timedelta(minutes=1))

    backend_lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    assert backend_lease["reason"] == "action_feedback"
    feedback = backend_lease["recent_action_feedback"][0]
    assert feedback["action_fields"] == ["git_release_requests"]
    assert any("git_feature_updates" in hint for hint in feedback["correction_hints"])
    assert any("git_refs.feature_branch" in hint for hint in feedback["correction_hints"])


def test_wake_due_agents_provider_prompts_and_mirrors_metadata(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    repo.set_agent_avatar(
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
    repo.update_agent(
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
    assert len(file_copier.calls) == 2
    copied_files = file_copier.calls[0]["files"]
    assert [file["destination_path"] for file in copied_files] == [
        "/artifacts/team-action.schema.json",
        "/artifacts/team-action.example.json",
    ]
    guidance_files = file_copier.calls[1]["files"]
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
        }
    ]
    guidance = str(guidance_files[0]["content"])
    assert "Continue your assigned team-agent work" not in guidance
    assert "managers and project leads may create top-level tasks" not in guidance
    assert "non-lead roles" not in guidance
    assert 'Persistent agent id: "backend"' in guidance
    assert "Current display name: not configured; use your agent id" in guidance
    assert "task creation mandate in your wake prompt" in guidance
    assert "Action surface guide:" in guidance
    assert "Do not accept or reject someone else's task with task_updates" in guidance
    assert "Use review_decisions only for review requests assigned to you" in guidance
    assert "Raw agent or review branches are not release sources" in guidance
    assert "supersedes_deliverable_id" in guidance
    assert "item_type operator_directive" in guidance
    assert "response_requested is false" in guidance
    assert "no-op replies" in guidance
    assert "priority low, normal, or high" in guidance
    assert "Do not start tasks" in guidance
    assert "creating child tasks via task_creations with parent_task_id" in guidance
    schema = json.loads(str(copied_files[0]["content"]))
    assert schema["properties"]["task_updates"]["items"]["required"] == ["task_id"]
    assert "task_id" in schema["properties"]["task_creations"]["items"]["properties"]
    artifact_paths_schema = schema["properties"]["deliverables"]["items"]["properties"][
        "artifact_paths"
    ]
    assert artifact_paths_schema["minItems"] == 1
    assert "default" not in artifact_paths_schema
    assert (
        "supersedes_deliverable_id"
        in schema["properties"]["deliverables"]["items"]["properties"]
    )
    assert "instructions" in schema["properties"]["review_requests"]["items"]["properties"]
    assert schema["properties"]["stand_downs"]["items"]["required"] == [
        "agent_id",
        "reason",
    ]
    assert schema["properties"]["stand_down_revocations"]["items"]["required"] == [
        "agent_id"
    ]
    assert schema["properties"]["review_decisions"]["items"]["properties"]["state"]["enum"] == [
        "accepted",
        "rejected",
        "blocked",
    ]
    review_promotion_shapes = schema["properties"]["git_review_promotions"]["items"]["oneOf"]
    assert review_promotion_shapes[0]["required"] == ["assignment_id"]
    assert review_promotion_shapes[1]["required"] == ["deliverable_id"]
    assert "commit_sha" in review_promotion_shapes[1]["properties"]
    assert "source_branch" in review_promotion_shapes[1]["properties"]
    release_request_property = schema["properties"]["git_release_requests"]
    assert "git_refs.feature_branch" in release_request_property["description"]
    release_request_schema = schema["properties"]["git_release_requests"]["items"]
    assert release_request_schema["additionalProperties"] is False
    assert release_request_schema["required"] == ["project_id", "deliverable_ids"]
    assert set(release_request_schema["properties"]) == {
        "id",
        "project_id",
        "deliverable_ids",
        "release_branch",
        "canonical_branch",
        "summary",
    }
    assert (
        schema["properties"]["status_requests"]["items"]["properties"][
            "response_requested"
        ]["type"]
        == "boolean"
    )
    assert schema["properties"]["status_requests"]["items"]["properties"]["priority"][
        "enum"
    ] == ["low", "normal", "high"]
    assert (
        schema["properties"]["questions"]["items"]["properties"]["response_requested"][
            "type"
        ]
        == "boolean"
    )
    assert schema["properties"]["questions"]["items"]["properties"]["priority"]["enum"] == [
        "low",
        "normal",
        "high",
    ]
    assert (
        schema["properties"]["status_reports"]["items"]["properties"][
            "response_requested"
        ]["type"]
        == "boolean"
    )
    assert schema["properties"]["status_reports"]["items"]["properties"]["priority"][
        "enum"
    ] == ["low", "normal", "high"]
    assert schema["properties"]["chat_replies"]["items"]["required"] == [
        "conversation_id",
        "message_id",
        "body",
    ]
    assert schema["properties"]["operator_chat_messages"]["items"]["required"] == [
        "recipient_operator_user_id",
        "body",
    ]
    example = json.loads(str(copied_files[1]["content"]))
    assert example["schema_version"] == ACTION_CONTRACT_SCHEMA_VERSION
    assert example["chat_replies"][0]["body"]
    assert example["operator_chat_messages"][0]["recipient_operator_user_id"]
    assert example["status_requests"][0]["response_requested"] is True
    assert example["status_requests"][0]["priority"] == "normal"
    assert example["questions"][0]["response_requested"] is True
    assert example["questions"][0]["priority"] == "normal"
    assert example["status_reports"][0]["response_requested"] is False
    assert example["status_reports"][0]["priority"] == "low"
    assert example["stand_downs"] == []
    assert example["stand_down_revocations"] == []
    assert example["review_requests"][0]["instructions"]
    assert example["review_decisions"][0]["state"] == "accepted"
    assert example["git_review_promotions"][0]["deliverable_id"] == "deliverable-id"
    assert "commit_sha" in example["git_review_promotions"][0]
    assert result["dispatches"][0]["action_contract"]["seeded"] is True
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
    assert "team-action.json" in prompt
    assert "team-action.schema.json" in prompt
    assert "team-action.example.json" in prompt
    assert TEAM_AGENT_GUIDANCE_PATH in prompt
    assert TEAM_AGENT_GUIDANCE_SECTION_ID in prompt
    assert "Task creation mandate for this dispatch" in prompt
    assert "do not have create_root_task" in prompt
    assert "You may NOT create top-level tasks" in prompt
    assert "Every task_creations item you submit must set parent_task_id" in prompt
    assert "Your previous Team dispatch had validation errors" not in prompt
    assert "Do not accept or reject someone else's task with task_updates" not in prompt
    assert "Action surface guide:" not in prompt
    for field_name in (
        "task_creations",
        "status_requests",
        "operator_chat_messages",
        "operator_escalations",
        "stand_downs",
        "stand_down_revocations",
        "review_requests",
        "review_decisions",
        "goal_updates",
        "git_review_promotions",
        "git_feature_updates",
        "git_release_requests",
        "ephemeral_requests",
        "ephemeral_acceptances",
        "dependency_status",
        "child_task_ids",
        "subtask_counts",
    ):
        assert field_name in prompt
    assert session_store.calls[0]["metadata"]["team_addon"]["action_contract"] == {
        "seeded": True,
        "schema_version": ACTION_CONTRACT_SCHEMA_VERSION,
        "schema_path": "/artifacts/team-action.schema.json",
        "example_path": "/artifacts/team-action.example.json",
        "action_artifact_path": "/artifacts/team-action.json",
        "copied_paths": [
            "/artifacts/team-action.schema.json",
            "/artifacts/team-action.example.json",
        ],
    }
    assert session_store.calls[1]["metadata"]["team_addon"]["agent_guidance"] == {
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
    assert session_store.calls[2]["metadata"]["team_addon"]["agent_id"] == "backend"


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
    state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = repo.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please answer this after your current wake finishes.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )
    dispatch_id = f"team-chat:{message.id}"
    repo.queue_chat_message_dispatch(
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
    assert resumer.calls[0]["target"] == "session-backend"
    assert resumer.calls[0]["dispatch_id"] == dispatch_id
    prompt = str(resumer.calls[0]["prompt"])
    assert "Team Add-on Chat tab" in prompt
    assert f"Human operator message ID: {message.id}" in prompt
    assert "Please answer this after your current wake finishes." in prompt
    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversation_messages[message.id].status == "running"
    assert reread.agent_runtimes["backend"].active_dispatch_id == dispatch_id
    assert reread.agent_runtimes["backend"].wake_reason == "team_chat"


def test_wake_governance_managers_uses_governance_prompt_without_action_seed(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    repo.register_agent(
        agent_id="portfolio-manager",
        session_id="session-portfolio-manager",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="portfolio-manager",
        member_team_ids=["team"],
        goals=[{"id": "cost", "description": "Keep Team under budget"}],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
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
    inactive_guidance = str(guidance_files[0]["content"])
    assert "not currently assigned to a Team" in inactive_guidance
    assert "Normal Team-agent task" in inactive_guidance
    assert "/artifacts/team-action.json instructions do not apply" in inactive_guidance
    guidance = str(guidance_files[1]["content"])
    assert 'Persistent agent id: "portfolio-manager"' in guidance
    assert "has no team_id" in guidance
    assert "Member teams remain separate execution units" in guidance
    assert "Do not take ownership of member-team project tasks" in guidance
    assert "/artifacts/team-action.json" in guidance
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in guidance
    assert len(resumer.calls) == 1
    assert resumer.calls[0]["target"] == "session-portfolio-manager"
    assert "governance manager" in str(resumer.calls[0]["prompt"])
    assert "Do not take ownership of member-team project tasks" in str(
        resumer.calls[0]["prompt"]
    )
    assert TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL in str(resumer.calls[0]["prompt"])
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
        "requested_tools": [
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
            TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        ],
        "already_allowed_tools": [],
        "granted_tools": [
            TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
            TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
        ],
        "error": None,
    }
    assert session_store.sessions["session-portfolio-manager"].allowed_tools == [
        TEAM_CREATE_OPERATOR_DIRECTIVE_TOOL,
        TEAM_MARK_GOVERNANCE_AGENDA_HANDLED_TOOL,
    ]
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
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    lease = next(lease for lease in leases if lease["agent"]["id"] == "backend")
    lease["agent"] = {**lease["agent"], "effective_role_permissions": ["create_root_task"]}

    prompt = _build_prompt(lease, action_artifact_path="team-action.json")

    assert "effective permissions include create_root_task" in prompt
    assert "You may create top-level tasks by omitting parent_task_id" in prompt
    assert "You may NOT create top-level tasks" not in prompt


def test_build_prompt_includes_subordinate_role_context(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    repo.create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        description="Review code for correctness; do not own implementation work.",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
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

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    lease = next(lease for lease in leases if lease["agent"]["id"] == "lead")
    prompt = _build_prompt(lease, action_artifact_path="team-action.json")
    context = json.loads(prompt.split("Team context JSON:\n", 1)[1].split("\n\nTeam action contract", 1)[0])

    assert context["subordinate_agent_ids"] == ["backend", "reviewer"]
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
    assert "Reviewer roles should generally receive review_requests" in prompt


def test_wake_due_agents_provider_prompts_with_latest_action_feedback_once(
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
    assert "Most recent Team action feedback (one-shot)" in prompt
    assert "Your previous Team dispatch had validation errors" in prompt
    assert "action-feedback" in prompt
    assert "Only the task owner can create deliverables for that task." in prompt
    reread = TeamStateRepository(tmp_path).read()
    record = reread.action_records["action-feedback"]
    assert record.feedback_delivered_at is not None
    assert record.feedback_delivered_by_dispatch_id == result["dispatches"][0][
        "dispatch_id"
    ]


def test_wake_due_agents_provider_rotates_context_when_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    repo.request_agent_context_rotation(
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
    assert session_store.sessions["session-backend"].metadata["team_addon"][
        "context_rotation_pending"
    ] is False
    assert session_store.sessions["session-backend"].metadata["team_addon"][
        "last_context_rotated_at"
    ]


def test_wake_due_agents_provider_rotates_every_wake_policy(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    repo.update_agent(
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
    assert session_store.sessions["session-backend"].metadata["team_addon"][
        "context_clearing_policy"
    ] == "every_wake"


def test_wake_due_agents_provider_rotates_idle_agent_when_only_rotation_pending(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    repo.update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    repo.request_agent_context_rotation(
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
    repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    repo.update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    runtime = repo.read().agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    _state, leases = repo.lease_due_agents(now=base_time)
    dispatch_id = str(leases[0]["dispatch_id"])
    repo.record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="thread-idle",
    )

    state, result = repo.apply_agent_action(
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
    _state, leases = repo.lease_due_agents(now=base_time + timedelta(minutes=20))
    assert "backend" not in _leased_agent_ids(leases)


def test_substantive_idle_rotation_action_can_rearm_auto_context_clearing(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    repo.update_task(task_id="task", state=TeamTaskState.ACCEPTED)

    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = str(leases[0]["dispatch_id"])
    repo.record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="thread-idle",
    )

    state, result = repo.apply_agent_action(
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
    repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    repo.update_task(task_id="task", state=TeamTaskState.ACCEPTED)
    assert repo.read().agent_runtimes["backend"].context_rotation_pending is True

    state = repo.update_agent(
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
    repo.request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="manual refresh",
    )

    state = repo.update_agent(
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
    repo.request_agent_context_rotation(agent_id="backend", requested_by="operator:test")
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
    repo.update_team(
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
    assert "/home/agent/Truman" in prompt
    assert "The project host checkout is not mounted in the guest" in prompt
    assert session_store.calls[2]["metadata"]["team_addon"]["project_repo"] == project_repo
    assert session_store.calls[3]["metadata"]["team_addon"]["agent_id"] == "backend"


def test_wake_due_agents_provider_skips_already_seeded_action_contract(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {
                "seeded": True,
                "schema_version": ACTION_CONTRACT_SCHEMA_VERSION,
                "schema_path": "/artifacts/team-action.schema.json",
                "example_path": "/artifacts/team-action.example.json",
                "action_artifact_path": "/artifacts/team-action.json",
            },
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
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
    assert result["dispatches"][0]["action_contract"]["seeded"] is False
    assert result["dispatches"][0]["action_contract"]["reason"] == "already_seeded"
    assert file_copier.calls == []
    assert repo.read().agent_runtimes["backend"].active_dispatch_id is not None


def test_wake_due_agents_provider_reseeds_stale_action_contract(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {
                "seeded": True,
                "schema_version": 3,
                "schema_path": "/artifacts/team-action.schema.json",
                "example_path": "/artifacts/team-action.example.json",
                "action_artifact_path": "/artifacts/team-action.json",
            },
            "agent_guidance": {
                "seeded": True,
                "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
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
    assert result["dispatches"][0]["action_contract"]["seeded"] is True
    assert result["dispatches"][0]["action_contract"]["schema_version"] == (
        ACTION_CONTRACT_SCHEMA_VERSION
    )
    assert len(file_copier.calls) == 1
    assert session_store.calls[0]["metadata"]["team_addon"]["action_contract"][
        "schema_version"
    ] == ACTION_CONTRACT_SCHEMA_VERSION


def test_wake_due_agents_provider_reseeds_stale_agent_guidance(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {
                "seeded": True,
                "schema_version": ACTION_CONTRACT_SCHEMA_VERSION,
                "schema_path": "/artifacts/team-action.schema.json",
                "example_path": "/artifacts/team-action.example.json",
                "action_artifact_path": "/artifacts/team-action.json",
            },
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
    assert result["dispatches"][0]["action_contract"]["seeded"] is False
    assert result["dispatches"][0]["agent_guidance"]["seeded"] is True
    assert len(file_copier.calls) == 1
    copied_file = file_copier.calls[0]["files"][0]
    assert copied_file["destination_path"] == TEAM_AGENT_GUIDANCE_PATH
    assert copied_file["merge_strategy"] == "managed_section"
    assert copied_file["section_id"] == TEAM_AGENT_GUIDANCE_SECTION_ID
    assert session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"][
        "guidance_version"
    ] == TEAM_AGENT_GUIDANCE_VERSION


def test_wake_due_agents_provider_clears_inactive_agent_guidance_metadata(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {
                "seeded": True,
                "schema_version": ACTION_CONTRACT_SCHEMA_VERSION,
                "schema_path": "/artifacts/team-action.schema.json",
                "example_path": "/artifacts/team-action.example.json",
                "action_artifact_path": "/artifacts/team-action.json",
            },
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
    repo.update_agent(
        agent_id="backend",
        display_name="Beatrice",
        set_display_name=True,
        expected_revision=repo.read().revision,
    )
    session_store = FakeSessionStore()
    session_store.sessions["session-backend"].metadata = {
        "team_addon": {
            "action_contract": {
                "seeded": True,
                "schema_version": ACTION_CONTRACT_SCHEMA_VERSION,
                "schema_path": "/artifacts/team-action.schema.json",
                "example_path": "/artifacts/team-action.example.json",
                "action_artifact_path": "/artifacts/team-action.json",
            },
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
    assert result["dispatches"][0]["action_contract"]["seeded"] is False
    assert result["dispatches"][0]["agent_guidance"]["seeded"] is True
    assert result["dispatches"][0]["agent_guidance"]["display_name"] == "Beatrice"
    assert len(file_copier.calls) == 1
    copied_file = file_copier.calls[0]["files"][0]
    assert copied_file["destination_path"] == TEAM_AGENT_GUIDANCE_PATH
    assert copied_file["merge_strategy"] == "managed_section"
    assert copied_file["section_id"] == TEAM_AGENT_GUIDANCE_SECTION_ID
    assert 'Current display name: "Beatrice"' in str(copied_file["content"])
    prompt = str(resumer.calls[0]["prompt"])
    assert "Beatrice" not in prompt
    assert '"display_name"' not in prompt
    assert session_store.calls[0]["metadata"]["team_addon"]["agent_guidance"][
        "display_name"
    ] == "Beatrice"


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
    assert len(session_store.calls) == 2
    assert "action_contract" in session_store.calls[0]["metadata"]["team_addon"]
    assert "agent_guidance" in session_store.calls[1]["metadata"]["team_addon"]


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
        key: value
        for key, value in failed_repo_preparation.items()
        if key != "dispatch_id"
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


def test_action_artifact_query_and_apply_provider(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    repo.register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _state, backend_conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    _state, backend_message = repo.record_operator_chat_message(
        conversation_id=backend_conversation.id,
        body="Backend private chat",
        created_by="operator:alice@example.com",
        expected_revision=repo.read().revision,
    )
    _state, reviewer_conversation, _created = repo.create_or_get_conversation(
        agent_id="reviewer",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        expected_revision=repo.read().revision,
    )
    repo.record_operator_chat_message(
        conversation_id=reviewer_conversation.id,
        body="Reviewer private chat",
        created_by="operator:bob@example.com",
        expected_revision=repo.read().revision,
    )
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = next(
        lease["dispatch_id"]
        for lease in leases
        if lease["agent"]["id"] == "backend"
    )
    action = {
        "agent_id": "backend",
        "dispatch_id": dispatch_id,
        "deliverables": [
            {
                "task_id": "task",
                "summary": "Backend work is ready.",
                "artifact_refs": ["artifact-space:/teams/team/projects/backend/spec"],
            }
        ],
    }
    query = TeamAgentActionArtifactQueryProvider()
    query.initialize(
        TeamAgentActionArtifactQueryDefinition(
            id="read",
            display_name="Read action",
            type="query:team:agent-action-artifact",
            session_id="session-backend",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_artifact_reader=FakeArtifactReader(json.dumps(action)),
        ),
    )
    parsed = query.execute()
    assert parsed["valid"] is True

    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action=parsed["action"],
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_metadata_updater=FakeMetadataUpdater(),
        ),
    )

    result = apply.execute()

    assert result["success"] is True
    assert set(result["state"]["conversations"]) == {backend_conversation.id}
    assert set(result["state"]["conversation_messages"]) == {backend_message.id}
    reread = TeamStateRepository(tmp_path).read()
    assert len(reread.deliverables) == 1
    assert reread.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


def test_apply_agent_action_publishes_declared_deliverable_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
    archive = tmp_path / "payload.tar.gz"
    report = tmp_path / "report.txt"
    report.write_text("report\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(report, arcname="reports/report.txt")
    archive_content = archive.read_bytes()
    commands: list[str] = []

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del resolved_config
        effect = self.effect_definition
        commands.append(str(effect.command))  # type: ignore[attr-defined]
        return {
            "success": True,
            "session_id": "session-backend",
            "execution_backend": "container_worker_vm",
            "stdout": json.dumps(
                {
                    "success": True,
                    "archive_format": "tar.gz",
                    "artifacts_root": "/artifacts",
                    "source_paths": ["reports/report.txt"],
                    "source_file_count": 1,
                    "source_byte_count": len("report\n"),
                    "content_base64": base64.b64encode(archive_content).decode("ascii"),
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "agent_id": "backend",
                "dispatch_id": dispatch_id,
                "deliverables": [
                    {
                        "id": "deliverable-1",
                        "task_id": "task",
                        "summary": "Backend work is ready.",
                        "artifact_paths": ["reports/report.txt"],
                    }
                ],
            },
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_metadata_updater=FakeMetadataUpdater(),
        ),
    )

    result = apply.execute()

    assert result["success"] is True
    artifact_ref = "artifact-space:/teams/team/projects/project/deliverables/deliverable-1"
    artifact_key = f"team/team/project/deliverable-1/{dispatch_id}/bundle"
    assert result["published_deliverable_artifacts"][0]["artifact_ref"] == artifact_ref
    assert "\"reports/report.txt\"" in commands[0]
    reread = TeamStateRepository(tmp_path).read()
    assert reread.deliverables["deliverable-1"].artifact_refs == [artifact_ref]
    assert PublishedArtifactStore(tmp_path).get(artifact_key).metadata.key == artifact_key
    latest = ArtifactSpaceStore(tmp_path).read().latest_versions_for_space(
        "/teams/team/projects/project/deliverables/deliverable-1",
        artifact_id="bundle",
    )
    assert latest[0].artifact_key == artifact_key
    inbox = list(reread.inbox.values())[-1]
    assert inbox.payload["artifact_refs"] == [artifact_ref]


def test_apply_agent_action_publishes_artifacts_for_session_resolved_action(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
    archive = tmp_path / "payload.tar.gz"
    report = tmp_path / "report.txt"
    report.write_text("report\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(report, arcname="reports/report.txt")
    archive_content = archive.read_bytes()

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        return {
            "success": True,
            "session_id": "session-backend",
            "execution_backend": "container_worker_vm",
            "stdout": json.dumps(
                {
                    "success": True,
                    "archive_format": "tar.gz",
                    "artifacts_root": "/artifacts",
                    "source_paths": ["reports/report.txt"],
                    "source_file_count": 1,
                    "source_byte_count": len("report\n"),
                    "content_base64": base64.b64encode(archive_content).decode("ascii"),
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "dispatch_id": dispatch_id,
                "deliverables": [
                    {
                        "id": "deliverable-1",
                        "task_id": "task",
                        "summary": "Backend work is ready.",
                        "artifact_paths": ["reports/report.txt"],
                    }
                ],
            },
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_metadata_updater=FakeMetadataUpdater(),
        ),
    )

    result = apply.execute()

    assert result["success"] is True
    artifact_ref = "artifact-space:/teams/team/projects/project/deliverables/deliverable-1"
    assert result["published_deliverable_artifacts"][0]["artifact_ref"] == artifact_ref
    reread = TeamStateRepository(tmp_path).read()
    assert reread.deliverables["deliverable-1"].producer_agent_id == "backend"
    assert reread.deliverables["deliverable-1"].artifact_refs == [artifact_ref]


def test_apply_agent_action_treats_empty_artifact_paths_as_omitted(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise AssertionError("empty artifact_paths should not publish artifacts")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "agent_id": "backend",
                "dispatch_id": dispatch_id,
                "deliverables": [
                    {
                        "id": "deliverable-1",
                        "task_id": "task",
                        "summary": "Backend work is ready.",
                        "artifact_paths": [],
                    }
                ],
            },
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_metadata_updater=FakeMetadataUpdater(),
        ),
    )

    result = apply.execute()

    assert result["success"] is True
    assert "published_deliverable_artifacts" not in result
    reread = TeamStateRepository(tmp_path).read()
    assert reread.deliverables["deliverable-1"].artifact_refs == []
    records = list(reread.action_records.values())
    assert records[0].success is True
    assert "artifact_paths" not in records[0].action["deliverables"][0]


def test_apply_agent_action_records_deliverable_artifact_publish_failure(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]

    def fail_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        raise RuntimeError("artifact file not found: reports/missing.txt")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fail_execute)
    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "agent_id": "backend",
                "dispatch_id": dispatch_id,
                "deliverables": [
                    {
                        "id": "deliverable-1",
                        "task_id": "task",
                        "summary": "Backend work is ready.",
                        "artifact_paths": ["reports/missing.txt"],
                    }
                ],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = apply.execute()

    assert result["success"] is False
    assert result["applied"] is False
    assert "failed to publish artifact_paths" in result["errors"][0]
    reread = TeamStateRepository(tmp_path).read()
    assert reread.deliverables == {}
    records = list(reread.action_records.values())
    assert len(records) == 1
    assert records[0].success is False
    assert records[0].dispatch_id == dispatch_id
    assert "failed to publish artifact_paths" in (records[0].error or "")
    assert records[0].action["deliverables"][0]["artifact_paths"] == ["reports/missing.txt"]
    assert reread.agent_runtimes["backend"].active_dispatch_id is None


def test_apply_agent_action_encodes_unsafe_ids_for_deliverable_artifact_spaces(
    tmp_path: Path, monkeypatch
) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="Team A",
        name="Team A",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="Project A",
        team_id="Team A",
        name="Project A",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=1,
    )
    repo.create_role(
        role_id="project-backend",
        team_id="Team A",
        project_id="Project A",
        name="Backend",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="Team A",
        project_id="Project A",
        role_id="project-backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=3,
    )
    repo.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="Project A",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=4,
    )
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
    archive = tmp_path / "payload.tar.gz"
    report = tmp_path / "report.txt"
    report.write_text("report\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(report, arcname="reports/report.txt")
    archive_content = archive.read_bytes()

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        return {
            "success": True,
            "session_id": "session-backend",
            "execution_backend": "container_worker_vm",
            "stdout": json.dumps(
                {
                    "success": True,
                    "archive_format": "tar.gz",
                    "artifacts_root": "/artifacts",
                    "source_paths": ["reports/report.txt"],
                    "source_file_count": 1,
                    "source_byte_count": len("report\n"),
                    "content_base64": base64.b64encode(archive_content).decode("ascii"),
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "agent_id": "backend",
                "dispatch_id": dispatch_id,
                "deliverables": [
                    {
                        "id": "deliverable 1",
                        "task_id": "task",
                        "summary": "Backend work is ready.",
                        "artifact_paths": ["reports/report.txt"],
                    }
                ],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = apply.execute()

    assert result["success"] is True
    artifact_record = result["published_deliverable_artifacts"][0]
    artifact_space_path = artifact_record["artifact_space_path"]
    assert "Team A" not in artifact_space_path
    assert "Project A" not in artifact_space_path
    assert "deliverable 1" not in artifact_space_path
    assert artifact_record["artifact_ref"] == f"artifact-space:{artifact_space_path}"
    assert PublishedArtifactStore(tmp_path).get(artifact_record["artifact_key"]).metadata.key == (
        artifact_record["artifact_key"]
    )
    artifact_state = ArtifactSpaceStore(tmp_path).read()
    assert artifact_state.spaces[artifact_space_path].metadata == {
        "team_id": "Team A",
        "project_id": "Project A",
        "deliverable_id": "deliverable 1",
        "source": "team_addon",
    }
    latest = artifact_state.latest_versions_for_space(
        artifact_space_path,
        artifact_id="bundle",
    )
    assert latest[0].artifact_key == artifact_record["artifact_key"]
    reread = TeamStateRepository(tmp_path).read()
    assert reread.deliverables["deliverable 1"].artifact_refs == [
        artifact_record["artifact_ref"]
    ]


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
    versions = ArtifactSpaceStore(tmp_path).read().versions_for_space(
        "/teams/team/projects/project/deliverables/deliverable-1",
        artifact_id="bundle",
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


def test_session_routing_ignores_non_team_dispatch_for_persistent_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
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
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
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
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
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


def test_invalid_action_artifact_increments_no_progress(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
    query = TeamAgentActionArtifactQueryProvider()
    query.initialize(
        TeamAgentActionArtifactQueryDefinition(
            id="read",
            display_name="Read action",
            type="query:team:agent-action-artifact",
            session_id="session-backend",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            session_artifact_reader=FakeArtifactReader("not json"),
        ),
    )
    parsed = query.execute()
    assert parsed["valid"] is False

    apply = TeamApplyAgentActionEffectProvider()
    apply.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            valid=False,
            error=parsed["error"],
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = apply.execute()

    assert result["success"] is False
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agent_runtimes["backend"].no_progress_count == 1


def test_apply_agent_action_requests_ephemeral_assignment_and_publishes_event(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)
    repo.set_agent_avatar(
        agent_id="backend",
        avatar=AgentAvatar(
            asset_id="av_ephemeralprivate0000000000000001",
            source="generated",
            mime_type="image/webp",
            sha256="c" * 64,
            style_preset="professional",
            prompt_note="ephemeral private note",
            model="gpt-image-2",
            preview_id="av_previewephemeral000000000001",
        ),
        expected_revision=repo.read().revision,
    )
    repo.update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(
            voice="sage",
            speaking_style="ephemeral private voice direction",
        ),
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
    provider = TeamApplyAgentActionEffectProvider()
    provider.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            dispatch_id=dispatch_id,
            action={
                "agent_id": "backend",
                "dispatch_id": dispatch_id,
                "ephemeral_requests": [
                    {
                        "id": "ephemeral-1",
                        "task_id": "task",
                        "title": "Draft implementation",
                        "prompt": "Implement a narrow backend slice.",
                        "execution_backend": "container_worker_vm",
                        "metadata": {"purpose": "unit-test"},
                    }
                ],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created_ephemeral_assignment_ids"] == ["ephemeral-1"]
    reread = TeamStateRepository(tmp_path).read()
    assignment = reread.ephemeral_assignments["ephemeral-1"]
    assert assignment.parent_agent_id == "backend"
    assert assignment.session_id.startswith("team-eph-")
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.ephemeral.requested"
    assert events[0].payload["assignment_id"] == "ephemeral-1"
    assert events[0].payload["metadata"]["team_addon"]["ephemeral_assignment_id"] == "ephemeral-1"
    prompt = str(events[0].payload["prompt"])
    assert "ephemeralprivate" not in prompt
    assert "ephemeral private note" not in prompt
    assert '"avatar"' not in prompt
    assert "ephemeral private voice direction" not in prompt
    assert '"voice_profile"' not in prompt


def test_apply_agent_action_publishes_operator_escalation_event(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)

    def _set_target(state):
        state.teams["team"].operator_communication_target_id = "operator:ops-channel"
        return state

    repo.update(_set_target)

    provider = TeamApplyAgentActionEffectProvider()
    provider.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-manager",
            action={
                "agent_id": "manager",
                "operator_escalations": [
                    {
                        "title": "Need a decision",
                        "message": "Operator input required.",
                        "urgency": "high",
                    }
                ],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(result["operator_escalation_event_ids"]) == 1
    assert len(result["published_event_ids"]) == 1
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.operator.escalation_requested"
    assert events[0].payload == {
        "team_id": "team",
        "manager_agent_id": "manager",
        "communication_target_id": "operator:ops-channel",
        "title": "Need a decision",
        "message": "Operator input required.",
        "urgency": "high",
    }


def test_apply_agent_action_routes_manager_escalation_to_governance(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        goals=[{"id": "cost", "description": "Keep teams within budget"}],
        expected_revision=repo.read().revision,
    )
    repo.update_team(
        team_id="team",
        escalate_to_governance=True,
        expected_revision=repo.read().revision,
    )

    provider = TeamApplyAgentActionEffectProvider()
    provider.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-manager",
            action={
                "agent_id": "manager",
                "operator_escalations": [
                    {
                        "title": "Need policy guidance",
                        "message": "Team needs a new staffing policy.",
                        "urgency": "normal",
                    }
                ],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["applied"] is True
    assert len(result["created_governance_agenda_item_ids"]) == 1
    assert result["operator_escalation_event_ids"] == []
    assert result.get("created_inbox_item_ids") == []
    assert "published_event_ids" not in result

    state = TeamStateRepository(tmp_path).read()
    agenda_id = result["created_governance_agenda_item_ids"][0]
    agenda_item = state.governance_agenda[agenda_id]
    assert agenda_item.item_type == "team_escalation"
    assert agenda_item.title == "Need policy guidance"
    assert agenda_item.summary == "Team needs a new staffing policy."
    assert agenda_item.team_id == "team"
    assert agenda_item.payload["escalation_id"].startswith("escalation-")
    assert agenda_item.payload["governance_group_id"] == "portfolio"
    assert state.governance_runtimes["portfolio"].next_wake_at is None

    _state, leases = TeamStateRepository(tmp_path).lease_due_governance_managers(
        now=agenda_item.created_at,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "team_escalation"
    assert leases[0]["agenda"][0]["id"] == agenda_id


def test_apply_agent_action_publishes_git_promotion_event(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)

    def _set_git(state):
        from truman_team_addon.models import ProjectGitConfig

        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name="Truman.git",
        )
        return state

    repo.update(_set_git)
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )
    repo.apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/eph-1/ephemeral-1",
            "commit_sha": "abc1234",
        },
    )

    provider = TeamApplyAgentActionEffectProvider()
    provider.initialize(
        TeamApplyAgentActionEffectDefinition(
            id="apply",
            display_name="Apply",
            type="effect:team:apply-agent-action",
            session_id="session-backend",
            action={
                "agent_id": "backend",
                "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    assert len(result["published_event_ids"]) == 1
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    git_events = [
        event for event in events if event.event_type == "team.git.promotion_requested"
    ]
    assert len(git_events) == 1
    payload = git_events[0].payload
    assert payload["promotion_kind"] == "review"
    assert payload["source_branch"] == "agents/eph-1/ephemeral-1"
    assert payload["target_branch"] == "team/team/reviews/ephemeral-1"
    assert payload["host_repo_path"] == "/opt/repos/Truman"
    assert payload["bare_repo_name"] == "Truman.git"


def test_ephemeral_lifecycle_collects_action_routes_parent_and_cleans_up(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)
    state, request_result = repo.request_ephemeral_assignments(
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

    apply = TeamApplyEphemeralActionEffectProvider()
    apply.initialize(
        TeamApplyEphemeralActionEffectDefinition(
            id="apply-ephemeral",
            display_name="Apply Ephemeral",
            type="effect:team:apply-ephemeral-action",
            session_id=assignment.session_id,
            action={
                "ephemeral_assignment_id": assignment.id,
                "summary": "Ephemeral work is ready.",
                "artifact_refs": ["artifact-space:/teams/team/ephemeral/result"],
            },
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    result = apply.execute()
    assert result["success"] is True
    assert result["state"] == "completed"

    reread = TeamStateRepository(tmp_path).read()
    assert reread.ephemeral_assignments[assignment.id].summary == "Ephemeral work is ready."
    inbox_items = list(reread.inbox.values())
    assert inbox_items[-1].recipient_agent_id == "backend"
    assert inbox_items[-1].payload["ephemeral_assignment_id"] == assignment.id
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert any(event.event_type == "team.ephemeral.completed" for event in events)

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

    _state, acceptance = repo.apply_agent_action(
        session_id="session-backend",
        action={
            "agent_id": "backend",
            "ephemeral_acceptances": [
                {"assignment_id": assignment.id, "deliverable_id": "deliverable-ephemeral"}
            ],
        },
    )
    assert acceptance["accepted_ephemeral_assignment_ids"] == [assignment.id]
    final = TeamStateRepository(tmp_path).read()
    assert final.deliverables["deliverable-ephemeral"].producer_agent_id == "backend"


def test_mark_ephemeral_spawned_records_create_failure(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path, max_ephemeral=1)
    state, request_result = repo.request_ephemeral_assignments(
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
    repo.store.write_object(state.model_dump(mode="json"))
    project_query = TeamStateQueryProvider()
    project_query.initialize(
        TeamStateQueryDefinition(
            id="state",
            display_name="State",
            type="query:team:state",
            project_id="alpha-project",
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    project_state = project_query.execute()["state"]

    assert set(project_state["teams"]) == {"alpha-team"}
    assert set(project_state["projects"]) == {"alpha-project"}
    assert set(project_state["agents"]) == {"alpha-lead", "alpha-dev", "alpha-reviewer"}
    assert "voice_profile" not in project_state["agents"]["alpha-dev"]
    assert set(project_state["agent_runtimes"]) == {"alpha-dev"}
    assert set(project_state["tasks"]) == {"alpha-task", "alpha-helper"}
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
    assert project_state["release_packages"]["alpha-release"][
        "git_promotion_record_ids"
    ] == ["alpha-release-promotion"]
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
    assert all(
        "beta-" not in json.dumps(row)
        for row in (
            project_runtime_operations["agents"]
            + project_runtime_operations["attention"]
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
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    agent_state = agent_query.execute()["state"]

    assert set(agent_state["teams"]) == {"alpha-team"}
    assert set(agent_state["projects"]) == {"alpha-project"}
    assert set(agent_state["agents"]) == {"alpha-dev"}
    assert "voice_profile" not in agent_state["agents"]["alpha-dev"]
    assert set(agent_state["agent_runtimes"]) == {"alpha-dev"}
    assert set(agent_state["tasks"]) == {"alpha-task"}
    # alpha-helper is out of this agent's scope, so its edges are filtered out,
    # but dependency_status still reflects global truth (alpha-helper is pending).
    scoped_task = agent_state["tasks"]["alpha-task"]
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
    assert agent_state["release_packages"]["alpha-release"][
        "git_promotion_record_ids"
    ] == ["alpha-release-promotion"]
    assert set(agent_state["ephemeral_assignments"]) == {"alpha-ephemeral"}
    agent_runtime_operations = agent_state["runtime_operations"]
    assert {row["id"] for row in agent_runtime_operations["agents"]} == {"alpha-dev"}
    assert {
        row["inbox_item_id"]
        for row in agent_runtime_operations["attention"]
        if row.get("inbox_item_id")
    } == {"alpha-inbox"}
    assert all(
        "beta-" not in json.dumps(row)
        for row in (
            agent_runtime_operations["agents"]
            + agent_runtime_operations["attention"]
        )
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
    assert effects["team-mark-ephemeral-cleaned-up"]["type"] == (
        "effect:team:mark-ephemeral-cleaned-up"
    )
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
    assert ("team-read-ephemeral-action", "team-apply-ephemeral-action") in edges
    assert ("team-destroy-ephemeral-agent", "team-mark-ephemeral-cleaned-up") in edges


def test_team_runtime_pack_routes_operator_escalation_to_communication() -> None:
    pack = _team_runtime_sections()
    triggers = {node["id"]: node for node in pack["triggers"]}
    effects = {node["id"]: node for node in pack["effects"]}
    edges = {(edge["source_id"], edge["target_id"]) for edge in pack["edges"]}

    assert triggers["team-operator-escalation"]["type"] == "trigger:event"
    assert triggers["team-operator-escalation"]["event_type"] == (
        "team.operator.escalation_requested"
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
    assert effects["team-record-feature-promotion"]["type"] == (
        "effect:team:record-git-promotion"
    )
    assert ("team-git-promotion-feature", "team-consolidate-feature-branches") in edges
    assert ("team-consolidate-feature-branches", "team-record-feature-promotion") in edges

    # release -> consolidate -> record-git-promotion
    assert effects["team-consolidate-release-branches"]["type"] == (
        "effect:team:consolidate-git-branches"
    )
    assert effects["team-record-release-promotion"]["type"] == (
        "effect:team:record-git-promotion"
    )
    assert ("team-git-promotion-release", "team-consolidate-release-branches") in edges
    assert ("team-consolidate-release-branches", "team-record-release-promotion") in edges

    # canonical_push -> push-branch -> record-git-promotion -> mark-release-published
    push = effects["team-push-canonical-branch"]
    assert push["type"] == "effect:git:push-branch"
    assert push["local_branch"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert push["origin_branch"] == "${{team-git-promotion-requested.payload.target_branch}}"
    record_canonical = effects["team-record-canonical-promotion"]
    assert record_canonical["type"] == "effect:team:record-git-promotion"
    assert record_canonical["promotion_id"] == (
        "${{team-git-promotion-requested.payload.promotion_id}}"
    )
    assert record_canonical["success"] == "${{team-push-canonical-branch.success}}"
    assert effects["team-mark-release-published"]["type"] == (
        "effect:team:mark-release-published"
    )
    assert effects["team-mark-release-published"]["release_package_id"] == (
        "${{team-git-promotion-requested.payload.release_package_id}}"
    )
    assert ("team-git-promotion-canonical", "team-push-canonical-branch") in edges
    assert ("team-push-canonical-branch", "team-record-canonical-promotion") in edges
    assert (
        "team-record-canonical-promotion",
        "team-mark-release-published",
    ) in edges


def test_team_runtime_pack_only_pushes_release_branch_to_canonical() -> None:
    pack = _team_runtime_sections()
    push_nodes = [
        node for node in pack["effects"] if node["type"] == "effect:git:push-branch"
    ]
    # The release/canonical path is the ONLY route allowed to push to a canonical remote;
    # ephemeral/review/feature branches must never reach effect:git:push-branch.
    assert [node["id"] for node in push_nodes] == ["team-push-canonical-branch"]
    push = push_nodes[0]
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
