from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext

from evander_team_addon.models import (
    AgentProjectAssignment,
    Deliverable,
    DeliverableGitRefs,
    ProjectGitConfig,
    ProjectGoal,
    ReviewBlockKind,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.provider_schemas import TeamWakeDueAgentsEffectDefinition
from evander_team_addon.providers import (
    TeamWakeDueAgentsEffectProvider,
    _record_dependency_git_preparation_blocker,
)
from evander_team_addon.services.domain.blockers import resolve_blocker
from evander_team_addon.services.domain.preparation_failures import (
    TASK_PREPARATION_CONTRACT_VERSION,
    task_preparation_generation,
)
from evander_team_addon.services.domain.review import retry_blocked_review
from evander_team_addon.services.runtime_service import TeamRuntimeService

from test_team_addon_runtime import (
    FakeFileCopier,
    FakeRepoCloner,
    FakeResumer,
    FakeSessionStore,
    FakeVmExecutor,
    _add_luke_reviewer,
    _seed_team,
    _team_admin_service,
    _team_agent_action_service,
)


def test_task_preparation_failure_reuses_incident_and_unlocks_on_generation_change(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    lease = leases[0]
    TeamRuntimeService(repo).fail_agent_dispatch(
        agent_id="backend",
        dispatch_id=lease["dispatch_id"],
        error="accepted dependency branch moved",
    )

    first = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="accepted dependency branch moved",
    )
    first_blocker = next(
        blocker
        for blocker in first.blockers.values()
        if blocker.reason_code == "dependency_git_preparation_failed"
    )
    repeated = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="accepted dependency branch moved",
    )
    matching = [
        blocker
        for blocker in repeated.blockers.values()
        if blocker.reason_code == "dependency_git_preparation_failed"
    ]
    assert [blocker.id for blocker in matching] == [first_blocker.id]

    _state, suppressed = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )
    assert suppressed == []

    def _change_task_generation(state):
        state.tasks[
            "task"
        ].description = "Use the replacement dependency candidate recorded by the lead."
        return state

    repo.update(_change_task_generation)
    changed, resumed = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )
    assert len(resumed) == 1
    assert changed.blockers[first_blocker.id].state == TeamBlockerState.RESOLVED


def test_preparation_contract_upgrade_retries_legacy_failure_immediately(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    runtime = TeamRuntimeService(repo)
    _state, leases = runtime.lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    lease = leases[0]
    runtime.fail_agent_dispatch(
        agent_id="backend",
        dispatch_id=lease["dispatch_id"],
        error="task worktree base ref does not resolve",
    )
    failed = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="task worktree base ref does not resolve",
    )
    blocker = next(iter(failed.blockers.values()))
    cause = blocker.source_id.rsplit(":", 1)[-1]

    def _mark_as_prior_contract(state):
        state.blockers[blocker.id].source_id = (
            f"execution-preparation:task:task:contract-"
            f"{TASK_PREPARATION_CONTRACT_VERSION - 1}:{cause}"
        )
        state.agent_runtimes["backend"].next_wake_at = datetime(
            2099,
            1,
            1,
            tzinfo=UTC,
        )
        return state

    repo.update(_mark_as_prior_contract)
    retried_state, retry_leases = runtime.lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )

    assert len(retry_leases) == 1
    assert retry_leases[0]["dispatch_id"] != lease["dispatch_id"]
    assert retried_state.blockers[blocker.id].state == TeamBlockerState.RESOLVED


def test_task_preparation_generation_ignores_scheduling_bookkeeping(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    lease = leases[0]
    TeamRuntimeService(repo).fail_agent_dispatch(
        agent_id="backend",
        dispatch_id=lease["dispatch_id"],
        error="worktree unavailable",
    )
    state = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="worktree unavailable",
    )
    blocker = next(iter(state.blockers.values()))

    def _touch_scheduling_bookkeeping(state):
        state.tasks["task"].updated_at += timedelta(microseconds=1)
        state.tasks["task"].priority += 1
        return state

    repo.update(_touch_scheduling_bookkeeping)
    unchanged, repeated = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )

    assert repeated == []
    assert unchanged.blockers[blocker.id].state == TeamBlockerState.OPEN


def test_project_git_change_unlocks_task_preparation_generation(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    lease = leases[0]
    TeamRuntimeService(repo).fail_agent_dispatch(
        agent_id="backend",
        dispatch_id=lease["dispatch_id"],
        error="worktree unavailable",
    )
    state = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="worktree unavailable",
    )
    blocker = next(iter(state.blockers.values()))

    def _change_project_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="replacement-base",
        )
        return state

    repo.update(_change_project_git)
    changed, retried = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )

    assert len(retried) == 1
    assert changed.blockers[blocker.id].state == TeamBlockerState.RESOLVED


def test_dependency_and_candidate_changes_advance_preparation_generation(
    tmp_path,
) -> None:
    state = _seed_team(tmp_path).read()
    dependency = state.tasks["task"].model_copy(
        update={
            "id": "dependency",
            "state": TeamTaskState.FINISHED,
            "title": "Prepare dependency",
            "description": "Original dependency instructions.",
            "dependency_task_ids": [],
            "deliverable_ids": ["dependency-candidate"],
        },
        deep=True,
    )
    state.tasks[dependency.id] = dependency
    state.tasks["task"].dependency_task_ids = [dependency.id]
    state.deliverables["dependency-candidate"] = Deliverable(
        id="dependency-candidate",
        task_id=dependency.id,
        producer_agent_id="backend",
        summary="Pinned dependency candidate.",
        state=TeamRecordState.ACCEPTED,
        git_refs=DeliverableGitRefs(
            agent_branch="agents/session-backend/dependency",
            commit_sha="abc123",
            submitted_commit_sha="abc123",
        ),
    )
    original = task_preparation_generation(state, "task")

    dependency.description = "Replacement dependency instructions."
    dependency_changed = task_preparation_generation(state, "task")
    state.deliverables["dependency-candidate"].git_refs.commit_sha = "def456"
    candidate_changed = task_preparation_generation(state, "task")

    assert dependency_changed != original
    assert candidate_changed != dependency_changed


def test_project_repo_preparation_failure_blocks_repeat_but_not_recovery_wake(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        return state

    repo.update(_set_git)
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="backend",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(success=False, message="repo missing"),
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )

    failed = provider.execute()

    assert failed["success"] is False
    assert resumer.calls == []
    assert len(failed["preparation_blocker_ids"]) == 1
    state = repo.read()
    blocker = state.blockers[failed["preparation_blocker_ids"][0]]
    assert blocker.reason_code == "task_execution_preparation_failed"
    assert blocker.task_id == "task"
    assert state.agent_runtimes["backend"].no_progress_count == 0

    repeated = provider.execute()
    assert repeated["leased_count"] == 0
    assert len(repo.read().blockers) == 1

    lead_resumer = FakeResumer()
    lead_session_store = FakeSessionStore()
    lead_session_store.sessions["session-lead"] = SimpleNamespace(
        session_id="session-lead",
        vm_id="vm-lead",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    recovery_provider = TeamWakeDueAgentsEffectProvider()
    recovery_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-recovery",
            display_name="Wake recovery owner",
            type="effect:team:wake-due-agents",
            agent_id="lead",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=lead_resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(success=False, message="repo missing"),
            vm_executor=FakeVmExecutor(),
            session_lookup=lead_session_store,
            session_metadata_updater=lead_session_store,
        ),
    )
    recovery = recovery_provider.execute()
    assert recovery["success"] is True
    assert recovery["leased_count"] == 1
    assert recovery["leases"][0]["dispatch"]["project_ids"] == []
    assert len(lead_resumer.calls) == 1


def test_preparation_recovery_wake_isolates_ordinary_project_work(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="lead-task",
        owner_agent_id="lead",
        project_id="project",
        priority=2,
        title="Lead work",
        description="Ordinary project work in the unavailable repository.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    def _set_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        return state

    repo.update(_set_git)
    failed_cloner = FakeRepoCloner(success=False, message="repo missing")

    backend_provider = TeamWakeDueAgentsEffectProvider()
    backend_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-backend",
            display_name="Wake backend",
            type="effect:team:wake-due-agents",
            agent_id="backend",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=failed_cloner,
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )
    failed = backend_provider.execute()
    assert failed["success"] is False

    lead_resumer = FakeResumer()
    lead_session_store = FakeSessionStore()
    lead_session_store.sessions["session-lead"] = SimpleNamespace(
        session_id="session-lead",
        vm_id="vm-lead",
        vm_provider="libvirt",
        directory_path="root",
        metadata={},
    )
    recovery_cloner = FakeRepoCloner(success=False, message="repo missing")
    recovery_provider = TeamWakeDueAgentsEffectProvider()
    recovery_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-recovery",
            display_name="Wake recovery owner",
            type="effect:team:wake-due-agents",
            agent_id="lead",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=lead_resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=recovery_cloner,
            vm_executor=FakeVmExecutor(),
            session_lookup=lead_session_store,
            session_metadata_updater=lead_session_store,
        ),
    )

    recovery = recovery_provider.execute()

    assert recovery["success"] is True
    assert recovery["leased_count"] == 1
    assert recovery["leases"][0]["dispatch"]["project_ids"] == []
    assert recovery["leases"][0]["dispatch"]["task_ids"] == []
    assert recovery_cloner.calls == []
    assert len(lead_resumer.calls) == 1
    assert {blocker.reason_code for blocker in repo.read().blockers.values()} == {
        "task_execution_preparation_failed"
    }


def test_delegated_preparation_recovery_does_not_retry_affected_task_repository(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        return state

    repo.update(_set_git)
    failed_cloner = FakeRepoCloner(success=False, message="repo missing")
    initial_provider = TeamWakeDueAgentsEffectProvider()
    initial_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-initial",
            display_name="Wake backend",
            type="effect:team:wake-due-agents",
            agent_id="backend",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=failed_cloner,
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )
    failed = initial_provider.execute()
    blocker_id = failed["preparation_blocker_ids"][0]

    def _delegate_to_task_owner(state):
        blocker = state.blockers[blocker_id]
        blocker.route = TeamBlockerRoute.DELEGATED
        blocker.delegate_agent_id = "backend"
        blocker.active_dispatch_id = None
        blocker.follow_up_at = None
        return state

    repo.update(_delegate_to_task_owner)
    recovery_resumer = FakeResumer()
    recovery_cloner = FakeRepoCloner(success=False, message="repo still missing")
    recovery_provider = TeamWakeDueAgentsEffectProvider()
    recovery_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-recovery",
            display_name="Wake delegated recovery owner",
            type="effect:team:wake-due-agents",
            agent_id="backend",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=recovery_resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=recovery_cloner,
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )

    recovery = recovery_provider.execute()

    assert recovery["success"] is True
    assert recovery["leased_count"] == 1
    assert recovery_cloner.calls == []
    assert len(recovery_resumer.calls) == 1
    dispatch = recovery["leases"][0]["dispatch"]
    assert dispatch["task_ids"] == []
    assert dispatch["project_ids"] == []
    assert dispatch["managed_repo_ids"] == []
    assert dispatch["blocker_ids"] == [blocker_id]


def test_future_delegated_preparation_blocker_does_not_contaminate_other_work(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)

    def _set_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        return state

    repo.update(_set_git)
    initial_provider = TeamWakeDueAgentsEffectProvider()
    initial_provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-initial",
            display_name="Wake backend",
            type="effect:team:wake-due-agents",
            agent_id="backend",
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=FakeResumer(),
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=FakeRepoCloner(success=False, message="repo missing"),
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )
    failed = initial_provider.execute()
    blocker_id = failed["preparation_blocker_ids"][0]

    admin = _team_admin_service(repo)
    admin.create_project(
        project_id="other-project",
        team_id="team",
        name="Other project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="other-goal", description="Ship unrelated work")],
        expected_revision=repo.read().revision,
    )

    def _delegate_and_assign_other_project(state):
        state.project_assignments["backend-other-project"] = AgentProjectAssignment(
            id="backend-other-project",
            team_id="team",
            project_id="other-project",
            agent_id="backend",
        )
        blocker = state.blockers[blocker_id]
        blocker.route = TeamBlockerRoute.DELEGATED
        blocker.delegate_agent_id = "backend"
        blocker.active_dispatch_id = None
        blocker.follow_up_at = blocker.updated_at + timedelta(days=1)
        return state

    repo.update(_delegate_and_assign_other_project)
    admin.create_or_reprioritize_task(
        task_id="other-task",
        owner_agent_id="backend",
        project_id="other-project",
        priority=1,
        title="Unrelated task",
        description="This work does not use the unavailable repository.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    resumer = FakeResumer()
    cloner = FakeRepoCloner(success=False, message="broken repo was retried")
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake-unrelated",
            display_name="Wake unrelated work",
            type="effect:team:wake-due-agents",
            agent_id="backend",
            respect_wake_interval=False,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(),
            vm_repo_cloner=cloner,
            vm_executor=FakeVmExecutor(),
            session_lookup=FakeSessionStore(),
        ),
    )

    dispatched = provider.execute()

    assert dispatched["success"] is True
    assert dispatched["leased_count"] == 1
    assert cloner.calls == []
    assert len(resumer.calls) == 1
    dispatch = dispatched["leases"][0]["dispatch"]
    assert dispatch["task_ids"] == ["other-task"]
    assert dispatch["project_ids"] == ["other-project"]
    assert dispatch["managed_repo_ids"] == []
    assert dispatch["blocker_ids"] == [blocker_id]


def test_review_artifact_preparation_blocks_before_inference_and_requires_retry(
    tmp_path,
    monkeypatch,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)
    artifact_ref = "artifact-space:/teams/team/projects/project/deliverables/missing"
    _state, submission = _team_agent_action_service(repo).apply_agent_action(
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
    assert submission["success"] is True

    _state, requested = _team_agent_action_service(repo).apply_agent_action(
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
    assert requested["success"] is True

    def _unexpected_import(
        self: AgentExecEffectProvider,
        *,
        resolved_config=None,
    ):
        del self, resolved_config
        raise AssertionError("missing artifact-space version should not import")

    monkeypatch.setattr(AgentExecEffectProvider, "execute", _unexpected_import)
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

    failed = provider.execute()

    assert failed["success"] is False
    assert resumer.calls == []
    state = repo.read()
    review = state.review_requests["review-luke"]
    assert review.state == TeamRecordState.BLOCKED
    assert review.block_kind == ReviewBlockKind.SYSTEM
    assert state.agent_runtimes["luke"].no_progress_count == 0
    blocker = next(blocker for blocker in state.blockers.values() if blocker.source_id == review.id)
    assert blocker.reason_code == "review_preparation_failed"

    repeated = provider.execute()
    assert repeated["leased_count"] == 0
    assert resumer.calls == []

    retry_result: dict = {"events": []}

    def _retry(state):
        retry_blocked_review(
            state,
            actor=state.agents["lead"],
            review_request_id=review.id,
            reason="Artifact publication was repaired.",
            reviewer_agent_id=None,
            result=retry_result,
        )
        return state

    repo.update(_retry)
    retried = repo.read()
    replacement_id = retried.review_requests[review.id].superseded_by_review_request_id
    assert replacement_id is not None
    assert retried.review_requests[replacement_id].state == TeamRecordState.PENDING
    assert retried.blockers[blocker.id].state == TeamBlockerState.RESOLVED


def test_review_repository_preparation_failure_blocks_exact_review_attempt(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _add_luke_reviewer(repo)

    def _set_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
        )
        return state

    repo.update(_set_git)
    _state, submission = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable-1",
                    "task_id": "task",
                    "summary": "Backend work is ready.",
                }
            ],
        },
    )
    assert submission["success"] is True

    def _pin_deliverable_candidate(state):
        state.deliverables["deliverable-1"].git_refs = DeliverableGitRefs(
            commit_sha="a" * 40,
            submitted_commit_sha="a" * 40,
        )
        return state

    repo.update(_pin_deliverable_candidate)
    _state, requested = _team_agent_action_service(repo).apply_agent_action(
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
    assert requested["success"] is True

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
            vm_repo_cloner=FakeRepoCloner(success=False, message="repo missing"),
            vm_executor=FakeVmExecutor(),
            session_lookup=session_store,
            session_metadata_updater=session_store,
        ),
    )

    failed = provider.execute()

    assert failed["success"] is False
    assert resumer.calls == []
    state = repo.read()
    assert state.review_requests["review-luke"].state == TeamRecordState.BLOCKED
    blockers = [
        blocker for blocker in state.blockers.values() if blocker.source_id == "review-luke"
    ]
    assert len(blockers) == 1
    assert blockers[0].reason_code == "review_preparation_failed"
    assert failed["preparation_blocker_ids"] == [blockers[0].id]


def test_explicit_resolution_allows_same_task_preparation_generation_to_retry(
    tmp_path,
) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
    )
    lease = leases[0]
    TeamRuntimeService(repo).fail_agent_dispatch(
        agent_id="backend",
        dispatch_id=lease["dispatch_id"],
        error="worktree unavailable",
    )
    state = _record_dependency_git_preparation_blocker(
        repo,
        lease=lease,
        task_id="task",
        error="worktree unavailable",
    )
    blocker = next(iter(state.blockers.values()))

    def _resolve(state):
        resolve_blocker(
            state,
            blocker=state.blockers[blocker.id],
            actor_agent_id="lead",
            dispatch_id=None,
            note="Operator repaired the task worktree.",
        )
        return state

    repo.update(_resolve)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        defer_activation=True,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
