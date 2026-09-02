from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from config_helpers import canonical_worker_config
from evander.config import GatewayConfig
from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import AgentDestroyEffectDefinition
from evander.models import utc_now
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.queries.providers.base import QueryRuntimeContext
from evander_team_addon.models import (
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStatus,
    EphemeralAssignment,
    EphemeralAssignmentState,
    EphemeralAgentPolicy,
    GovernanceRuntime,
    ReleasePackage,
    RolePermission,
    TeamCaseStatus,
    TeamDispatch,
    TeamDispatchStatus,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.provider_schemas import (
    TeamPublishOutboxEventsEffectDefinition,
    TeamRegisterAgentEffectDefinition,
    TeamRestoreAgentEffectDefinition,
    TeamRetireAgentEffectDefinition,
    TeamSessionRoutingQueryDefinition,
)
from evander_team_addon.providers import (
    TeamPublishOutboxEventsEffectProvider,
    TeamRegisterAgentEffectProvider,
    TeamRestoreAgentEffectProvider,
    TeamRetireAgentEffectProvider,
    TeamSessionRoutingQueryProvider,
    register_providers,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.workflow_agent_lifecycle_service import (
    TeamWorkflowAgentLifecycleService,
)


@dataclass
class _Session:
    session_id: str
    directory_path: str = "root"
    expires_at: object = None


class _Sessions:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = {session.session_id: session for session in sessions}

    def get_session(self, session_id: str) -> _Session | None:
        return self.sessions.get(session_id)


class _ListSessionsOnly:
    def __init__(self, *sessions: _Session) -> None:
        self._sessions = list(sessions)

    def list_sessions(self) -> list[_Session]:
        return list(self._sessions)


class _DestroyingAdminService:
    def __init__(self, sessions: _Sessions) -> None:
        self._sessions = sessions
        self.destroyed_session_ids: list[str] = []

    def delete_admin_agent(self, target: str, *, backend: str) -> dict[str, object]:
        self.destroyed_session_ids.append(target)
        self._sessions.sessions.pop(target, None)
        return {
            "success": True,
            "target_name": target,
            "session_id": target,
            "status": "destroyed",
            "backend": backend,
        }


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    admin = TeamAdminService(repo)
    admin.create_team(
        team_id="team",
        name="Lifecycle Team",
        manager_agent_id="manager",
        manager_session_id="manager-session",
        expected_revision=0,
    )
    admin.create_role(
        role_id="support",
        team_id="team",
        name="Support",
        expected_revision=repo.read().revision,
    )
    return repo


def _register(
    runtime_dir: Path,
    sessions: object,
    *,
    session_id: str,
    agent_id: str | None = None,
    project_id: str | None = None,
    reports_to_agent_id: str | None = "manager",
    directory_path: str = "root",
    expected_revision: int | None = None,
) -> dict[str, object]:
    provider = TeamRegisterAgentEffectProvider()
    provider.initialize(
        TeamRegisterAgentEffectDefinition(
            id=f"register-{session_id}",
            display_name="Register Team agent",
            type="effect:team:register-agent",
            session_id=session_id,
            team_id="team",
            role_id="support",
            agent_id=agent_id,
            project_id=project_id,
            reports_to_agent_id=reports_to_agent_id,
            expected_revision=expected_revision,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(runtime_dir)},
            session_lookup=sessions,
            metadata={"directory_path": directory_path},
        ),
    )
    return provider.execute()


def _restore(
    runtime_dir: Path,
    sessions: object,
    *,
    agent_id: str,
    new_session_id: str,
    expected_old_session_id: str,
    expected_team_id: str = "team",
    role_id: str = "support",
    project_id: str | None = None,
    reports_to_agent_id: str | None = "manager",
    directory_path: str = "root",
    expected_revision: int | None = None,
) -> dict[str, object]:
    provider = TeamRestoreAgentEffectProvider()
    provider.initialize(
        TeamRestoreAgentEffectDefinition(
            id=f"restore-{agent_id}",
            display_name="Restore Team agent",
            type="effect:team:restore-agent",
            agent_id=agent_id,
            new_session_id=new_session_id,
            expected_old_session_id=expected_old_session_id,
            expected_team_id=expected_team_id,
            role_id=role_id,
            project_id=project_id,
            reports_to_agent_id=reports_to_agent_id,
            expected_revision=expected_revision,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(runtime_dir)},
            session_lookup=sessions,
            metadata={"directory_path": directory_path},
        ),
    )
    return provider.execute()


def _retire(
    runtime_dir: Path,
    sessions: object,
    *,
    agent_id: str,
    session_id: str,
    expected_team_id: str = "team",
    expected_revision: int | None = None,
) -> dict[str, object]:
    provider = TeamRetireAgentEffectProvider()
    provider.initialize(
        TeamRetireAgentEffectDefinition(
            id=f"retire-{agent_id}",
            display_name="Retire Team agent",
            type="effect:team:retire-agent",
            agent_id=agent_id,
            expected_session_id=session_id,
            expected_team_id=expected_team_id,
            expected_revision=expected_revision,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(runtime_dir)},
            session_lookup=sessions,
            metadata={"directory_path": "root"},
        ),
    )
    return provider.execute()


def test_register_agent_effect_is_idempotent_and_enforces_session_scope(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("generated-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("expired-session", expires_at=utc_now() - timedelta(seconds=1)),
        _Session(
            "outside-session",
            directory_path="other",
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )

    created = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )
    assert created["success"] is True
    assert created["created"] is True
    assert created["agent_id"] == "support-agent"
    state = repo.read()
    assert state.agents["support-agent"].session_id == "support-session"
    assert state.agents["support-agent"].reports_to_agent_id == "manager"
    revision_after_create = state.revision

    retry = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )
    assert retry["success"] is True
    assert retry["created"] is False
    assert retry["revision"] == revision_after_create

    generated = _register(tmp_path, sessions, session_id="generated-session")
    assert generated["success"] is True
    assert generated["created"] is True
    assert str(generated["agent_id"]).startswith("agent-team-")

    duplicate = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="different-agent",
    )
    assert duplicate["success"] is False
    assert "already bound" in str(duplicate["error"])

    expired = _register(tmp_path, sessions, session_id="expired-session")
    outside = _register(
        tmp_path,
        sessions,
        session_id="outside-session",
        directory_path="root/workflow",
    )
    missing = _register(tmp_path, sessions, session_id="missing-session")
    assert expired["success"] is False and "expired" in str(expired["error"])
    assert outside["success"] is False and "outside workflow directory" in str(outside["error"])
    assert missing["success"] is False and "does not exist" in str(missing["error"])


def test_lifecycle_effects_accept_completed_replays_with_original_revision(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    registration_revision = repo.read().revision

    created = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
        expected_revision=registration_revision,
    )
    assert created["success"] is True
    assert created["created"] is True

    registration_retry = _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
        expected_revision=registration_revision,
    )
    assert registration_retry["success"] is True
    assert registration_retry["created"] is False
    assert registration_retry["revision"] == repo.read().revision

    retirement_revision = repo.read().revision
    retired = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
        expected_revision=retirement_revision,
    )
    assert retired["success"] is True
    assert retired["already_retired"] is False

    sessions.sessions.pop("support-session")
    retirement_retry = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
        expected_revision=retirement_revision,
    )
    assert retirement_retry["success"] is True
    assert retirement_retry["already_retired"] is True
    assert retirement_retry["revision"] == repo.read().revision


def test_restore_agent_rebinds_new_session_resets_runtime_and_preserves_history(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Lifecycle Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )
    sessions = _Sessions(
        _Session("old-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("new-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="old-session",
        agent_id="support-agent",
        project_id="project",
    )["success"]
    admin.update_agent(
        agent_id="support-agent",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=3),
        expected_revision=repo.read().revision,
    )
    admin.create_case(
        case_id="historical-case",
        team_id="team",
        project_id="project",
        title="Completed investigation",
        owner_agent_id="support-agent",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id="historical-memory",
        team_id="team",
        project_id="project",
        agent_id="support-agent",
        scope="agent_private",
        kind="other",
        title="Prior generation",
        summary="Retain durable knowledge across execution sessions.",
        expected_revision=repo.read().revision,
    )
    completed_at = utc_now()

    def _seed_old_runtime_and_dispatch(state):
        state.agent_runtimes["support-agent"] = AgentRuntime(
            agent_id="support-agent",
            wake_reason="old work",
            no_progress_count=3,
            context_clearing_policy=AgentContextClearingPolicy.WORK_LINEAGE,
            context_clearing_idle_grace_seconds=321,
            context_rotation_pending=True,
            context_rotation_requested_by="operator",
            context_rotation_reason="manual",
            last_handoff_summary="Old execution-context handoff.",
            last_thread_id="old-model-thread",
        )
        state.dispatches["old-dispatch"] = TeamDispatch(
            id="old-dispatch",
            agent_id="support-agent",
            session_id="old-session",
            cause_kind="scheduled",
            status=TeamDispatchStatus.COMPLETED,
            completed_at=completed_at,
        )
        return state

    repo.update(_seed_old_runtime_and_dispatch)
    assert _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="old-session",
    )["success"]
    sessions.sessions.pop("old-session")
    restoration_revision = repo.read().revision

    restored = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
        project_id="project",
        expected_revision=restoration_revision,
    )

    assert restored["success"] is True
    assert restored["restored"] is True
    assert restored["already_restored"] is False
    assert restored["previous_session_id"] == "old-session"
    assert restored["session_id"] == "new-session"
    state = repo.read()
    agent = state.agents["support-agent"]
    assert agent.status == AgentStatus.ACTIVE
    assert agent.team_id == "team"
    assert agent.role_id == "support"
    assert agent.session_id == "new-session"
    assert agent.reports_to_agent_id == "manager"
    assert agent.retired_team_id is None
    assert agent.ephemeral_policy.max_concurrent == 3
    runtime = state.agent_runtimes["support-agent"]
    assert runtime.wake_leased_until is None
    assert runtime.active_dispatch_id is None
    assert runtime.wake_reason is None
    assert runtime.no_progress_count == 0
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.WORK_LINEAGE
    assert runtime.context_clearing_idle_grace_seconds == 321
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.context_rotation_pending is False
    assert runtime.context_rotation_requested_by is None
    assert runtime.context_rotation_reason is None
    assert runtime.last_handoff_summary is None
    assert runtime.last_thread_id is None
    assert runtime.work_lineage_thread_ids == {}
    assert state.dispatches["old-dispatch"].session_id == "old-session"
    assert state.cases["historical-case"].owner_agent_id == "support-agent"
    assert state.memories["historical-memory"].agent_id == "support-agent"
    assert set(state.agents["manager"].subordinate_agent_ids) == {"support-agent"}
    assert {
        assignment.project_id
        for assignment in state.project_assignments.values()
        if assignment.agent_id == "support-agent"
    } == {"project"}
    revision_after_restore = state.revision

    retry = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
        project_id="project",
        expected_revision=restoration_revision,
    )
    assert retry["success"] is True
    assert retry["already_restored"] is True
    assert retry["revision"] == revision_after_restore
    receipt = repo.get_idempotency_record(
        tool_name="effect:team:restore-agent",
        caller_identity="workflow-agent-lifecycle",
        scope_kind="agent",
        scope_id="support-agent",
        request_id="new-session",
    )
    assert receipt is not None
    assert receipt.response_payload["previous_session_id"] == "old-session"

    inexact_retry = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="different-old-session",
        project_id="project",
    )
    assert inexact_retry["success"] is False
    assert "idempotency key reused" in str(inexact_retry["error"])
    assert repo.read().revision == revision_after_restore


def test_restore_agent_checks_replay_in_transaction_after_a_competing_restore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("old-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("new-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="old-session",
        agent_id="support-agent",
    )["success"]
    assert _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="old-session",
    )["success"]
    restoration_revision = repo.read().revision
    competing_repo = TeamStateRepository(tmp_path)
    competing_service = TeamWorkflowAgentLifecycleService(
        competing_repo,
        session_lookup=sessions,
        workflow_directory_path="root",
    )
    service = TeamWorkflowAgentLifecycleService(
        repo,
        session_lookup=sessions,
        workflow_directory_path="root",
    )
    original_check_replay = repo.check_idempotency_replay
    original_transactional_update = repo.update_with_command_transaction
    competing_restore_completed = False

    def _complete_competing_restore() -> None:
        nonlocal competing_restore_completed
        if competing_restore_completed:
            return
        competing_restore_completed = True
        result = competing_service.restore_agent(
            agent_id="support-agent",
            new_session_id="new-session",
            expected_old_session_id="old-session",
            expected_team_id="team",
            role_id="support",
            reports_to_agent_id="manager",
            expected_revision=restoration_revision,
        )
        assert result.already_restored is False

    def _race_after_replay_lookup(**kwargs):
        replay = original_check_replay(**kwargs)
        if replay is None:
            _complete_competing_restore()
        return replay

    def _race_before_transaction(mutator, *, expected_revision=None):
        _complete_competing_restore()
        return original_transactional_update(mutator, expected_revision=expected_revision)

    monkeypatch.setattr(repo, "check_idempotency_replay", _race_after_replay_lookup)
    monkeypatch.setattr(repo, "update_with_command_transaction", _race_before_transaction)

    result = service.restore_agent(
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
        expected_team_id="team",
        role_id="support",
        reports_to_agent_id="manager",
        expected_revision=restoration_revision,
    )

    assert competing_restore_completed is True
    assert result.restored is True
    assert result.already_restored is True
    assert result.state.revision == restoration_revision + 1
    assert result.agent.session_id == "new-session"


def test_restore_agent_rejects_archived_retained_team(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    sessions = _Sessions(
        _Session("old-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("new-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="old-session",
        agent_id="support-agent",
    )["success"]
    assert _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="old-session",
    )["success"]
    admin.update_team(
        team_id="team",
        archived=True,
        expected_revision=repo.read().revision,
    )
    revision_before_rejection = repo.read().revision

    rejected = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
        reports_to_agent_id=None,
        expected_revision=revision_before_rejection,
    )

    assert rejected["success"] is False
    assert rejected["error"] == "team team is archived"
    state = repo.read()
    assert state.revision == revision_before_rejection
    assert state.agents["support-agent"].status == AgentStatus.ARCHIVED
    assert state.agents["support-agent"].team_id is None


def test_restore_agent_rejects_invalid_agent_session_scope_and_revision(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("old-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("new-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("bound-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("expired-session", expires_at=utc_now() - timedelta(seconds=1)),
        _Session(
            "outside-session",
            directory_path="other",
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="old-session",
        agent_id="support-agent",
    )["success"]
    assert _register(
        tmp_path,
        sessions,
        session_id="bound-session",
        agent_id="other-agent",
    )["success"]

    non_archived = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
    )
    assert non_archived["success"] is False
    assert "not fully archived" in str(non_archived["error"])

    assert _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="old-session",
    )["success"]
    revision_before_rejections = repo.read().revision

    wrong_old_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="wrong-session",
    )
    bound_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="bound-session",
        expected_old_session_id="old-session",
    )
    expired_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="expired-session",
        expected_old_session_id="old-session",
    )
    outside_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="outside-session",
        expected_old_session_id="old-session",
        directory_path="root/workflow",
    )
    missing_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="missing-session",
        expected_old_session_id="old-session",
    )
    same_session = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="old-session",
        expected_old_session_id="old-session",
    )
    stale_revision = _restore(
        tmp_path,
        sessions,
        agent_id="support-agent",
        new_session_id="new-session",
        expected_old_session_id="old-session",
        expected_revision=revision_before_rejections - 1,
    )

    assert "does not match expected_old_session_id" in str(wrong_old_session["error"])
    assert "already bound to Team agent other-agent" in str(bound_session["error"])
    assert "expired" in str(expired_session["error"])
    assert "outside workflow directory" in str(outside_session["error"])
    assert "does not exist" in str(missing_session["error"])
    assert "must differ" in str(same_session["error"])
    assert stale_revision["success"] is False
    assert stale_revision["expected_revision"] == revision_before_rejections - 1
    assert stale_revision["current_revision"] == revision_before_rejections
    assert repo.read().revision == revision_before_rejections
    assert repo.read().agents["support-agent"].status == AgentStatus.ARCHIVED


def test_lifecycle_effects_use_the_production_session_lookup_contract(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    adapter = AdminRuntimeProviderAdapter(
        SimpleNamespace(
            runtime=SimpleNamespace(
                session_repository=_ListSessionsOnly(
                    _Session(
                        "production-session",
                        directory_path="root/workflow",
                        expires_at=utc_now() + timedelta(hours=1),
                    ),
                    _Session(
                        "replacement-session",
                        directory_path="root/workflow",
                        expires_at=utc_now() + timedelta(hours=1),
                    ),
                )
            )
        )
    )

    registered = _register(
        tmp_path,
        adapter,
        session_id="production-session",
        agent_id="production-agent",
    )
    assert registered["success"] is True, registered
    assert repo.read().agents["production-agent"].session_id == "production-session"

    retired = _retire(
        tmp_path,
        adapter,
        agent_id="production-agent",
        session_id="production-session",
    )
    assert retired["success"] is True
    assert repo.read().agents["production-agent"].status == AgentStatus.ARCHIVED
    # Session cleanup may be eventual; a terminal old record does not prevent a
    # distinct replacement binding.
    assert "production-session" in adapter.read_all_sessions()

    restored = _restore(
        tmp_path,
        adapter,
        agent_id="production-agent",
        new_session_id="replacement-session",
        expected_old_session_id="production-session",
    )
    assert restored["success"] is True
    assert repo.read().agents["production-agent"].session_id == "replacement-session"


def test_register_agent_rejects_non_cleaned_ephemeral_session_binding(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Lifecycle Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="ephemeral-task",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Ephemeral work",
        description="Work already bound to the ephemeral session.",
        created_by_agent_id="manager",
        expected_revision=repo.read().revision,
    )

    def _add_assignment(state):
        state.ephemeral_assignments["ephemeral-assignment"] = EphemeralAssignment(
            id="ephemeral-assignment",
            parent_agent_id="manager",
            project_id="project",
            task_id="ephemeral-task",
            session_id="shared-session",
            title="Ephemeral worker",
            prompt="Complete the ephemeral work.",
            state=EphemeralAssignmentState.REQUESTED,
        )
        return state

    repo.update(_add_assignment)
    sessions = _Sessions(_Session("shared-session", expires_at=utc_now() + timedelta(hours=1)))
    for assignment_state in (
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.RUNNING,
        EphemeralAssignmentState.CLEANUP_PENDING,
    ):

        def _set_assignment_state(state):
            state.ephemeral_assignments["ephemeral-assignment"].state = assignment_state
            return state

        repo.update(_set_assignment_state)
        revision_before_rejection = repo.read().revision
        rejected = _register(
            tmp_path,
            sessions,
            session_id="shared-session",
            agent_id="persistent-agent",
        )
        assert rejected["success"] is False
        assert "non-cleaned ephemeral assignment ephemeral-assignment" in str(rejected["error"])
        assert repo.read().revision == revision_before_rejection
        assert "persistent-agent" not in repo.read().agents

    def _mark_cleaned_up(state):
        state.ephemeral_assignments[
            "ephemeral-assignment"
        ].state = EphemeralAssignmentState.CLEANED_UP
        return state

    repo.update(_mark_cleaned_up)
    registered = _register(
        tmp_path,
        sessions,
        session_id="shared-session",
        agent_id="persistent-agent",
    )
    assert registered["success"] is True


def test_register_agent_rejects_archived_role_before_mutation(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.update_role(
        role_id="support",
        archived=True,
        expected_revision=repo.read().revision,
    )
    sessions = _Sessions(
        _Session("archived-role-session", expires_at=utc_now() + timedelta(hours=1))
    )
    revision_before_rejection = repo.read().revision

    rejected = _register(
        tmp_path,
        sessions,
        session_id="archived-role-session",
        agent_id="archived-role-agent",
    )

    assert rejected["success"] is False
    assert rejected["error"] == "role support is archived"
    assert repo.read().revision == revision_before_rejection
    assert "archived-role-agent" not in repo.read().agents


def test_retire_agent_effect_requires_child_first_cleanup_and_preserves_retry_history(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("discovery-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    assert _register(
        tmp_path,
        sessions,
        session_id="discovery-session",
        agent_id="discovery-agent",
        reports_to_agent_id="support-agent",
    )["success"]

    blocked = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert blocked["success"] is False
    assert any("subordinate" in blocker for blocker in blocked["blockers"])

    retired_child = _retire(
        tmp_path,
        sessions,
        agent_id="discovery-agent",
        session_id="discovery-session",
    )
    assert retired_child["success"] is True
    assert retired_child["retired"] is True
    assert retired_child["already_retired"] is False
    state = repo.read()
    child = state.agents["discovery-agent"]
    assert child.status == AgentStatus.ARCHIVED
    assert child.team_id is None
    assert child.role_id is None
    assert child.reports_to_agent_id is None
    assert child.retired_team_id == "team"
    assert "discovery-agent" not in state.agents["support-agent"].subordinate_agent_ids

    sessions.sessions.pop("discovery-session")
    retry = _retire(
        tmp_path,
        sessions,
        agent_id="discovery-agent",
        session_id="discovery-session",
    )
    assert retry["success"] is True
    assert retry["already_retired"] is True

    wrong_identity = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
        expected_team_id="other-team",
    )
    assert wrong_identity["success"] is False
    assert "does not match expected_team_id" in str(wrong_identity["error"])

    retired_parent = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert retired_parent["success"] is True


def test_retirement_preserves_closed_case_and_memory_history(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="support-case",
        team_id="team",
        title="Customer issue",
        owner_agent_id="support-agent",
        expected_revision=repo.read().revision,
    )

    blocked = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert blocked["success"] is False
    assert blocked["blockers"] == ["owns non-closed case support-case"]

    admin.update_case(
        case_id="support-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_or_update_memory(
        memory_id="support-memory",
        team_id="team",
        agent_id="support-agent",
        scope="agent_private",
        kind="other",
        title="Support history",
        summary="Historical context retained after retirement.",
        expected_revision=repo.read().revision,
    )

    def _record_memory_creator(state):
        state.memories["support-memory"].created_by_agent_id = "support-agent"
        return state

    repo.update(_record_memory_creator)
    retired = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )

    assert retired["success"] is True
    state = repo.read()
    assert state.agents["support-agent"].retired_team_id == "team"
    assert state.cases["support-case"].owner_agent_id == "support-agent"
    assert state.memories["support-memory"].agent_id == "support-agent"
    assert state.memories["support-memory"].created_by_agent_id == "support-agent"


def test_retirement_blocks_governance_management_and_active_dispatches(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    admin = TeamAdminService(repo)
    admin.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="support-agent",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    managed_group = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert managed_group["success"] is False
    assert managed_group["blockers"] == ["manager of governance group portfolio"]

    admin.update_governance_group(
        governance_group_id="portfolio",
        archived=True,
        expected_revision=repo.read().revision,
    )

    def _start_governance_dispatch(state):
        state.governance_runtimes["portfolio"] = GovernanceRuntime(
            governance_group_id="portfolio",
            active_dispatch_id="dispatch-governance",
        )
        return state

    repo.update(_start_governance_dispatch)
    active_dispatch = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert active_dispatch["success"] is False
    assert active_dispatch["blockers"] == [
        "active governance dispatch dispatch-governance for group portfolio"
    ]

    def _finish_governance_dispatch(state):
        state.governance_runtimes["portfolio"].active_dispatch_id = None
        return state

    repo.update(_finish_governance_dispatch)
    retired = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert retired["success"] is True


def test_retirement_preserves_rejected_task_ownership(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    admin = TeamAdminService(repo)
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Lifecycle Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="rejected-task",
        owner_agent_id="support-agent",
        project_id="project",
        priority=1,
        title="Rejected work",
        description="Retain the historical owner after rejection.",
        created_by_agent_id="manager",
        expected_revision=repo.read().revision,
    )

    def _reject_task(state):
        state.tasks["rejected-task"].state = TeamTaskState.REJECTED
        return state

    repo.update(_reject_task)
    retired = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )

    assert retired["success"] is True
    state = repo.read()
    assert state.tasks["rejected-task"].owner_agent_id == "support-agent"
    assert state.agents["support-agent"].retired_team_id == "team"


def test_retirement_blocks_active_release_and_preserves_terminal_release_owner(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    admin = TeamAdminService(repo)
    admin.update_role(
        role_id="support",
        permissions=[RolePermission.REQUEST_RELEASE],
        expected_revision=repo.read().revision,
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Lifecycle Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=repo.read().revision,
    )

    def _add_release(state):
        state.release_packages["release"] = ReleasePackage(
            id="release",
            project_id="project",
            owner_agent_id="support-agent",
            policy_version=2,
        )
        return state

    repo.update(_add_release)
    active_release = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )
    assert active_release["success"] is False
    assert active_release["blockers"] == ["owns non-terminal release package release"]

    def _accept_release(state):
        state.release_packages["release"].state = TeamRecordState.ACCEPTED
        return state

    repo.update(_accept_release)
    retired = _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )

    assert retired["success"] is True
    state = repo.read()
    assert state.release_packages["release"].owner_agent_id == "support-agent"
    assert state.agents["support-agent"].retired_team_id == "team"


def test_session_routing_ignores_retired_persistent_agent(tmp_path: Path) -> None:
    _seed_team(tmp_path)
    sessions = _Sessions(_Session("support-session", expires_at=utc_now() + timedelta(hours=1)))
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    assert _retire(
        tmp_path,
        sessions,
        agent_id="support-agent",
        session_id="support-session",
    )["success"]

    routing = TeamSessionRoutingQueryProvider()
    routing.initialize(
        TeamSessionRoutingQueryDefinition(
            id="route-retired",
            display_name="Route retired session",
            type="query:team:session-routing",
            session_id="support-session",
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    result = routing.execute()

    assert result["success"] is True
    assert result["kind"] == "ignored"
    assert result["persistent"] is False
    assert result["ignored"] is True
    assert result["reason"] == "agent is archived or retired from its team"


def test_case_close_event_is_outboxed_once_and_not_for_initial_or_resolved_cases(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="open-case",
        team_id="team",
        title="Close through workflow",
        description="The customer confirmed that the recovery worked.",
        metadata={"source_thread": "thread-42"},
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="open-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )

    records = repo.list_outbox_records(intent_type="team.case.closed")
    assert len(records) == 1
    event = records[0].payload["payload"]
    assert event["case_id"] == "open-case"
    assert event["team_id"] == "team"
    assert event["previous_status"] == "open"
    assert event["case"] | {
        "created_at": None,
        "updated_at": None,
        "closed_at": None,
    } == {
        "id": "open-case",
        "team_id": "team",
        "project_id": None,
        "title": "Close through workflow",
        "description": "The customer confirmed that the recovery worked.",
        "status": "closed",
        "priority": "normal",
        "owner_agent_id": None,
        "source": None,
        "source_ref": None,
        "tags": [],
        "metadata": {"source_thread": "thread-42"},
        "task_ids": [],
        "source_evidence_snapshot_ids": [],
        "created_at": None,
        "updated_at": None,
        "closed_at": None,
    }
    assert event["case"]["closed_at"].replace("Z", "+00:00") == event["closed_at"]
    assert records[0].idempotency_key

    admin.update_case(
        case_id="open-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_case(
        case_id="initially-closed",
        team_id="team",
        title="No event on initial close",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )
    admin.create_case(
        case_id="resolved-case",
        team_id="team",
        title="No event on resolve",
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="resolved-case",
        status=TeamCaseStatus.RESOLVED,
        expected_revision=repo.read().revision,
    )
    assert len(repo.list_outbox_records(intent_type="team.case.closed")) == 1


def test_agent_case_update_uses_the_same_transactional_close_event(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="agent-case",
        team_id="team",
        title="Agent closure",
        owner_agent_id="manager",
        expected_revision=repo.read().revision,
    )

    state, result = TeamAgentActionService(repo).apply_agent_action(
        action={
            "agent_id": "manager",
            "case_updates": [{"case_id": "agent-case", "status": "closed"}],
        },
        expected_revision=repo.read().revision,
    )

    assert result["success"] is True
    assert state.cases["agent-case"].status == TeamCaseStatus.CLOSED
    records = repo.list_outbox_records(intent_type="team.case.closed")
    assert len(records) == 1
    assert records[0].payload["payload"]["case_id"] == "agent-case"


def test_case_closed_trigger_supports_child_first_retirement_then_session_destroy(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    sessions = _Sessions(
        _Session("support-session", expires_at=utc_now() + timedelta(hours=1)),
        _Session("discovery-session", expires_at=utc_now() + timedelta(hours=1)),
    )
    assert _register(
        tmp_path,
        sessions,
        session_id="support-session",
        agent_id="support-agent",
    )["success"]
    assert _register(
        tmp_path,
        sessions,
        session_id="discovery-session",
        agent_id="discovery-agent",
        reports_to_agent_id="support-agent",
    )["success"]

    admin = TeamAdminService(repo)
    admin.create_case(
        case_id="cleanup-case",
        team_id="team",
        title="Workflow cleanup",
        expected_revision=repo.read().revision,
    )
    admin.update_case(
        case_id="cleanup-case",
        status=TeamCaseStatus.CLOSED,
        expected_revision=repo.read().revision,
    )

    worker_config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "case-closed-cleanup",
                    "display_name": "Case closed cleanup",
                    "type": "trigger:event",
                    "event_type": "team.case.closed",
                }
            ],
        }
    )
    trigger_worker = TriggerOrchestratorWorker(
        config=GatewayConfig(runtime_dir=tmp_path),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    assert trigger_worker.run_once().events_published == 0

    publisher = TeamPublishOutboxEventsEffectProvider()
    publisher.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish-case-closure",
            display_name="Publish case closure",
            type="effect:team:publish-outbox-events",
            intent_types=["team.case.closed"],
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    published = publisher.execute()
    assert published["success"] is True
    assert published["published_count"] == 1

    assert trigger_worker.run_once().events_published == 1

    destroying_admin = _DestroyingAdminService(sessions)
    for agent_id, session_id in (
        ("discovery-agent", "discovery-session"),
        ("support-agent", "support-session"),
    ):
        retired = _retire(
            tmp_path,
            sessions,
            agent_id=agent_id,
            session_id=session_id,
        )
        assert retired["success"] is True
        assert sessions.get_session(session_id) is not None

        destroy = AgentDestroyEffectProvider()
        destroy.initialize(
            AgentDestroyEffectDefinition(
                id=f"destroy-{agent_id}",
                display_name="Destroy retired core session",
                type="effect:agent:destroy",
                session_id=session_id,
                execution_backend="container_worker_vm",
            ),
            EffectRuntimeContext(
                resolved_config={"runtime_dir": str(tmp_path)},
                admin_service=destroying_admin,
            ),
        )
        assert destroy.execute()["success"] is True

    assert destroying_admin.destroyed_session_ids == [
        "discovery-session",
        "support-session",
    ]
    state = repo.read()
    assert state.agents["discovery-agent"].status == AgentStatus.ARCHIVED
    assert state.agents["support-agent"].status == AgentStatus.ARCHIVED


def test_lifecycle_effects_are_registered_with_output_contracts() -> None:
    registration = register_providers()

    assert (
        registration.effect_providers["effect:team:register-agent"]
        is TeamRegisterAgentEffectProvider
    )
    assert (
        registration.effect_providers["effect:team:restore-agent"]
        is TeamRestoreAgentEffectProvider
    )
    assert (
        registration.effect_providers["effect:team:retire-agent"] is TeamRetireAgentEffectProvider
    )
