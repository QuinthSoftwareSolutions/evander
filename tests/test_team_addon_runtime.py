from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from truman_team_addon.models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    Deliverable,
    EphemeralAgentPolicy,
    EphemeralAssignment,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGoal,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamState,
    TeamTaskState,
)
from truman_team_addon.provider_schemas import (
    TeamAgentActionArtifactQueryDefinition,
    TeamApplyAgentActionEffectDefinition,
    TeamApplyEphemeralActionEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamStateQueryDefinition,
    TeamWakeDueAgentsEffectDefinition,
)
from truman_team_addon.providers import (
    TeamAgentActionArtifactQueryProvider,
    TeamApplyAgentActionEffectProvider,
    TeamApplyEphemeralActionEffectProvider,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamSessionRoutingQueryProvider,
    TeamStateQueryProvider,
    TeamWakeDueAgentsEffectProvider,
)
from truman_team_addon.repository import TeamStateRepository
from truman_team_addon.workflow_packs import team_runtime_v1


class FakeResumer:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or {"thread_id": "thread-1"}

    def resume_agent(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return dict(self.result)


class FakeMetadataUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"success": True}


class FakeArtifactReader:
    def __init__(self, content: str) -> None:
        self.content = content

    def read_session_artifact_text(self, **_: object) -> dict[str, object]:
        return {"content": self.content}


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
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=max_ephemeral),
        expected_revision=2,
    )
    repo.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=3,
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


def test_wake_due_agents_provider_prompts_and_mirrors_metadata(tmp_path: Path) -> None:
    _seed_team(tmp_path)
    resumer = FakeResumer()
    metadata_updater = FakeMetadataUpdater()
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
            session_metadata_updater=metadata_updater,
        ),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["leased_count"] == 1
    assert resumer.calls[0]["target"] == "session-backend"
    assert resumer.calls[0]["dispatch_id"] == result["dispatches"][0]["dispatch_id"]
    prompt = str(resumer.calls[0]["prompt"])
    assert "team-action.json" in prompt
    for field_name in (
        "task_creations",
        "status_requests",
        "operator_escalations",
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
    assert "do not start tasks" in prompt.lower()
    assert "creating child tasks via task_creations with parent_task_id" in prompt
    assert metadata_updater.calls[0]["metadata"]["team_addon"]["agent_id"] == "backend"


def test_wake_due_agents_provider_releases_lease_when_resume_reports_failure(
    tmp_path: Path,
) -> None:
    _seed_team(tmp_path)
    resumer = FakeResumer({"success": False, "message": "vm not ready"})
    metadata_updater = FakeMetadataUpdater()
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
            session_metadata_updater=metadata_updater,
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
    assert metadata_updater.calls == []


def test_action_artifact_query_and_apply_provider(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _state, leases = repo.lease_due_agents(now=datetime(2026, 1, 1, tzinfo=UTC))
    dispatch_id = leases[0]["dispatch_id"]
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
    reread = TeamStateRepository(tmp_path).read()
    assert len(reread.deliverables) == 1
    assert reread.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW


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
    repo.store.write_object(_multi_team_state().model_dump(mode="json"))
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
    pack = team_runtime_v1()["config"]
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
    pack = team_runtime_v1()["config"]
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
    pack = team_runtime_v1()["config"]
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

    # review -> promote-agent-branch -> record-git-promotion
    promote = effects["team-git-promote-review-branch"]
    assert promote["type"] == "effect:git:promote-agent-branch"
    assert promote["agent_branch"] == "${{team-git-promotion-requested.payload.source_branch}}"
    assert promote["review_branch"] == "${{team-git-promotion-requested.payload.target_branch}}"
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
    pack = team_runtime_v1()["config"]
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
        agents={
            "alpha-manager": PersistentAgent(
                id="alpha-manager",
                team_id="alpha-team",
                role="manager",
                session_id="session-alpha-manager",
                subordinate_agent_ids=["alpha-lead"],
            ),
            "alpha-lead": PersistentAgent(
                id="alpha-lead",
                team_id="alpha-team",
                project_id="alpha-project",
                role="lead",
                session_id="session-alpha-lead",
                reports_to_agent_id="alpha-manager",
                subordinate_agent_ids=["alpha-dev", "alpha-reviewer"],
            ),
            "alpha-dev": PersistentAgent(
                id="alpha-dev",
                team_id="alpha-team",
                project_id="alpha-project",
                role="developer",
                session_id="session-alpha-dev",
                reports_to_agent_id="alpha-lead",
            ),
            "alpha-reviewer": PersistentAgent(
                id="alpha-reviewer",
                team_id="alpha-team",
                project_id="alpha-project",
                role="reviewer",
                session_id="session-alpha-reviewer",
                reports_to_agent_id="alpha-lead",
            ),
            "beta-manager": PersistentAgent(
                id="beta-manager",
                team_id="beta-team",
                role="manager",
                session_id="session-beta-manager",
                subordinate_agent_ids=["beta-lead"],
            ),
            "beta-lead": PersistentAgent(
                id="beta-lead",
                team_id="beta-team",
                project_id="beta-project",
                role="lead",
                session_id="session-beta-lead",
                reports_to_agent_id="beta-manager",
                subordinate_agent_ids=["beta-dev", "beta-reviewer"],
            ),
            "beta-dev": PersistentAgent(
                id="beta-dev",
                team_id="beta-team",
                project_id="beta-project",
                role="developer",
                session_id="session-beta-dev",
                reports_to_agent_id="beta-lead",
            ),
            "beta-reviewer": PersistentAgent(
                id="beta-reviewer",
                team_id="beta-team",
                project_id="beta-project",
                role="reviewer",
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
