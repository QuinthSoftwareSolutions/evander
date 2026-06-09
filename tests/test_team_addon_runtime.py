from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from truman_team_addon.models import EphemeralAgentPolicy, ProjectGoal, TeamTaskState
from truman_team_addon.provider_schemas import (
    TeamAgentActionArtifactQueryDefinition,
    TeamApplyAgentActionEffectDefinition,
    TeamApplyEphemeralActionEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamWakeDueAgentsEffectDefinition,
)
from truman_team_addon.providers import (
    TeamAgentActionArtifactQueryProvider,
    TeamApplyAgentActionEffectProvider,
    TeamApplyEphemeralActionEffectProvider,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamSessionRoutingQueryProvider,
    TeamWakeDueAgentsEffectProvider,
)
from truman_team_addon.repository import TeamStateRepository
from truman_team_addon.workflow_packs import team_runtime_v1


class FakeResumer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def resume_agent(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"thread_id": "thread-1"}


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
    assert "team-action.json" in str(resumer.calls[0]["prompt"])
    assert metadata_updater.calls[0]["metadata"]["team_addon"]["agent_id"] == "backend"


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
    assert ("team-ephemeral-requested", "team-create-ephemeral-agent") in edges
    assert ("team-read-ephemeral-action", "team-apply-ephemeral-action") in edges
    assert ("team-destroy-ephemeral-agent", "team-mark-ephemeral-cleaned-up") in edges
